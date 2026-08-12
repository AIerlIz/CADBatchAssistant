# -*- coding: utf-8 -*-
"""AsyncPanel 任务代次机制测试（H4：停止后重启竞态防护）。

不实例化真实 tkinter：mock 掉 apply_vista_theme / ttk.Style / Tk 交互，
直接构造 AsyncPanel 子类验证：
- _stop 会禁用 btn_start（阻止停止后立即重开导致双线程并发）
- _poll_queue 只响应当前代次的 __DONE__（旧任务残留 sentinel 不复位新任务）
"""

from __future__ import annotations

import queue
import unittest
from unittest import mock

from cadbatchassistant import common


class _FakePanel(common.AsyncPanel):
    """最小实现：只提供 _poll_queue / _stop / _on_finish 需要的属性。"""

    def _build_ui(self):
        pass

    def _work(self, *args) -> bool:
        return True


class AsyncPanelGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._patcher = mock.patch.object(common, "apply_vista_theme")
        self._patcher.start()
        self._style = mock.patch.object(common.ttk, "Style")
        self._style.start()

        parent = mock.Mock()
        root = mock.Mock()
        parent.winfo_toplevel.return_value = root
        root.after.return_value = None

        self.panel = _FakePanel(parent)
        # 补齐 _poll_queue 依赖的控件属性
        self.panel.log_text = mock.Mock()
        self.panel.progress = mock.Mock()
        self.panel.btn_start = mock.Mock()
        self.panel.btn_stop = mock.Mock()

    def tearDown(self) -> None:
        self._style.stop()
        self._patcher.stop()

    def test_stop_disables_start_button(self) -> None:
        """H4：_stop 禁用 btn_start，任务结束前不可重开。"""
        self.panel.running = True
        self.panel._stop()
        self.panel.btn_start.config.assert_called_with(state="disabled")
        self.panel.btn_stop.config.assert_called_with(state="disabled")

    def test_poll_ignores_stale_generation_done(self) -> None:
        """旧代次 __DONE__（停止后残留）不触发 _on_finish，不复位 UI 状态。"""
        self.panel._run_seq = 2  # 当前代次 2
        # 队列里只有旧任务（代次 1）的 __DONE__
        self.panel.msg_queue.put(("__DONE__", True, 1))
        with mock.patch.object(self.panel, "_on_finish") as on_finish:
            self.panel._poll_queue()
            on_finish.assert_not_called()

    def test_poll_accepts_current_generation_done(self) -> None:
        """当前代次 __DONE__ 正常触发 _on_finish。"""
        self.panel._run_seq = 3
        self.panel.msg_queue.put(("__DONE__", True, 3))
        with mock.patch.object(self.panel, "_on_finish") as on_finish:
            self.panel._poll_queue()
            on_finish.assert_called_once_with(True)


class StartWorkerSeqTest(unittest.TestCase):
    def setUp(self) -> None:
        self._patcher = mock.patch.object(common, "apply_vista_theme")
        self._patcher.start()
        self._style = mock.patch.object(common.ttk, "Style")
        self._style.start()
        parent = mock.Mock()
        root = mock.Mock()
        parent.winfo_toplevel.return_value = root
        root.after.return_value = None
        self.panel = _FakePanel(parent)
        self.panel._build_ui()

    def tearDown(self) -> None:
        self._style.stop()
        self._patcher.stop()

    def test_start_worker_increments_seq_and_passes_to_run(self) -> None:
        """每次启动分配递增代次，且 worker 以 (seq, *args) 调用 _run。"""
        with mock.patch.object(common.threading, "Thread") as thread_cls:
            self.panel._start_worker(("a", "b"))
            self.panel._start_worker(("c",))
        self.assertEqual(self.panel._run_seq, 2)
        # 第二次启动的 thread target 参数应为 (2, "c")
        calls = [c for c in thread_cls.call_args_list]
        self.assertEqual(calls[1].kwargs["args"], (2, "c"))
        self.assertEqual(calls[0].kwargs["args"], (1, "a", "b"))


if __name__ == "__main__":
    unittest.main()
