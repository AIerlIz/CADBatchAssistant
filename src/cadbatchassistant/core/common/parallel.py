"""文件级并行处理工具（改字/填表/目录三个助手共用）。

核心是 Executor 抽象：对相互独立的文件处理任务（readfile → 处理 → 写出）
执行批量映射（ezdxf 解析为 CPU 密集型，GIL 下多线程无效，通常用多进程）。

设计约束：
- Windows 下 multiprocessing 默认 spawn：worker 必须是模块级顶层函数，
  参数（item）必须可 pickle（dict / dataclass / partial 均可）
- 文件数少（<4）或单核时自动回退串行（AutoExecutor），避免进程启动开销
- 单文件失败不中断整批：对应结果位置返回 TaskFailed(error)
- 取消：is_cancelled() 置位后停止收集新结果并取消未开始的任务
  （运行中的任务等其完成，与串行「当前文件处理完后停止」语义一致）
- on_done(result, index, item)：每完成一个任务即回调（实时进度/日志）；
  并行时完成顺序与提交顺序不同，index 用于定位

可切换性：业务代码默认用 map_files（auto 模式）；需要固定执行方式时
经 create_executor("serial"|"process"|"thread") 获取实现并调用 .map()，
例如调试时切串行、或按部署环境切换多进程/多线程。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import (
    ALL_COMPLETED,
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
)
from concurrent.futures import wait as _wait
from typing import Protocol


class TaskFailed:
    """单文件任务失败的包装（携带原始异常）。

    调用方用 isinstance(result, TaskFailed) 判定失败，读取 .error。
    """

    def __init__(self, error: Exception) -> None:
        self.error = error


def _run_serial(worker, items, is_cancelled, on_done) -> list:
    """串行执行（文件数少 / 单核时的回退路径）。"""
    results: list = []
    for i, item in enumerate(items):
        if is_cancelled is not None and is_cancelled():
            break
        try:
            result = worker(item)
        except Exception as ex:  # noqa: BLE001 - 单文件容错，不中断整批
            result = TaskFailed(ex)
        results.append(result)
        if on_done is not None:
            on_done(result, i, item)
    return results


def _run_pool(pool_cls, worker, items, is_cancelled, on_done, max_workers) -> list:
    """线程/进程池通用执行：结果按提交顺序返回，支持取消与实时回调。"""
    n = len(items)
    results: list = [None] * n
    ex = pool_cls(max_workers=max_workers)
    try:
        fut_map = {ex.submit(worker, item): i for i, item in enumerate(items)}
        # 用 wait(timeout) 轮询而非 as_completed 阻塞：每 0.2s 检查一次取消，
        # 保证「停止」按钮在任务尚未完成时也能及时响应。
        pending = set(fut_map)
        while pending:
            if is_cancelled is not None and is_cancelled():
                break
            done, pending = _wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in done:
                i = fut_map[fut]
                try:
                    result = fut.result()
                except Exception as ex2:  # noqa: BLE001 - 单文件容错
                    result = TaskFailed(ex2)
                results[i] = result
                if on_done is not None:
                    on_done(result, i, items[i])
    finally:
        # 取消：未开始的任务取消，运行中的任务等待其完成（与串行
        # 「当前文件处理完后停止」语义一致）。不能用 shutdown(wait=False)：
        # 运行中的子进程会继续写临时目录，调用方随后 rmtree 因句柄占用
        # 静默失败 → 残留 %TEMP% 目录 + 子进程后台空转。
        ex.shutdown(wait=True, cancel_futures=True)
    return results


class Executor(Protocol):
    """批量任务执行器接口：业务代码依赖本协议，实现可切换（串行/进程/线程）。"""

    def map(
        self,
        worker,
        items: list,
        is_cancelled=None,
        on_done=None,
        max_workers: int | None = None,
    ) -> list:
        """执行 worker(item)，返回与提交顺序一致的结果列表。"""
        ...


class SerialExecutor:
    """串行执行器（调试/小批量/单核场景）。"""

    def map(
        self,
        worker,
        items: list,
        is_cancelled=None,
        on_done=None,
        max_workers: int | None = None,
    ) -> list:
        return _run_serial(worker, items, is_cancelled, on_done)


class ProcessExecutor:
    """多进程执行器（ezdxf 解析 CPU 密集，GIL 下多线程无效，默认首选）。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(
        self,
        worker,
        items: list,
        is_cancelled=None,
        on_done=None,
        max_workers: int | None = None,
    ) -> list:
        return _run_pool(
            ProcessPoolExecutor,
            worker,
            items,
            is_cancelled,
            on_done,
            max_workers or self.max_workers,
        )


