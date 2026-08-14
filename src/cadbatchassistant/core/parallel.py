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
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor
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
    cancelled = False
    try:
        fut_map = {ex.submit(worker, item): i for i, item in enumerate(items)}
        # 用 wait(timeout) 轮询而非 as_completed 阻塞：每 0.2s 检查一次取消，
        # 保证「停止」按钮在任务尚未完成时也能及时响应。
        pending = set(fut_map)
        while pending:
            if is_cancelled is not None and is_cancelled():
                cancelled = True
                break
            done, pending = _wait(pending, timeout=0.2,
                                  return_when=FIRST_COMPLETED)
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
        if cancelled:
            ex.shutdown(wait=False, cancel_futures=True)
        else:
            ex.shutdown(wait=True)
    return results


class Executor(Protocol):
    """批量任务执行器接口：业务代码依赖本协议，实现可切换（串行/进程/线程）。"""

    def map(self, worker, items: list,
            is_cancelled=None, on_done=None,
            max_workers: int | None = None) -> list:
        """执行 worker(item)，返回与提交顺序一致的结果列表。"""
        ...


class SerialExecutor:
    """串行执行器（调试/小批量/单核场景）。"""

    def map(self, worker, items: list,
            is_cancelled=None, on_done=None,
            max_workers: int | None = None) -> list:
        return _run_serial(worker, items, is_cancelled, on_done)


class ProcessExecutor:
    """多进程执行器（ezdxf 解析 CPU 密集，GIL 下多线程无效，默认首选）。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(self, worker, items: list,
            is_cancelled=None, on_done=None,
            max_workers: int | None = None) -> list:
        return _run_pool(ProcessPoolExecutor, worker, items,
                         is_cancelled, on_done,
                         max_workers or self.max_workers)


class ThreadExecutor:
    """多线程执行器（I/O 密集任务 / 无法 pickle 的 worker 场景）。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(self, worker, items: list,
            is_cancelled=None, on_done=None,
            max_workers: int | None = None) -> list:
        return _run_pool(ThreadPoolExecutor, worker, items,
                         is_cancelled, on_done,
                         max_workers or self.max_workers)


class AutoExecutor:
    """自动执行器：文件数少（<4）或单核时回退串行，否则多进程。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers

    def map(self, worker, items: list,
            is_cancelled=None, on_done=None,
            max_workers: int | None = None) -> list:
        n = len(items)
        if n == 0:
            return []
        cpus = os.cpu_count() or 1
        workers = max_workers or self.max_workers or max(1, min(cpus, 4))
        if n < 4 or workers <= 1:
            return _run_serial(worker, items, is_cancelled, on_done)
        return _run_pool(ProcessPoolExecutor, worker, items,
                         is_cancelled, on_done, workers)


def create_executor(mode: str = "auto",
                    max_workers: int | None = None) -> Executor:
    """创建批量任务执行器（工厂）。

    mode:
        "auto"   - 自动：少文件（<4）或单核回退串行，否则多进程（默认）
        "serial" - 串行（调试 / 行为可预期）
        "process"- 固定多进程
        "thread" - 固定多线程（I/O 密集或 worker 不可 pickle）
    max_workers: 池大小上限（process/thread/auto 生效；None 按 CPU 数 ≤4 自动）。
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


def map_files(worker, items: list,
              max_workers: int | None = None,
              is_cancelled=None,
              on_done=None,
              executor: Executor | None = None) -> list:
    """并行执行 worker(item)，返回与提交顺序一致的结果列表（兼容入口）。

    worker  : 模块级顶层函数，接收单个 item（可 pickle 的元组/对象）
    items   : 任务参数列表
    max_workers : 并行进程数上限；None 时按 CPU 数（≤4）自动决定
    is_cancelled : 取消回调（主进程调用，不传给子进程）
    on_done  : on_done(result, index, item)，每完成一个任务回调
    executor : 指定执行器（默认 None → auto，等价于原 map_files 行为）；
               需要固定串行/线程时传 create_executor(...) 的结果。
    """
    if executor is not None:
        return executor.map(worker, items, is_cancelled=is_cancelled,
                            on_done=on_done, max_workers=max_workers)
    return create_executor("auto", max_workers=max_workers).map(
        worker, items, is_cancelled=is_cancelled, on_done=on_done)
