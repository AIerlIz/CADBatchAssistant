"""gui_text.CadTextApp._start 的异常复位测试（H5）。

复制输入文件失败（如 DWG 被 AutoCAD 占用）时，面板必须复位运行态
（running=False、按钮恢复、worker 不启动），否则永久卡死在运行态。
不实例化真实 tkinter：全部 mock。
"""

from __future__ import annotations

import unittest
from unittest import mock

import cadbatchassistant.gui.panels.gui_text as gt


class TextStartCopyFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        # CadTextApp.__init__ 需要 AsyncPanel.__init__（真实 Tk）→ 绕开构造，
        # 用 object.__new__ + 手工补属性；_start 只用到的成员在此补齐。
        self.app = gt.CadTextApp.__new__(gt.CadTextApp)
        self.app.running = False
        self.app.scanned_files = [r"D:\a\A1.dwg"]
        self.app.var_dry = mock.Mock()
        self.app.var_dry.get.return_value = False
        self.app.var_out = mock.Mock()
        self.app.var_out.get.return_value = r"D:\out"
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
        # _rules / 复制 / _start_worker 均 mock（begin_run 由 RunStartMixin._start
        # 调用，位于 gui_shared；本测试断言其不执行——复制失败不进入运行态）
        chain = [
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
        """复制失败：面板不进入运行态（running=False、worker 不启动、临时目录清理）。

        复制输入文件在 begin_run 之前完成（_prepare_run 阶段），失败即不启动，
        按钮无需复位（从未被禁用），不存在卡死运行态的可能。
        本测试依赖 ODA 校验通过才走复制分支，故 mock require_for_dwg 返回 None
        （否则 CI 无 ODA 时停在校验、rmtree 不被调用而失败）。
        """
        self._patch_work_chain()
        conv = mock.Mock()
        conv.require_for_dwg.return_value = None
        with (
            mock.patch.object(gt.messagebox, "showwarning"),
            mock.patch.object(gt.messagebox, "showerror"),
            mock.patch.object(gt.dc, "get_converter", return_value=conv),
        ):
            self.app._start()
        self.assertFalse(self.app.running)
        self.app.btn_start.config.assert_not_called()  # 从未禁用，无需恢复
        self.app.btn_stop.config.assert_not_called()
        self.app._start_worker.assert_not_called()
        # 未进入运行态：begin_run（gui_shared）不被调用——由按钮从未禁用隐含
        # 临时目录被清理
        gt.shutil.rmtree.assert_called_once_with(r"D:\tmp\work_in", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