class ThreadExecutor:
    """多线程执行器（I/O 密集任务 / 无法 pickle 的 worker 场景）。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(
        self,
        worker,
        items: list,
        is_cancelled=None,
        on_done=None,
        max_workers: int | None = None,
    ) -> list:
        return _run_pool(
            ThreadPoolExecutor,
            worker,
            items,
            is_cancelled,
            on_done,
            max_workers or self.max_workers,
        )


class AutoExecutor:
    """自动执行器：文件数少（<4）或单核时回退串行，否则多进程。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(
        self,
        worker,
        items: list,
        is_cancelled=None,
        on_done=None,
        max_workers: int | None = None,
    ) -> list:
        n = len(items)
        if n == 0:
            return []
        cpus = os.cpu_count() or 1
        workers = (
            max_workers
            or self.max_workers
            or get_default_max_workers()
            or max(1, min(cpus, 4))
        )
        if n < 4 or workers <= 1:
            return _run_serial(worker, items, is_cancelled, on_done)
        return _run_pool(
            ProcessPoolExecutor, worker, items, is_cancelled, on_done, workers
        )


# 全局默认并行度：应用启动时按配置注入（set_default_max_workers）；
# None 时保持历史行为（CPU 数 ≤4 自动）。用单元素容器避免 global 语句。
_DEFAULT_MAX_WORKERS: list[int | None] = [None]


def set_default_max_workers(n: int | None) -> None:
    """设置全局默认并行 worker 数（>0 生效；None 恢复自动）。

    供应用层启动时按 config.json/env 注入（app_config.get_max_workers），
    三功能共享；防止各面板每次运行新建进程池时各自 hardcode 上限。
    """
    _DEFAULT_MAX_WORKERS[0] = n if (n is not None and int(n) > 0) else None


def get_default_max_workers() -> int | None:
    """当前全局默认并行 worker 数（None 表示自动：CPU 数 ≤4）。"""
    return _DEFAULT_MAX_WORKERS[0]


def create_executor(mode: str = "auto", max_workers: int | None = None) -> Executor:
    """创建批量任务执行器（工厂）。

    mode:
        "auto"   - 自动：少文件（<4）或单核回退串行，否则多进程（默认）
        "serial" - 串行（调试 / 行为可预期）
        "process"- 固定多进程
        "thread" - 固定多线程（I/O 密集或 worker 不可 pickle）
    max_workers: 池大小上限（process/thread/auto 生效；None 依次取
                 set_default_max_workers 的全局默认、CPU 数 ≤4 自动）。
    """
    if mode == "auto":
        return AutoExecutor(max_workers=max_workers)
    if mode == "serial":
        return SerialExecutor()
    if mode == "process":
        return ProcessExecutor(max_workers=max_workers)
    if mode == "thread":
        return ThreadExecutor(max_workers=max_workers)
    raise ValueError(f"未知的并行模式: {mode}")


# ---------------------------------------------------------------------------
# 共享进程池（跨 map_files 调用复用）
#
# Windows spawn 下每次新建进程池都有 ~1s+ 的启动开销；同一批处理内的多
# 个阶段（改字 DXF 批 → DWG 批、填表/目录的分块取值）复用同一池可省掉
# 中间各次的 spawn。池按「健康/报废」状态注册：
# - 健康池（无取消发生）在用户退出后保留，供后续调用复用；
# - 任一调用取消后该池标记报废，等其最后一位用户退出即销毁重建——运行中
#   的任务仍等其完成（与 _run_pool 的 shutdown(wait=True, cancel_futures=True)
#   一致），不残留后台子进程空转；不影响其他并发用户（其 fut 仍在跑）。
# 应用退出时调用 shutdown_shared_pools() 回收剩余健康池。
# ---------------------------------------------------------------------------
_shared_lock = threading.Lock()
# pool → [当前用户数, 是否报废]；健康且无用户的池保留复用
_PoolState = list  # [用户数:int, 是否报废:bool]（list 便于原地修改）
_shared_pools: dict[ProcessPoolExecutor, _PoolState] = {}


