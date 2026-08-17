"""RunStartMixin 启动骨架契约测试（不实例化真实 tkinter，全部 mock）。

骨架行为：_start 统一为「running 检查 → _prepare_run（校验收集参数）
→ begin_run → _after_begin_run → _start_worker」；_prepare_run 返回 None
（校验失败）时不进入运行态；_start_worker 启动异常时兜底复位。
"""

from __future__ import annotations

import unittest
from unittest import mock

from cadbatchassistant.gui.mixins.gui_shared import RunStartMixin


class _FakePanel(RunStartMixin):
    """最小面板：只实现骨架需要的成员（其余 mock 由测试注入）。"""

    def __init__(self) -> None:
        self.running = False
        self.btn_start = mock.Mock()
        self.btn_stop = mock.Mock()

    def _prepare_run(self) -> tuple | None:  # pragma: no cover - 子类语义
        return ("args",)


class RunStartMixinTest(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = _FakePanel()
        self.panel._start_worker = mock.Mock()
        self.panel._emit = mock.Mock()
        self.panel._after_begin_run = mock.Mock()
        self.panel._run_maximum = mock.Mock(return_value=5)
        # begin_run 与 messagebox 均来自 gui_shared 模块命名空间
        self._pb = mock.patch("cadbatchassistant.gui.mixins.gui_shared.begin_run")
        self.begin_run = self._pb.start()
        self.addCleanup(self._pb.stop)

    def test_running_skips(self) -> None:
        """运行中（running=True）时直接返回，不重复启动。"""
        self.panel.running = True
        self.panel._start()
        self.panel._start_worker.assert_not_called()
        self.begin_run.assert_not_called()

    def test_prepare_none_does_not_start(self) -> None:
        """_prepare_run 返回 None（校验失败/取消）时不进入运行态。"""
        self.panel._prepare_run = mock.Mock(return_value=None)
        self.panel._start()
        self.panel._start_worker.assert_not_called()
        self.begin_run.assert_not_called()

    def test_prepare_args_starts_worker(self) -> None:
        """_prepare_run 返回参数时：begin_run(maximum) → 钩子 → 启动 worker。"""
        self.panel._prepare_run = mock.Mock(return_value=("x", "y"))
        self.panel._start()
        self.begin_run.assert_called_once_with(self.panel, maximum=5)
        self.panel._after_begin_run.assert_called_once_with(("x", "y"))
        self.panel._start_worker.assert_called_once_with(("x", "y"))

    def test_worker_start_exception_resets_state(self) -> None:
        """_start_worker 抛异常时复位运行态（running=False、按钮恢复），不卡死。"""
        self.panel._prepare_run = mock.Mock(return_value=("x",))
        self.panel._start_worker.side_effect = RuntimeError("boom")
        self.panel._start()
        self.assertFalse(self.panel.running)
        self.panel.btn_start.config.assert_called_with(state="normal")
        self.panel.btn_stop.config.assert_called_with(state="disabled")
        self.panel._emit.assert_called_with("启动失败：boom")


if __name__ == "__main__":
    unittest.main()
