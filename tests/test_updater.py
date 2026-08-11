"""基于 GitHub Release 的在线更新模块测试（stdlib unittest + mock）。"""

from __future__ import annotations

import base64
import json
import tempfile
import threading
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


class IgnoredVersionTest(unittest.TestCase):
    def test_not_ignored_when_none(self):
        self.assertFalse(updater.is_ignored("v1.1.0", None))

    def test_not_ignored_when_empty(self):
        self.assertFalse(updater.is_ignored("v1.1.0", ""))

    def test_ignored_when_equal(self):
        self.assertTrue(updater.is_ignored("v1.1.0", "v1.1.0"))

    def test_not_ignored_when_differs(self):
        self.assertFalse(updater.is_ignored("v1.2.0", "v1.1.0"))

    def test_ignored_requires_exact_match(self):
        # 忽略记录是 tag 字符串，前缀不同不算忽略
        self.assertFalse(updater.is_ignored("v1.1.0", "1.1.0"))


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
        # 内容以 MZ 开头以通过 PE 头校验（expect_exe 默认开启）
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_download_resp(
                                   [b"MZ", b"abc", b"def", b""], total=8)):
            result = updater.download_asset(
                "https://x/y.exe", dest, size=8,
                progress_cb=lambda d, t: seen.append((d, t)))
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), b"MZabcdef")
        self.assertEqual(seen[-1], (8, 8))
        self.assertEqual(seen[0], (0, 8))

    def test_mirror_prefix_applied(self):
        dest = self._dest("mirror.exe")
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"MZ", b"", b""])) as m:
            updater.download_asset(
                "https://github.com/a/b.exe", dest,
                mirror="https://ghproxy.com/")
        self.assertEqual(
            m.call_args.args[0].full_url,
            "https://ghproxy.com/https://github.com/a/b.exe")
        self.assertEqual(dest.read_bytes(), b"MZ")

    def test_size_mismatch_raises_and_cleans(self):
        dest = self._dest("short.exe")
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_download_resp([b"ab", b""])):
            with self.assertRaises(updater.UpdateError):
                updater.download_asset("https://x/y.exe", dest, size=99,
                                       retries=1, retry_delay=0)
        self.assertFalse(dest.exists())

    def test_mirror_blank_keeps_url(self):
        dest = self._dest("no_mirror.exe")
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"MZ", b"", b""])) as m:
            updater.download_asset("https://github.com/a/b.exe", dest,
                                   mirror="  ")
        self.assertEqual(m.call_args.args[0].full_url,
                         "https://github.com/a/b.exe")

    def test_incomplete_read_raises_and_cleans(self):
        # 连接在 Content-Length 满足前 EOF（IncompleteRead）→ UpdateError 且清理
        dest = self._dest("incomplete.exe")
        err = updater.http.client.IncompleteRead(b"", 100)
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=err):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset("https://x/y.exe", dest,
                                       retries=2, retry_delay=0)
        self.assertIn("已自动尝试", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_size_mismatch_retries_then_succeeds(self):
        # 前 2 次下载不完整，第 3 次成功（自动重试）
        dest = self._dest("retry.exe")
        short = _download_resp([b"MZ", b"", b""], total=2)
        full = _download_resp([b"MZ", b"x" * 10, b""], total=12)
        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=[short, short, full]) as m:
            result = updater.download_asset(
                "https://x/y.exe", dest, size=12, retries=3, retry_delay=0)
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), b"MZ" + b"x" * 10)
        self.assertEqual(m.call_count, 3)

    def test_retries_exhausted_reports_attempts(self):
        # 重试耗尽：消息含已尝试次数，且不留半成品
        dest = self._dest("exhausted.exe")
        short = _download_resp([b"MZ", b"", b""], total=2)
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=short):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset(
                    "https://x/y.exe", dest, size=12, retries=2,
                    retry_delay=0)
        self.assertIn("已自动尝试", str(cm.exception))
        self.assertIn("2 次", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_non_exe_error_page_rejected(self):
        # 镜像/服务器返回 200 错误页（HTML 非 MZ）→ 报「不是安装包」
        dest = self._dest("page.exe")
        html = b"<html><body>Request Entity Too Large</body></html>"
        with mock.patch.object(updater.urllib.request, "urlopen",
                               return_value=_download_resp(
                                   [html, b""], total=len(html))):
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset(
                    "https://x/y.exe", dest, size=len(html),
                    retries=1, retry_delay=0)
        self.assertIn("不是安装包", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_mirror_failure_falls_back_to_direct(self):
        # 镜像不可用 → 自动降级直连 GitHub，文件内容正确
        dest = self._dest("fallback.exe")
        resp = _download_resp([b"MZ", b"body", b""], total=7)

        def fake_urlopen(req, timeout=None):
            if "ghproxy.com" in req.full_url:
                raise updater.urllib.error.URLError("mirror down")
            return resp

        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=fake_urlopen) as m:
            result = updater.download_asset(
                "https://github.com/a/b.exe", dest,
                mirror="https://ghproxy.com/", retries=1, retry_delay=0)
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), b"MZbody")
        urls = [c.args[0].full_url for c in m.call_args_list]
        self.assertEqual(urls, [
            "https://ghproxy.com/https://github.com/a/b.exe",
            "https://github.com/a/b.exe",
        ])

    def test_cancel_not_retried(self):
        # 用户取消（progress_cb 抛 UpdateError("已取消")）→ 不重试、原样传播
        dest = self._dest("cancel.exe")

        def cb(done, total):
            raise updater.UpdateError("已取消")

        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"MZ", b"x", b""],
                                            total=3)) as m:
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset(
                    "https://x/y.exe", dest, size=3, retries=3,
                    retry_delay=0, progress_cb=cb)
        self.assertEqual(str(cm.exception), "已取消")
        self.assertEqual(m.call_count, 1)
        self.assertFalse(dest.exists())  # 取消后不留半成品

    def test_abort_event_interrupts_backoff(self):
        # 退避等待期间取消被置位 → 立即中止（不等待剩余退避时长）
        dest = self._dest("abort.exe")
        evt = threading.Event()

        def fake_urlopen(req, timeout=None):
            evt.set()  # 第一次失败后即置取消标志
            raise updater.urllib.error.URLError("boom")

        with mock.patch.object(updater.urllib.request, "urlopen",
                               side_effect=fake_urlopen) as m:
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset(
                    "https://x/y.exe", dest, retries=3, retry_delay=100,
                    abort_event=evt)
        self.assertEqual(str(cm.exception), "已取消")
        self.assertEqual(m.call_count, 1)
        self.assertFalse(dest.exists())

    def test_retries_clamped_to_min_one(self):
        # retries<=0 时至少尝试 1 次，避免消息出现「已自动尝试 0 次」
        dest = self._dest("clamp.exe")
        with mock.patch.object(
                updater.urllib.request, "urlopen",
                return_value=_download_resp([b"ab", b""])) as m:
            with self.assertRaises(updater.UpdateError) as cm:
                updater.download_asset(
                    "https://x/y.exe", dest, size=99, retries=0,
                    retry_delay=0)
        self.assertIn("已自动尝试 1 次", str(cm.exception))
        self.assertEqual(m.call_count, 1)


class BuildReplaceCommandTest(unittest.TestCase):
    def test_script_embedded_utf16(self):
        cmd = updater.build_replace_command(
            r"C:\tmp\new.exe", r"C:\Program Files\CADBatchAssistant.exe")
        self.assertTrue(cmd.startswith(
            "powershell -NoProfile -NonInteractive -EncodedCommand "))
        encoded = cmd.rsplit(" ", 1)[-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn(r"C:\tmp\new.exe", script)
        self.assertIn(r"C:\Program Files\CADBatchAssistant.exe", script)
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
