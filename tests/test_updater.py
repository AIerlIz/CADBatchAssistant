"""基于 GitHub Release 的在线更新模块测试（stdlib unittest + mock）。"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadbatchassistant.core import updater


class ParseVersionTest(unittest.TestCase):
    def test_standard_tag(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))

    def test_no_v_prefix(self):
        self.assertEqual(updater.parse_version("1.2.3"), (1, 2, 3))

    def test_too_few_parts(self):
        self.assertIsNone(updater.parse_version("v1.2"))

    def test_suffix_unsupported(self):
        self.assertIsNone(updater.parse_version("v1.2.3-beta"))

    def test_non_numeric(self):
        self.assertIsNone(updater.parse_version("v1.x.3"))

    def test_empty(self):
        self.assertIsNone(updater.parse_version(""))


class IsNewerTest(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(updater.is_newer((1, 1, 0), (1, 0, 0)))

    def test_equal(self):
        self.assertFalse(updater.is_newer((1, 0, 0), (1, 0, 0)))

    def test_older(self):
        self.assertFalse(updater.is_newer((1, 0, 0), (1, 1, 0)))

    def test_none(self):
        self.assertFalse(updater.is_newer(None, (1, 0, 0)))
        self.assertFalse(updater.is_newer((1, 1, 0), None))


def _json_response(data: dict):
    """构造支持 with 语句、read() 返回 JSON 字节的 mock response。"""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _download_resp(chunks: list[bytes], total=None):
    """构造支持 with 语句、按块 read()、带 Content-Length 的 mock response。"""
    resp = mock.MagicMock()
    resp.read.side_effect = chunks
    resp.headers = {"Content-Length": str(total) if total is not None else None}
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class CheckLatestTest(unittest.TestCase):
    def test_success(self):
        data = {
            "tag_name": "v1.1.0",
            "assets": [{
                "name": updater.ASSET_NAME,
                "browser_download_url": "https://github.com/AIerlIz/CADBatchAssistant/"
                                        "releases/download/v1.1.0/CADBatchAssistant.exe",
                "size": 123,
            }],
        }
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_json_response(data)):
            result = updater.check_latest()
        self.assertTrue(result["ok"])
        self.assertEqual(result["tag"], "v1.1.0")
        self.assertEqual(result["version"], (1, 1, 0))
        self.assertEqual(result["size"], 123)
        self.assertTrue(result["url"].endswith("CADBatchAssistant.exe"))

    def test_no_matching_asset(self):
        data = {"tag_name": "v1.1.0",
                "assets": [{"name": "other.exe",
                            "browser_download_url": "https://x/other.exe"}]}
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_json_response(data)):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("安装包", result["error"])

    def test_unparseable_tag(self):
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_json_response({"tag_name": "beta"})):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("版本号", result["error"])

    def test_http_error(self):
        err = updater.urllib.error.HTTPError("u", 404, "Not Found", None, None)
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=err):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("404", result["error"])

    def test_bad_json(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=resp):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("解析失败", result["error"])

    def test_assets_not_list(self):
        # 异常数据（assets 非 list）不抛异常，按无资产处理
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_json_response(
                                   {"tag_name": "v1.1.0", "assets": "oops"})):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("安装包", result["error"])

    def test_network_error(self):
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                side_effect=updater.urllib.error.URLError("timeout")):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("无法连接", result["error"])


class DownloadAssetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cad_updater_test_"))

    def tearDown(self):
        for f in self._tmp.iterdir():
            f.unlink(missing_ok=True)
        self._tmp.rmdir()

    def _dest(self, name: str) -> Path:
        return self._tmp / name

    def test_download_writes_file_and_progress(self):
        dest = self._dest("out.exe")
        seen = []
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_download_resp(
                                   [b"abc", b"def", b""], total=6)):
            result = updater.download_asset(
                "https://x/y.exe", dest, size=6,
                progress_cb=lambda d, t: seen.append((d, t)))
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), b"abcdef")
        self.assertEqual(seen[-1], (6, 6))
        self.assertEqual(seen[0], (0, 6))

    def test_mirror_prefix_applied(self):
        dest = self._dest("mirror.exe")
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"x", b""])) as m:
            updater.download_asset(
                "https://github.com/a/b.exe", dest,
                mirror="https://ghproxy.com/")
        self.assertEqual(
            m.call_args.args[0].full_url,
            "https://ghproxy.com/https://github.com/a/b.exe")
        self.assertEqual(dest.read_bytes(), b"x")

    def test_size_mismatch_raises_and_cleans(self):
        dest = self._dest("short.exe")
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_download_resp([b"ab", b""])):
            with self.assertRaises(updater.UpdateError):
                updater.download_asset("https://x/y.exe", dest, size=99)
        self.assertFalse(dest.exists())

    def test_mirror_blank_keeps_url(self):
        dest = self._dest("no_mirror.exe")
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"x", b""])) as m:
            updater.download_asset("https://github.com/a/b.exe", dest,
                                   mirror="  ")
        self.assertEqual(m.call_args.args[0].full_url,
                         "https://github.com/a/b.exe")


class BuildReplaceCommandTest(unittest.TestCase):
    def test_script_embedded_utf16(self):
        cmd = updater.build_replace_command(
            r"C:\tmp\new.exe", r"C:\Program Files\CAD批处理助手.exe")
        self.assertTrue(cmd.startswith(
            "powershell -NoProfile -NonInteractive -EncodedCommand "))
        encoded = cmd.rsplit(" ", 1)[-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn(r"C:\tmp\new.exe", script)
        self.assertIn(r"C:\Program Files\CAD批处理助手.exe", script)
        self.assertIn("Start-Process", script)

    def test_quote_in_path_escaped(self):
        cmd = updater.build_replace_command(
            r"C:\tmp\a'b.exe", r"C:\Program Files\a'b\app.exe")
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertIn(r"$src = 'C:\tmp\a''b.exe'", script)
        # Copy-Item 与 Start-Process 两处重启路径均转义，无裸单引号
        self.assertIn(r"a''b\app.exe", script)
        self.assertNotIn("a'b", script)

    def test_no_restart(self):
        cmd = updater.build_replace_command("a.exe", "b.exe", restart=False)
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertNotIn("Start-Process", script)


class RunReplaceTest(unittest.TestCase):
    def test_spawns_powershell_without_shell(self):
        with mock.patch.object(updater.subprocess, "Popen") as m:
            updater.run_replace(r"C:\a\new.exe", r"C:\a\app.exe")
        m.assert_called_once()
        cmd = m.call_args.args[0]
        self.assertTrue(cmd.startswith("powershell -NoProfile"))
        self.assertFalse(m.call_args.kwargs.get("shell"))


class IsFrozenTest(unittest.TestCase):
    def test_dev_mode_not_frozen(self):
        self.assertFalse(updater.is_frozen())


if __name__ == "__main__":
    unittest.main()
