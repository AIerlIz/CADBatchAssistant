"""core.parallel 单测：串行/线程池执行、单文件容错、取消语义与自动回退。

v2.6 并行重构的核心逻辑此前无测试覆盖；本文件重点验证取消语义：
「未开始的任务取消、运行中的任务等其完成」——map_files 返回时不得有
后台线程/进程继续空转（否则调用方清理临时目录会因句柄占用失败）。
"""

from __future__ import annotations

import threading
import time

import pytest

from cadbatchassistant.core.parallel import (
    AutoExecutor,
    TaskFailed,
    ThreadExecutor,
    create_executor,
    map_files,
)


def _identity(x):
    return x


def _boom(x):
    if x == 2:
        raise ValueError("boom")
    return x * 2


def test_serial_order_and_results():
    ex = create_executor("serial")
    assert ex.map(_identity, [1, 2, 3]) == [1, 2, 3]


def test_serial_single_failure_wrapped_not_abort():
    """单文件异常包装为 TaskFailed，不中断整批（串行路径）。"""
    res = create_executor("serial").map(_boom, [1, 2, 3])
    assert res[0] == 2
    assert isinstance(res[1], TaskFailed)
    assert "boom" in str(res[1].error)
    assert res[2] == 6


def test_serial_on_done_called_in_order():
    done = []

    def on_done(result, idx, item):
        done.append((idx, item, result))

    create_executor("serial").map(_identity, [10, 20], on_done=on_done)
    assert done == [(0, 10, 10), (1, 20, 20)]


def test_serial_cancel_stops_after_current_item():
    """取消：串行路径当前文件处理完后停止（不再启动后续任务）。"""
    seen = []

    def worker(x):
        seen.append(x)
        return x

    def is_cancelled():
        return len(seen) >= 1

    res = create_executor("serial").map(worker, [1, 2, 3], is_cancelled=is_cancelled)
    assert seen == [1]
    assert res == [1]


def test_thread_results_in_submit_order():
    """线程池：结果按提交顺序返回（完成顺序无关）。"""
    def slow_first(x):
        time.sleep(0.05 if x == 0 else 0)
        return x * 10

    res = ThreadExecutor(max_workers=2).map(slow_first, [0, 1, 2])
    assert res == [0, 10, 20]


def test_thread_failure_wrapped():
    res = ThreadExecutor(max_workers=2).map(_boom, [1, 2, 3])
    assert res[0] == 2
    assert isinstance(res[1], TaskFailed)
    assert res[2] == 6


def test_cancel_waits_running_tasks_and_skips_pending():
    """取消语义核心：运行中的任务等其完成（不杀线程），未开始的任务被取消。

    保证 map_files 返回时没有后台线程继续写文件——调用方随后清理临时
    目录不会因句柄占用而静默失败（修复前 shutdown(wait=False) 会泄漏）。
    """
    started: list[int] = []
    finished: list[int] = []
    lock = threading.Lock()
    flag = {"cancel": False}

    def slow(x):
        with lock:
            started.append(x)
        time.sleep(0.2)
        with lock:
            finished.append(x)
        return x

    def is_cancelled():
        return flag["cancel"]

    def after_first(result, idx, item):
        flag["cancel"] = True  # 第一个任务完成后触发取消

    res = ThreadExecutor(max_workers=2).map(
        slow, list(range(6)), is_cancelled=is_cancelled, on_done=after_first
    )

    with lock:
        assert started, "至少应有一个任务开始执行"
        # map 返回时，所有已开始的任务都已完成（无后台线程空转）
        assert set(finished) == set(started)
    # 未开始的任务被取消：started 少于总数
    assert len(started) < 6
    # 已收集的结果按提交序落位、值正确（完成序不定，不假设具体哪几个）
    collected = {i: r for i, r in enumerate(res) if r is not None}
    assert collected, "至少应收集到已完成任务的结果"
    assert all(collected[i] == i for i in collected)


def test_cancel_without_running_tasks_returns():
    """is_cancelled 一开始就为 True：不执行任何任务，立即返回（无结果）。"""
    seen = []

    def worker(x):
        seen.append(x)
        return x

    res = map_files(
        worker,
        [1, 2],
        is_cancelled=lambda: True,
        executor=create_executor("serial"),
    )
    assert seen == []
    assert res == []


def test_auto_falls_back_to_serial_for_few_items():
    assert AutoExecutor().map(_identity, [1, 2, 3]) == [1, 2, 3]


def test_map_files_empty():
    assert map_files(_identity, []) == []


def test_create_executor_unknown_mode():
    with pytest.raises(ValueError):
        create_executor("bogus")
