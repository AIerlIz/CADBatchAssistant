"""基于 GitHub Release 的在线更新模块测试（stdlib unittest + mock）。"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cadbatchassistant.core import updater
from cadbatchassistant.core.updater.download import _parse_sha256


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


def _sha256_resp(content: bytes):
    """构造返回 content 的 sha256 文本的 mock response。"""
    resp = mock.MagicMock()
    resp.read.return_value = hashlib.sha256(content).hexdigest().encode()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def sha256_first_urlopen(sha_url: str, content: bytes, *download_handlers):
    """构造 fake urlopen：首个请求（sha256 拉取）返回 content 的哈希，
    其余依次交给 handlers。

    各 handler 可为异常实例（原样抛出）或 response（直接返回）；调用方需保证
    请求总数不超过 handlers 数量。
    """
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _sha256_resp(content)
        handler = download_handlers[calls["n"] - 2]
        if isinstance(handler, BaseException):
            raise handler
        return handler

    return fake_urlopen


class CheckLatestTest(unittest.TestCase):
    def test_success(self):
        data = {
            "tag_name": "v1.1.0",
            "assets": [
                {
                    "name": updater.ASSET_NAME,
                    "browser_download_url": "https://github.com/AIerlIz/CADBatchAssistant/"
                    "releases/download/v1.1.0/CADBatchAssistant.exe",
                    "size": 123,
                },
                {
                    "name": updater.SHA256_ASSET_NAME,
                    "browser_download_url": "https://github.com/AIerlIz/CADBatchAssistant/"
                    "releases/download/v1.1.0/CADBatchAssistant.exe.sha256",
                },
            ],
        }
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=_json_response(data)
        ):
            result = updater.check_latest()
        self.assertTrue(result["ok"])
        self.assertEqual(result["tag"], "v1.1.0")
        self.assertEqual(result["version"], (1, 1, 0))
        self.assertEqual(result["size"], 123)
        self.assertTrue(result["url"].endswith("CADBatchAssistant.exe"))
        self.assertTrue(result["sha256_url"].endswith("CADBatchAssistant.exe.sha256"))

    def test_no_matching_asset(self):
        data = {
            "tag_name": "v1.1.0",
            "assets": [
                {"name": "other.exe", "browser_download_url": "https://x/other.exe"}
            ],
        }
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=_json_response(data)
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("安装包", result["error"])

    def test_unparseable_tag(self):
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            return_value=_json_response({"tag_name": "beta"}),
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("版本号", result["error"])

    def test_http_error(self):
        err = updater.urllib.error.HTTPError("u", 404, "Not Found", None, None)
        with mock.patch.object(updater.urllib.request, "urlopen", side_effect=err):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("404", result["error"])

    def test_bad_json(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"not json"
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=resp):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("解析失败", result["error"])

    def test_assets_not_list(self):
        # 异常数据（assets 非 list）不抛异常，按无资产处理
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            return_value=_json_response({"tag_name": "v1.1.0", "assets": "oops"}),
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("安装包", result["error"])

    def test_network_error(self):
        with mock.patch.object(
            updater.urllib.request,
            "urlopen",
            side_effect=updater.urllib.error.URLError("timeout"),
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("无法连接", result["error"])

    def test_oversized_response_rejected(self):
        """L6：响应超过大小上限时中止读取并报错（防恶意超大 body 耗尽内存）。"""
        oversized = mock.MagicMock()
        oversized.read.return_value = b"x" * (updater.MAX_RESPONSE_BYTES + 1)
        oversized.__enter__.return_value = oversized
        oversized.__exit__.return_value = False
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=oversized
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("响应过大", result["error"])

    def test_sha256_asset_url_returned(self):
        """M9：Release 含 .sha256 资产时返回其下载 URL。"""
        exe_url = (
            "https://github.com/AIerlIz/CADBatchAssistant/releases/"
            "download/v1.1.0/CADBatchAssistant.exe"
        )
        sha_url = exe_url + ".sha256"
        data = {
            "tag_name": "v1.1.0",
            "assets": [
                {
                    "name": updater.ASSET_NAME,
                    "browser_download_url": exe_url,
                    "size": 123,
                },
                {"name": updater.SHA256_ASSET_NAME, "browser_download_url": sha_url},
            ],
        }
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=_json_response(data)
        ):
            result = updater.check_latest()
        self.assertTrue(result["ok"])
        self.assertEqual(result["sha256_url"], sha_url)

    def test_missing_sha256_asset_rejected(self):
        """无 .sha256 资产的 Release 视为不可安全更新（不再回退弱校验）。"""
        exe_url = (
            "https://github.com/AIerlIz/CADBatchAssistant/releases/"
            "download/v1.0.0/CADBatchAssistant.exe"
        )
        data = {
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": updater.ASSET_NAME,
                    "browser_download_url": exe_url,
                    "size": 123,
                }
            ],
        }
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=_json_response(data)
        ):
            result = updater.check_latest()
        self.assertFalse(result["ok"])
        self.assertIn("校验和资产", result["error"])


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
        content = b"MZ" + b"abc" + b"def"
        fake = sha256_first_urlopen(
            "https://x/y.exe.sha256", content,
            _download_resp([b"MZ", b"abc", b"def", b""], total=8),
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ):
            result = updater.download_asset(
                "https://x/y.exe",
                dest,
                size=8,
                progress_cb=lambda d, t: seen.append((d, t)),
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), content)
        self.assertEqual(seen[-1], (8, 8))
        self.assertEqual(seen[0], (0, 8))

    def test_mirror_prefix_applied(self):
        dest = self._dest("mirror.exe")
        content = b"MZ"
        fake = sha256_first_urlopen(
            "https://github.com/a/b.exe.sha256", content,
            _download_resp([b"MZ", b"", b""]),
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ) as m:
            updater.download_asset(
                "https://github.com/a/b.exe",
                dest,
                mirror="https://ghproxy.com/",
                sha256_url="https://github.com/a/b.exe.sha256",
            )
        # 首次调用为 sha256 拉取（直连 GitHub）；第二次为 exe 下载（镜像前缀）
        self.assertEqual(
            m.call_args_list[1].args[0].full_url,
            "https://ghproxy.com/https://github.com/a/b.exe",
        )
        self.assertEqual(dest.read_bytes(), content)

    def test_size_mismatch_raises_and_cleans(self):
        dest = self._dest("short.exe")
        content = b"ab"
        fake = sha256_first_urlopen(
            "https://x/y.exe.sha256", content, _download_resp([b"ab", b""])
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ), self.assertRaises(updater.UpdateError):
            updater.download_asset(
                "https://x/y.exe",
                dest,
                size=99,
                retries=1,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertFalse(dest.exists())

    def test_mirror_blank_keeps_url(self):
        dest = self._dest("no_mirror.exe")
        fake = sha256_first_urlopen(
            "https://github.com/a/b.exe.sha256", b"MZ",
            _download_resp([b"MZ", b"", b""]),
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ) as m:
            updater.download_asset(
                "https://github.com/a/b.exe",
                dest,
                mirror="  ",
                sha256_url="https://github.com/a/b.exe.sha256",
            )
        # 第二次调用（exe 下载）保持原 URL
        self.assertEqual(
            m.call_args_list[1].args[0].full_url, "https://github.com/a/b.exe"
        )

    def test_incomplete_read_raises_and_cleans(self):
        # 连接在 Content-Length 满足前 EOF（IncompleteRead）→ UpdateError 且清理
        dest = self._dest("incomplete.exe")
        err = updater.http.client.IncompleteRead(b"", 100)
        fake = sha256_first_urlopen("https://x/y.exe.sha256", b"MZ", err, err)
        with (
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=fake),
            self.assertRaises(updater.UpdateError) as cm,
        ):
            updater.download_asset(
                "https://x/y.exe",
                dest,
                retries=2,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertIn("已自动尝试", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_size_mismatch_retries_then_succeeds(self):
        # 前 2 次下载不完整，第 3 次成功（自动重试；sha256 拉取不占重试次数）
        dest = self._dest("retry.exe")
        content = b"MZ" + b"x" * 10
        short = _download_resp([b"MZ", b"", b""], total=2)
        full = _download_resp([b"MZ", b"x" * 10, b""], total=12)
        fake = sha256_first_urlopen(
            "https://x/y.exe.sha256", content, short, short, full
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ) as m:
            result = updater.download_asset(
                "https://x/y.exe",
                dest,
                size=12,
                retries=3,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), content)
        self.assertEqual(m.call_count, 4)  # sha256 拉取 1 次 + 下载 3 次

    def test_retries_exhausted_reports_attempts(self):
        # 重试耗尽：消息含已尝试次数，且不留半成品
        dest = self._dest("exhausted.exe")
        short = _download_resp([b"MZ", b"", b""], total=2)
        fake = sha256_first_urlopen("https://x/y.exe.sha256", b"MZ", short, short)
        with (
            mock.patch.object(updater.urllib.request, "urlopen", side_effect=fake),
            self.assertRaises(updater.UpdateError) as cm,
        ):
            updater.download_asset(
                "https://x/y.exe",
                dest,
                size=12,
                retries=2,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertIn("已自动尝试", str(cm.exception))
        self.assertIn("2 次", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_non_exe_error_page_rejected(self):
        # 镜像/服务器返回 200 错误页（HTML 非 MZ）→ 报「不是安装包」
        dest = self._dest("page.exe")
        html = b"<html><body>Request Entity Too Large</body></html>"
        fake = sha256_first_urlopen("https://x/y.exe.sha256", html,
                                    _download_resp([html, b""], total=len(html)))
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ), self.assertRaises(updater.UpdateError) as cm:
            updater.download_asset(
                "https://x/y.exe",
                dest,
                size=len(html),
                retries=1,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertIn("不是安装包", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_mirror_failure_falls_back_to_direct(self):
        # 镜像不可用 → 自动降级直连 GitHub，文件内容正确
        dest = self._dest("fallback.exe")
        content = b"MZbody"
        sha_url = "https://github.com/a/b.exe.sha256"
        resp = _download_resp([b"MZ", b"body", b""], total=7)

        def fake_urlopen(req, timeout=None):
            if req.full_url == sha_url:
                return _sha256_resp(content)
            if "ghproxy.com" in req.full_url:
                raise updater.urllib.error.URLError("mirror down")
            return resp

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ) as m:
            result = updater.download_asset(
                "https://github.com/a/b.exe",
                dest,
                mirror="https://ghproxy.com/",
                retries=1,
                retry_delay=0,
                sha256_url=sha_url,
            )
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), content)
        urls = [c.args[0].full_url for c in m.call_args_list]
        self.assertEqual(
            urls,
            [
                sha_url,  # sha256 校验和直连
                "https://ghproxy.com/https://github.com/a/b.exe",
                "https://github.com/a/b.exe",
            ],
        )

    def test_missing_sha256_url_rejected(self):
        # 未提供 sha256_url → 直接拒绝，不发起任何下载
        dest = self._dest("nohash.exe")
        with (
            mock.patch.object(updater.urllib.request, "urlopen") as m,
            self.assertRaises(updater.UpdateError) as cm,
        ):
            updater.download_asset("https://x/y.exe", dest, retries=1, retry_delay=0)
        self.assertIn("缺少 sha256", str(cm.exception))
        m.assert_not_called()

    def test_cancel_not_retried(self):
        # 用户取消（progress_cb 抛 UpdateError("已取消")）→ 不重试、原样传播
        dest = self._dest("cancel.exe")

        def cb(done, total):
            raise updater.UpdateError("已取消")

        fake = sha256_first_urlopen(
            "https://x/y.exe.sha256", b"MZx",
            _download_resp([b"MZ", b"x", b""], total=3),
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ) as m, self.assertRaises(updater.UpdateError) as cm:
            updater.download_asset(
                "https://x/y.exe",
                dest,
                size=3,
                retries=3,
                retry_delay=0,
                progress_cb=cb,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertEqual(str(cm.exception), "已取消")
        self.assertEqual(m.call_count, 2)  # sha256 拉取 1 次 + 下载 1 次
        self.assertFalse(dest.exists())  # 取消后不留半成品

    def test_abort_event_interrupts_backoff(self):
        # 退避等待期间取消被置位 → 立即中止（不等待剩余退避时长）
        dest = self._dest("abort.exe")
        evt = threading.Event()
        sha_url = "https://x/y.exe.sha256"

        def fake_urlopen(req, timeout=None):
            if req.full_url == sha_url:
                return _sha256_resp(b"MZ")
            evt.set()  # 首次下载失败后即置取消标志
            raise updater.urllib.error.URLError("boom")

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ) as m, self.assertRaises(updater.UpdateError) as cm:
            updater.download_asset(
                "https://x/y.exe",
                dest,
                retries=3,
                retry_delay=100,
                abort_event=evt,
                sha256_url=sha_url,
            )
        self.assertEqual(str(cm.exception), "已取消")
        self.assertEqual(m.call_count, 2)  # sha256 拉取 1 次 + 下载 1 次
        self.assertFalse(dest.exists())

    def test_retries_clamped_to_min_one(self):
        # retries<=0 时至少尝试 1 次，避免消息出现「已自动尝试 0 次」
        dest = self._dest("clamp.exe")
        fake = sha256_first_urlopen(
            "https://x/y.exe.sha256", b"ab", _download_resp([b"ab", b""])
        )
        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake
        ) as m, self.assertRaises(updater.UpdateError) as cm:
            updater.download_asset(
                "https://x/y.exe",
                dest,
                size=99,
                retries=0,
                retry_delay=0,
                sha256_url="https://x/y.exe.sha256",
            )
        self.assertIn("已自动尝试 1 次", str(cm.exception))
        self.assertEqual(m.call_count, 2)  # sha256 拉取 1 次 + 下载 1 次


class Sha256VerificationTest(unittest.TestCase):
    """M9：sha256 强校验 + 明文 HTTP 镜像拒绝。"""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cad_sha256_test_"))
        self.exe_url = "https://github.com/a/b/CADBatchAssistant.exe"
        self.sha_url = self.exe_url + ".sha256"

    def tearDown(self):
        for f in self._tmp.iterdir():
            f.unlink(missing_ok=True)
        self._tmp.rmdir()

    def _dest(self, name: str) -> Path:
        return self._tmp / name

    def _sha_resp(self, body: bytes):
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_sha256_match_succeeds(self):
        """exe 内容与 .sha256 一致 → 下载成功。"""
        dest = self._dest("ok.exe")
        content = b"MZ" + b"body"
        real_hash = hashlib.sha256(content).hexdigest()
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return self._sha_resp(f"{real_hash}  CADBatchAssistant.exe".encode())
            return _download_resp([content, b""], total=len(content))

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            result = updater.download_asset(
                self.exe_url, dest, retries=1, retry_delay=0, sha256_url=self.sha_url
            )
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), content)

    def test_sha256_mismatch_rejected_and_cleaned(self):
        """exe 内容与 .sha256 不符（被篡改）→ 报校验失败并清理半成品。"""
        dest = self._dest("bad.exe")
        content = b"MZ" + b"tampered"
        other_hash = "0" * 64  # 与真实内容不符

        def fake_urlopen(req, timeout=None):
            if req.full_url == self.sha_url:
                return self._sha_resp(f"{other_hash}  a.exe".encode())
            return _download_resp([content, b""], total=len(content))

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ), self.assertRaises(updater.UpdateError) as cm:
            updater.download_asset(
                self.exe_url,
                dest,
                retries=1,
                retry_delay=0,
                sha256_url=self.sha_url,
            )
        self.assertIn("SHA-256 不匹配", str(cm.exception))
        self.assertFalse(dest.exists())

    def test_parse_sha256_both_formats(self):
        """_parse_sha256 兼容「hash  文件名」与纯 hash 两种格式。"""
        h = "a" * 64
        self.assertEqual(_parse_sha256(f"{h}  CADBatchAssistant.exe\n"), h)
        self.assertEqual(_parse_sha256(f"{h.upper()}\n"), h)
        with self.assertRaises(updater.UpdateError):
            _parse_sha256("not a hash")

    def test_plain_http_mirror_rejected(self):
        """M9：明文 http:// 镜像被拒绝，不发起任何下载。"""
        dest = self._dest("http.exe")
        with (
            mock.patch.object(updater.urllib.request, "urlopen") as m,
            self.assertRaises(updater.UpdateError) as cm,
        ):
            updater.download_asset(
                self.exe_url,
                dest,
                mirror="http://mirror.example.com/",
                retries=1,
                retry_delay=0,
            )
        self.assertIn("仅支持 HTTPS", str(cm.exception))
        m.assert_not_called()

    def test_https_mirror_allowed(self):
        """https 镜像正常使用（sha256 校验和仍直连 GitHub）。"""
        dest = self._dest("mirror.exe")
        content = b"MZ" + b"ok"
        real_hash = hashlib.sha256(content).hexdigest()
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return self._sha_resp(real_hash.encode())
            return _download_resp([content, b""], total=len(content))

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ) as m:
            updater.download_asset(
                self.exe_url,
                dest,
                mirror="https://ghproxy.com/",
                retries=1,
                retry_delay=0,
                sha256_url=self.sha_url,
            )
        urls = [c.args[0].full_url for c in m.call_args_list]
        # 校验和始终直连 GitHub；exe 走镜像
        self.assertEqual(urls[0], self.sha_url)
        self.assertEqual(urls[1], f"https://ghproxy.com/{self.exe_url}")

    def test_sha256_always_direct_not_mirrored(self):
        """M9：sha256 校验和始终直连 GitHub（不走镜像）。

        若校验和与 exe 同镜像，镜像被攻破时可同时篡改两者，强校验形同虚设；
        直连 GitHub 保证校验和的来源独立于下载镜像。
        """
        dest = self._dest("direct_sha.exe")
        content = b"MZ" + b"body"
        real_hash = hashlib.sha256(content).hexdigest()
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return self._sha_resp(f"{real_hash}  a.exe".encode())
            return _download_resp([content, b""], total=len(content))

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=fake_urlopen
        ) as m:
            result = updater.download_asset(
                self.exe_url,
                dest,
                mirror="https://ghproxy.com/",
                retries=1,
                retry_delay=0,
                sha256_url=self.sha_url,
            )
        self.assertEqual(result, str(dest))
        self.assertEqual(dest.read_bytes(), content)
        urls = [c.args[0].full_url for c in m.call_args_list]
        # 校验和直连（不经 ghproxy 前缀）；exe 走镜像
        self.assertEqual(urls[0], self.sha_url)
        self.assertEqual(urls[1], f"https://ghproxy.com/{self.exe_url}")

    def test_parse_sha256_strips_bom(self):
        """带 UTF-8 BOM 的校验和文件也能解析（防手动上传）。"""
        h = "b" * 64
        self.assertEqual(_parse_sha256("\ufeff" + h + "\n"), h)


