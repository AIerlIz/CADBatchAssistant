"""首次启动引导标记逻辑测试（stdlib unittest + mock）。

覆盖 is_welcome_needed 的判定（空配置 / 缺键 / False / True）与
mark_welcome_seen 的全局配置写入（可读回、保留既有配置项、写入失败静默）；
以及多步引导向导的页面数据与导航逻辑（mock 掉全部 Tk 控件）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadbatchassistant import common
from cadbatchassistant.gui import welcome


class IsWelcomeNeededTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cad_welcome_test_"))

    def tearDown(self):
        for f in self._tmp.iterdir():
            f.unlink(missing_ok=True)
        self._tmp.rmdir()

    def test_empty_config_needs_welcome(self):
        # 首次运行（无配置文件）→ 需要引导
        self.assertTrue(common.is_welcome_needed({}))

    def test_missing_key_needs_welcome(self):
        # 有其它配置但无标记 → 需要引导
        self.assertTrue(common.is_welcome_needed({"oda": r"C:\x\ODAFileConverter.exe"}))

    def test_false_key_needs_welcome(self):
        self.assertTrue(common.is_welcome_needed({"welcome_seen": False}))

    def test_true_key_skips_welcome(self):
        self.assertFalse(common.is_welcome_needed({"welcome_seen": True}))

    def test_corrupt_config_file_needs_welcome(self):
        # 损坏的 JSON 经 load_config 返回 {} → 按首次运行需要引导，不崩溃
        bad = self._tmp / "config.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertTrue(common.is_welcome_needed(common.load_config(bad)))


class MarkWelcomeSeenTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cad_welcome_test_"))
        self._cfg = self._tmp / "config.json"
        self._patcher = mock.patch.object(common, "APP_CONFIG_FILE", self._cfg)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        for f in self._tmp.iterdir():
            f.unlink(missing_ok=True)
        self._tmp.rmdir()

    def test_mark_then_read_back(self):
        common.mark_welcome_seen()
        cfg = common.load_app_config()
        self.assertIs(True, cfg.get("welcome_seen"))
        # 写标记后判定不再需要引导
        self.assertFalse(common.is_welcome_needed(cfg))

    def test_mark_keeps_existing_config(self):
        common.save_app_config({"oda": r"C:\x\ODAFileConverter.exe"})
        common.mark_welcome_seen()
        cfg = common.load_app_config()
        self.assertEqual(cfg["oda"], r"C:\x\ODAFileConverter.exe")
        self.assertIs(True, cfg.get("welcome_seen"))

    def test_mark_write_failure_is_silent(self):
        # 配置文件父路径被普通文件占用 → 写入失败时不抛异常
        blocker = self._tmp / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.object(common, "APP_CONFIG_FILE", blocker / "config.json"):
            common.mark_welcome_seen()  # 不应抛异常
        # 标记未写入成功 → 配置中仍无标记，判定需要引导
        self.assertTrue(common.is_welcome_needed(common.load_app_config()))


class WizardPagesTest(unittest.TestCase):
    """多步引导向导的页面数据（纯逻辑，无需 Tk）。"""

    def test_five_pages_in_order(self):
        titles = [title for title, _ in welcome._PAGES]
        self.assertEqual(
            titles,
            ["欢迎", "改字助手", "填表助手", "目录助手", "设置与 ODA"],
        )

    def test_each_page_has_content(self):
        for title, lines in welcome._PAGES:
            with self.subTest(page=title):
                self.assertTrue(lines, f"{title} 页内容为空")
                for kind, text in lines:
                    self.assertIn(kind, {"head", "body", "hint"})
                    self.assertTrue(text)

    def test_welcome_page_uses_version(self):
        _, lines = welcome._PAGES[0]
        self.assertTrue(
            any(f"v{welcome.__version__}" in text for _, text in lines))


class WelcomeDialogNavTest(unittest.TestCase):
    """向导导航逻辑（mock 掉全部 Tk 控件）。"""

    def setUp(self):
        self._tk = mock.patch("cadbatchassistant.gui.welcome.tk")
        self._ttk = mock.patch("cadbatchassistant.gui.welcome.ttk")
        self._center = mock.patch("cadbatchassistant.gui.welcome.center_window")
        self._mark = mock.patch("cadbatchassistant.gui.welcome.mark_welcome_seen")
        tk_mock = self._tk.start()
        ttk_mock = self._ttk.start()
        self._center.start()
        self._mark_seen = self._mark.start()

        def fake_frame(*args, **kwargs):
            frame = mock.MagicMock()
            frame.winfo_children.return_value = []  # 渲染清空循环可迭代
            return frame

        ttk_mock.Frame.side_effect = fake_frame
        parent = mock.MagicMock()
        parent.winfo_toplevel.return_value = mock.MagicMock()
        self.dlg = welcome.WelcomeDialog(parent)

    def tearDown(self):
        self._tk.stop()
        self._ttk.stop()
        self._center.stop()
        self._mark.stop()

    def test_initial_state(self):
        self.assertEqual(self.dlg._page, 0)
        nav = self.dlg._nav_state()
        self.assertFalse(nav["prev_enabled"])
        self.assertEqual(nav["next_text"], "下一步")
        self.assertFalse(nav["next_closes"])
        self.assertTrue(nav["skip_visible"])

    def test_next_advances_to_last(self):
        for expected in range(1, len(welcome._PAGES)):
            self.dlg._next()
            self.assertEqual(self.dlg._page, expected)

    def test_next_on_last_page_closes(self):
        self.dlg._page = len(welcome._PAGES) - 1
        self.dlg._next()
        self._mark_seen.assert_called_once()
        self.dlg._win.destroy.assert_called_once()

    def test_last_page_nav_state(self):
        self.dlg._page = len(welcome._PAGES) - 1
        nav = self.dlg._nav_state()
        self.assertEqual(nav["next_text"], "完成使用")
        self.assertTrue(nav["next_closes"])
        self.assertFalse(nav["skip_visible"])
        self.assertTrue(nav["prev_enabled"])

    def test_prev_stays_on_first_page(self):
        self.dlg._prev()
        self.assertEqual(self.dlg._page, 0)

    def test_prev_from_middle(self):
        self.dlg._page = 2
        self.dlg._prev()
        self.assertEqual(self.dlg._page, 1)

    def test_close_marks_seen(self):
        self.dlg._close()
        self._mark_seen.assert_called_once()
        self.dlg._win.destroy.assert_called_once()

    def test_render_page_sets_step_label(self):
        self.dlg._page = 1
        self.dlg._render_page()
        _, kwargs = self.dlg._step_label.config.call_args
        self.assertEqual(
            kwargs["text"], f"第 2 / {len(welcome._PAGES)} 步：改字助手")

    def test_render_page_wires_nav_buttons(self):
        # 末页：「完成使用」按钮 command 指向 _close
        self.dlg._page = len(welcome._PAGES) - 1
        self.dlg._render_page()
        _, kwargs = self.dlg._btn_next.config.call_args
        self.assertEqual(kwargs["text"], "完成使用")
        self.assertIs(kwargs["command"].__func__, self.dlg._close.__func__)
        # 中间页：上一步启用、跳过引导隐藏
        self.dlg._page = 1
        self.dlg._render_page()
        args, _ = self.dlg._btn_prev.state.call_args
        self.assertEqual(args[0], ["!disabled"])
        self.dlg._btn_skip.pack_forget.assert_called()
        # 首页：上一步禁用、下一步 command 为 _next
        self.dlg._page = 0
        self.dlg._render_page()
        args, _ = self.dlg._btn_prev.state.call_args
        self.assertEqual(args[0], ["disabled"])
        _, kwargs = self.dlg._btn_next.config.call_args
        self.assertIs(kwargs["command"].__func__, self.dlg._next.__func__)

    def test_close_is_idempotent(self):
        # 窗口已销毁后再次 _close 不应抛异常（winfo_exists 守卫）
        self.dlg._win.winfo_exists.return_value = False
        self.dlg._close()  # 不应抛异常
        self._mark_seen.assert_called_once()
        self.dlg._win.destroy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
