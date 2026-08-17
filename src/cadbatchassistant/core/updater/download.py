"""更新包下载与强校验（镜像 / 重试 / 取消）。

- _mirror_url / _validate_mirror_scheme : 镜像地址拼接与 HTTPS 强制
- _fetch_sha256 / _parse_sha256 : 校验和获取与解析（始终直连 GitHub）
- download_asset: 分块下载（候选源 = 镜像 + 直连），失败自动重试；
  下载后强制 sha256 校验，另校验 size 与 PE 头作为纵深防御
- _download_once : 单次下载 + 校验（不做重试）
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from cadbatchassistant.core.updater.constants import (
    API_TIMEOUT,
    MAX_RESPONSE_BYTES,
    SHA256_ASSET_NAME,
    USER_AGENT,
    UpdateError,
)

# progress_cb 抛出的取消消息；重试逻辑须原样放行，不做重试。
# 公开供 UI 层引用，避免取消消息硬编码两处、后续发散。
CANCEL_MSG = "已取消"


def _cleanup(dest: Path) -> None:
    """尽力删除半成品下载文件，失败静默。"""
    with contextlib.suppress(OSError):
        dest.unlink(missing_ok=True)


def _mirror_url(url: str, mirror: str | None) -> str:
    """按镜像配置拼接下载地址；未配置镜像时原样返回。"""
    mirror = (mirror or "").strip().rstrip("/")
    if not mirror:
        return url
    return f"{mirror}/{url}"


def _validate_mirror_scheme(mirror: str | None) -> None:
    """M9：拒绝明文 HTTP 镜像（中间人可篡改 exe/校验和），仅允许 HTTPS。

    mirror 为空（未配置）时直接通过——直连 GitHub 本身是 HTTPS。
    """
    mirror = (mirror or "").strip()
    if mirror.lower().startswith("http://"):
        raise UpdateError(
            "下载镜像仅支持 HTTPS（明文 http 会被中间人篡改，已拒绝）。"
            "请改用 https 镜像或清空镜像配置直连 GitHub。"
        )


def _parse_sha256(text: str) -> str:
    """从 .sha256 校验和文件文本中解析 64 位十六进制哈希。

    兼容两种常见格式：
    - "<64位hex>  <文件名>"（sha256sum 输出）
    - 纯 "<64位hex>"（单列）
    解析失败抛 UpdateError。剥离 UTF-8 BOM（\\ufeff）以防手动上传文件带 BOM。
    """
    text = text.lstrip("\ufeff")
    for line in text.splitlines():
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        token = token.lower()
        if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
            return token
    raise UpdateError("校验和文件格式无法解析（未找到 64 位 SHA-256 哈希）")


def _fetch_sha256(sha256_url: str, timeout: int = API_TIMEOUT) -> str:
    """下载 .sha256 校验和文件并解析哈希；失败抛 UpdateError。

    始终直连 GitHub（不走镜像）：校验和是内容哈希，若与 exe 走同一镜像，
    镜像被攻破时 sha256 强校验形同虚设（攻击者可同时篡改两者）。
    """
    req = urllib.request.Request(
        sha256_url, headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise UpdateError("校验和响应过大，已中止读取")  # noqa: TRY301
        return _parse_sha256(body.decode("utf-8", errors="replace"))
    except (
        TimeoutError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        OSError,
        http.client.HTTPException,
        UpdateError,
    ) as e:
        raise UpdateError(f"获取校验和失败：{e}") from e


def _download_once(
    dest: Path,
    url: str,
    source: str,
    progress_cb,
    size: int | None,
    expect_exe: bool,
    expected_sha256: str,
) -> str:
    """单次下载 + 校验（不做重试）。

    - 网络 / 协议错误（含 IncompleteRead 等 HTTPException）转 UpdateError 并清理；
    - progress_cb 抛出的 UpdateError（用户取消）原样传播，不清理、不重试；
    - 下载完成后必做 sha256 强校验（M9），另校验 size（提供时）与
      PE 头（expect_exe 时）作为纵深防御。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with (
            urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp,
            open(dest, "wb") as f,
        ):
            total = int(resp.headers.get("Content-Length") or 0)
            if progress_cb is not None:
                progress_cb(0, total)
            downloaded = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb is not None:
                    progress_cb(downloaded, total)
    except UpdateError:
        _cleanup(dest)  # 用户取消：清理半成品后原样传播，不重试
        raise
    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        http.client.HTTPException,
    ) as e:
        _cleanup(dest)
        raise UpdateError(f"{source}下载失败：{e}") from e

    if size is not None:
        actual = dest.stat().st_size
        if actual != size:
            _cleanup(dest)
            raise UpdateError(f"{source}下载文件不完整（{actual} / {size} 字节）")
    # M9：sha256 强校验——镜像/服务器内容被篡改（即使凑齐 size 与 MZ 头）也会被拦截
    hasher = hashlib.sha256()
    try:
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                hasher.update(chunk)
    except OSError as e:
        _cleanup(dest)
        raise UpdateError(f"读取下载文件失败：{e}") from e
    if hasher.hexdigest() != expected_sha256.lower():
        _cleanup(dest)
        raise UpdateError(
            f"{source}下载文件校验失败（SHA-256 不匹配），"
            "文件可能被篡改，请检查网络或更换下载镜像"
        )
    if expect_exe:
        try:
            with open(dest, "rb") as f:
                is_pe = f.read(2) == b"MZ"
        except OSError as e:
            _cleanup(dest)
            raise UpdateError(f"读取下载文件失败：{e}") from e
        if not is_pe:
            _cleanup(dest)
            raise UpdateError(
                f"{source}返回的内容不是安装包（可能是错误页面），"
                "请检查网络或更换下载镜像"
            )
    return str(dest)