class BuildReplaceCommandTest(unittest.TestCase):
    def test_script_embedded_utf16(self):
        cmd = updater.build_replace_command(
            r"C:\tmp\new.exe", r"C:\Program Files\CADBatchAssistant.exe"
        )
        self.assertTrue(
            cmd.startswith("powershell -NoProfile -NonInteractive -EncodedCommand ")
        )
        encoded = cmd.rsplit(" ", 1)[-1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("Copy-Item -LiteralPath", script)
        self.assertIn(r"C:\tmp\new.exe", script)
        self.assertIn(r"C:\Program Files\CADBatchAssistant.exe", script)
        self.assertIn("Start-Process", script)

    def test_quote_in_path_escaped(self):
        cmd = updater.build_replace_command(
            r"C:\tmp\a'b.exe", r"C:\Program Files\a'b\app.exe"
        )
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertIn(r"$src = 'C:\tmp\a''b.exe'", script)
        # Copy-Item 与 Start-Process 两处重启路径均转义，无裸单引号
        self.assertIn(r"a''b\app.exe", script)
        self.assertNotIn("a'b", script)

    def test_no_restart(self):
        cmd = updater.build_replace_command("a.exe", "b.exe", restart=False)
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertNotIn("Start-Process", script)

    def test_wait_for_exit_polling_and_retry_in_script(self):
        """M6：脚本含轮询等待退出（60s 上限）+ Copy-Item 重试 + 失败日志。"""
        cmd = updater.build_replace_command(
            r"C:\tmp\new.exe", r"C:\app\CADBatchAssistant.exe"
        )
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        # 轮询等待：文件句柄占用探测 + 60s 截止
        self.assertIn("[System.IO.File]::Open", script)
        self.assertIn("AddSeconds(60)", script)
        # 覆盖重试：循环 + 多次 Copy-Item + 500ms 间隔
        self.assertIn("for ($i = 0; $i -lt 10; $i++)", script)
        self.assertIn("Start-Sleep -Milliseconds 500", script)
        # 失败写日志（静默失败用户无从得知）
        self.assertIn("CADBatchAssistant_update.log", script)
        self.assertIn("Write-Log", script)
        # 旧的固定延时已被移除
        self.assertNotIn("Start-Sleep -Milliseconds 1500", script)

    def test_wait_timeout_failure_writes_log_and_exits(self):
        """M6：等待超时/覆盖失败分支写日志并 exit 1。"""
        cmd = updater.build_replace_command("a.exe", "b.exe")
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertIn("等待原程序退出超时", script)
        self.assertIn("更新失败：覆盖 exe 失败", script)
        self.assertIn("exit 1", script)

    def test_replace_verify_sha256_after_copy(self):
        """启动即崩防护：提供 expected_sha256 时，脚本复制后校验再重启。"""
        h = "c" * 64
        cmd = updater.build_replace_command(
            r"C:\tmp\new.exe", r"C:\app\app.exe", expected_sha256=h
        )
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        # 校验块：Get-FileHash SHA256 与期望比对，失败写日志并 exit 1（不重启）
        self.assertIn("Get-FileHash -LiteralPath $dst -Algorithm SHA256", script)
        self.assertIn(h, script)
        self.assertIn("替换后的 exe 校验失败", script)
        self.assertIn("exit 1", script)

    def test_replace_without_sha256_has_no_verify_block(self):
        """未提供 expected_sha256 时不生成校验块（旧流程不受影响）。"""
        cmd = updater.build_replace_command("a.exe", "b.exe")
        script = base64.b64decode(cmd.rsplit(" ", 1)[-1]).decode("utf-16-le")
        self.assertNotIn("Get-FileHash -LiteralPath $dst", script)


class RunReplaceTest(unittest.TestCase):
    def test_spawns_powershell_without_shell(self):
        """run_replace 以参数列表（而非整串命令）启动 powershell，shell=False。"""
        with mock.patch.object(updater.subprocess, "Popen") as m:
            updater.run_replace(r"C:\a\new.exe", r"C:\a\app.exe")
        m.assert_called_once()
        cmd = m.call_args.args[0]
        self.assertIsInstance(cmd, list)
        self.assertTrue(cmd[0].lower().endswith("powershell.exe"))
        self.assertEqual(cmd[1:4], ["-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertTrue(cmd[4])
        self.assertFalse(m.call_args.kwargs.get("shell"))

    def test_run_replace_forwards_sha256(self):
        """run_replace 把 expected_sha256 透传给替换脚本。"""
        h = "d" * 64
        with mock.patch.object(updater.subprocess, "Popen") as m:
            updater.run_replace(r"C:\a\new.exe", r"C:\a\app.exe", expected_sha256=h)
        cmd = m.call_args.args[0]
        encoded = cmd[cmd.index("-EncodedCommand") + 1]
        script = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn(h, script)


class IsFrozenTest(unittest.TestCase):
    def test_dev_mode_not_frozen(self):
        self.assertFalse(updater.is_frozen())


if __name__ == "__main__":
    unittest.main()
