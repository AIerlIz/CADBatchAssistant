# -*- coding: utf-8 -*-
"""gui_text.CadTextApp._start 的异常复位测试（H5）。

复制输入文件失败（如 DWG 被 AutoCAD 占用）时，面板必须复位运行态
（running=False、按钮恢复、worker 不启动），否则永久卡死在运行态。
不实例化真实 tkinter：全部 mock。
"""

from __future__ import annotations

import unittest
from unittest import mock

import cadbatchassistant.gui.gui_text as gt


class TextStartCopyFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        # CadTextApp.__init__ 需要 AsyncPanel.__init__（真实 Tk）→ 绕开构造，
        # 用 object.__new__ + 手工补属性；_start 只用到的成员在此补齐。
        self.app = gt.CadTextApp.__new__(gt.CadTextApp)
        self.app.running = False
        self.app.scanned_files = [r"D:\a\A1.dwg"]
        self.app.var_dry = mock.Mock()
        self.app.var_dry.get.return_value = False
        self.app.var_output = mock.Mock()
        self.app.var_output.get.return_value = r"D:\out"
        self.app.rules_data = [("旧", "新")]
        self.app.var_case = mock.Mock()
        self.app.var_case.get.return_value = True
        self.app.var_regex = mock.Mock()
        self.app.var_regex.get.return_value = False
        self.app.btn_start = mock.Mock()
        self.app.btn_stop = mock.Mock()
        self.app.progress = mock.Mock()
        self.app.log_text = mock.Mock()
        self.app._cancel_event = mock.Mock()
        self.app.msg_queue = mock.Mock()

    def _patch_work_chain(self):
        # _rules / begin_run / 复制 / _start_worker 均 mock
        chain = [
            mock.patch.object(gt, "begin_run"),
            mock.patch.object(gt.tempfile, "mkdtemp", return_value=r"D:\tmp\work_in"),
            mock.patch.object(gt.shutil, "copy2", side_effect=PermissionError("占用")),
            mock.patch.object(gt.shutil, "rmtree"),
            mock.patch.object(self.app, "_start_worker"),
            mock.patch.object(self.app, "_emit"),
        ]
        for p in chain:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in chain])

    def test_copy_failure_resets_running_state(self) -> None:
        """复制失败：running 复位 False、btn_start 恢复、worker 不启动。"""
        self._patch_work_chain()
        with mock.patch.object(gt.messagebox, "showwarning"):
            self.app._start()
        self.assertFalse(self.app.running)
        self.app.btn_start.config.assert_called_with(state="normal")
        self.app.btn_stop.config.assert_called_with(state="disabled")
        self.app._start_worker.assert_not_called()
        # 临时目录被清理
        gt.shutil.rmtree.assert_called_once_with(r"D:\tmp\work_in", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