def download_asset(
    url: str,
    dest: str | Path,
    mirror: str | None = None,
    progress_cb=None,
    size: int | None = None,
    retries: int = 3,
    retry_delay: float = 1.0,
    fallback_to_direct: bool = True,
    expect_exe: bool = True,
    abort_event: threading.Event | None = None,
    sha256_url: str | None = None,
) -> str:
    """分块下载更新包到 dest，返回 dest 路径。

    progress_cb(downloaded, total) 在主线程之外的调用线程执行。
    下载强校验（M9）：sha256_url 必填，先获取校验和（始终直连 GitHub，
    不走镜像，确保镜像被攻破时强校验仍有效），下载后校验 SHA-256
    （内容被篡改即失败），另校验 size（提供时）与 PE 头（expect_exe 时）。
    缺少 sha256_url 或校验和获取失败一律视为异常中断，不做弱校验回退。
    mirror 为明文 http:// 时直接拒绝。
    网络错误与校验失败自动重试 retries 次（指数退避，起始 retry_delay 秒）；
    配置了镜像且失败时（fallback_to_direct）自动改用原始 URL 直连再试。
    用户取消（progress_cb 抛 UpdateError(CANCEL_MSG)）或 abort_event 被置位
    （退避等待期间）时立即中止，不重试，直接传播。
    全部尝试失败后抛 UpdateError，消息含已尝试次数与最后错误。
    """
    retries = max(1, int(retries))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _validate_mirror_scheme(mirror)
    if not sha256_url:
        raise UpdateError(
            f"缺少 sha256 校验和资产地址（{SHA256_ASSET_NAME}），无法安全更新"
        )

    # M9：取 sha256 校验和——始终直连 GitHub（不走镜像），确保镜像被攻破时
    # 强校验仍有效；获取失败视为异常中断，不做弱校验回退。
    expected_sha256 = _fetch_sha256(sha256_url)

    direct_url = url
    mirror_url = _mirror_url(url, mirror)
    candidates: list[tuple[str, str]] = []  # (url, 来源描述)
    if mirror_url != direct_url:
        candidates.append((mirror_url, "镜像"))
    if mirror_url == direct_url or fallback_to_direct:
        candidates.append((direct_url, "直连 GitHub"))

    last_error: UpdateError | None = None
    for candidate_url, source in candidates:
        for attempt in range(1, retries + 1):
            try:
                return _download_once(
                    dest,
                    candidate_url,
                    source,
                    progress_cb,
                    size,
                    expect_exe,
                    expected_sha256,
                )
            except UpdateError as e:
                if e.args and e.args[0] == CANCEL_MSG:
                    raise
                last_error = e
                if attempt < retries:
                    delay = retry_delay * (2 ** (attempt - 1))
                    if abort_event is not None:
                        # 可中断退避：取消被置位时立即中止，不等满整个退避时长
                        if abort_event.wait(delay):
                            raise UpdateError(CANCEL_MSG) from None
                    else:
                        time.sleep(delay)

    total_attempts = len(candidates) * retries
    raise UpdateError(
        f"下载失败：已自动尝试 {total_attempts} 次。最后错误：{last_error}。"
        "请检查网络连接，或更换/清空下载镜像后重试。"
    ) from last_error
