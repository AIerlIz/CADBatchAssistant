"""首次启动引导标记逻辑测试（stdlib unittest + mock）。

覆盖 is_welcome_needed 的判定（空配置 / 缺键 / False / True）与
mark_welcome_seen 的全局配置写入（可读回、保留既有配置项、写入失败静默）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadbatchassistant import common


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


if __name__ == "__main__":
    unittest.main()