def _acquire_shared_pool(max_workers: int) -> ProcessPoolExecutor:
    with _shared_lock:
        usable = next(
            (p for p, st in _shared_pools.items() if not st[1]), None
        )
        if usable is None:
            usable = ProcessPoolExecutor(max_workers=max_workers)
            _shared_pools[usable] = [0, False]
        _shared_pools[usable][0] += 1
        return usable


def _release_shared_pool(pool: ProcessPoolExecutor, broken: bool) -> None:
    with _shared_lock:
        st = _shared_pools.get(pool)
        if st is None:
            return
        st[0] -= 1
        if broken:
            st[1] = True
        if st[0] <= 0 and st[1]:
            pool.shutdown(wait=True, cancel_futures=True)
            _shared_pools.pop(pool, None)


def shutdown_shared_pools() -> None:
    """回收全部共享进程池（应用退出时调用；运行中的任务等其完成）。"""
    with _shared_lock:
        for pool, st in list(_shared_pools.items()):
            st[1] = True
            if st[0] <= 0:
                pool.shutdown(wait=True, cancel_futures=True)
                _shared_pools.pop(pool, None)


def _run_shared_pool(worker, items, is_cancelled, on_done, max_workers) -> list:
    """共享进程池执行：同一批内多次 map_files 复用池；取消语义与 _run_pool 一致。"""
    n = len(items)
    results: list = [None] * n
    pool = _acquire_shared_pool(max_workers)
    cancelled = {"v": False}
    try:
        fut_map = {pool.submit(worker, item): i for i, item in enumerate(items)}
        pending = set(fut_map)
        while pending:
            if is_cancelled is not None and is_cancelled():
                cancelled["v"] = True
                break
            done, pending = _wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in done:
                i = fut_map[fut]
                try:
                    result = fut.result()
                except Exception as ex2:  # noqa: BLE001 - 单文件容错
                    result = TaskFailed(ex2)
                results[i] = result
                if on_done is not None:
                    on_done(result, i, items[i])
        if cancelled["v"]:
            # 未开始的任务取消；运行中的任务等其完成——与 _run_pool 的
            # shutdown(wait=True, cancel_futures=True) 语义一致（池的报废/重建
            # 由 _release_shared_pool 在最后用户退出时进行）
            for fut in pending:
                fut.cancel()
            if pending:
                _wait(pending, timeout=None, return_when=ALL_COMPLETED)
    finally:
        _release_shared_pool(pool, broken=cancelled["v"])
    return results


def map_files(
    worker,
    items: list,
    max_workers: int | None = None,
    is_cancelled=None,
    on_done=None,
    executor: Executor | None = None,
    reuse_pool: bool = False,
) -> list:
    """并行执行 worker(item)，返回与提交顺序一致的结果列表（兼容入口）。

    worker  : 模块级顶层函数，接收单个 item（可 pickle 的元组/对象）
    items   : 任务参数列表
    max_workers : 并行进程数上限；None 时取全局默认（set_default_max_workers）
                  或按 CPU 数（≤4）自动决定
    is_cancelled : 取消回调（主进程调用，不传给子进程）
    on_done  : on_done(result, index, item)，每完成一个任务回调
    executor : 指定执行器（默认 None → auto，等价于原 map_files 行为）；
               需要固定串行/线程时传 create_executor(...) 的结果。
    reuse_pool : True 且未指定 executor 时使用共享进程池（同批多次调用
                 复用，省 spawn 开销）；取消后池自动报废重建，语义不变。
    """
    if reuse_pool and executor is None:
        n = len(items)
        if n == 0:
            return []
        workers = (
            max_workers
            or get_default_max_workers()
            or max(1, min(os.cpu_count() or 1, 4))
        )
        # 与 AutoExecutor 一致：文件数少或单核时回退串行，避免进程启动开销
        if n < 4 or workers <= 1:
            return _run_serial(worker, items, is_cancelled, on_done)
        return _run_shared_pool(worker, items, is_cancelled, on_done, workers)
    if executor is not None:
        return executor.map(
            worker,
            items,
            is_cancelled=is_cancelled,
            on_done=on_done,
            max_workers=max_workers,
        )
    return create_executor("auto", max_workers=max_workers).map(
        worker, items, is_cancelled=is_cancelled, on_done=on_done
    )
