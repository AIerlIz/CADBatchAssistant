"""基于 GitHub Release 的在线更新模块。

- check_latest  : 查询 GitHub 最新 Release，返回版本信息或失败原因
- download_asset: 下载更新包（支持镜像前缀），带进度回调
- build_replace_command : 生成"等待退出 → 覆盖 exe → 重启"的 PowerShell 命令
- is_frozen     : 是否打包运行（打包 exe 才启用更新）

镜像约定：download 前缀 = mirror.rstrip("/") + "/" + 原始 URL，如
https://ghproxy.com/https://github.com/... 或 https://github.com/...
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path


# GitHub 仓库与更新包信息（与 .github/workflows/build.yml 发布的资产一致）
GITHUB_REPO = "AIerlIz/CADBatchAssistant"
ASSET_NAME = "CADBatchAssistant.exe"
API_TIMEOUT = 15
USER_AGENT = "CADBatchAssistant-Updater"


class UpdateError(Exception):
    """更新流程失败（网络 / 数据 / 校验），message 可直接展示给用户。"""


def is_frozen() -> bool:
    """是否打包运行（PyInstaller）；开发模式（python main.py）下返回 False。"""
    return bool(getattr(sys, "frozen", False))


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """把 tag（如 v1.2.3 / 1.2.3）解析为 (major, minor, patch)；解析失败返回 None。"""
    text = tag.strip()
    if text.startswith("v"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) < 3:
        return None
    try:
        nums = tuple(int(p) for p in parts[:3])
    except ValueError:
        return None
    return nums  # type: ignore[return-value]


def is_newer(latest: tuple[int, int, int] | None,
             current: tuple[int, int, int] | None) -> bool:
    """latest > current 视为有新版本；任一解析失败返回 False。"""
    if latest is None or current is None:
        return False
    return latest > current


# 配置 key：用户忽略的版本 tag（如 "v1.1.0"）
IGNORE_KEY = "update_ignore"


def is_ignored(latest_tag: str, ignored_tag: str | None) -> bool:
    """最新版本 tag 是否等于用户忽略的版本（相等则不再提示）。"""
    return bool(ignored_tag) and ignored_tag == latest_tag


def ignored_version() -> str | None:
    """读取用户忽略的版本 tag（配置 update_ignore）；无则返回 None。"""
    from cadbatchassistant.common import load_app_config

    v = load_app_config().get(IGNORE_KEY, "")
    return str(v).strip() or None


def set_ignored_version(tag: str) -> None:
    """保存（或传空字符串清除）用户忽略的版本 tag。

    合并写入配置，保留 update_mirror 等其他配置项。
    """
    from cadbatchassistant.common import save_app_config

    save_app_config({IGNORE_KEY: tag})


def _request_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    """GET JSON（带 UA），失败时抛出带用户可读信息的 UpdateError。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise UpdateError(f"服务器返回 {e.code}（{e.reason}）") from e
    except (urllib.error.URLError, socket.timeout, OSError,
            http.client.HTTPException) as e:
        raise UpdateError(f"无法连接 GitHub：{e}") from e
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise UpdateError("更新信息解析失败") from e
    if not isinstance(data, dict):
        raise UpdateError("更新信息格式异常")
    return data


def check_latest(repo: str = GITHUB_REPO,
                 timeout: int = API_TIMEOUT) -> dict:
    """查询 GitHub 最新 Release。

    返回：
    - 成功: {"ok": True, "tag": "v1.1.0", "version": (1,1,0),
             "url": "...", "size": 123456}
    - 失败: {"ok": False, "error": "用户可读原因"}
    """
    try:
        data = _request_json(
            f"https://api.github.com/repos/{repo}/releases/latest", timeout)
    except UpdateError as e:
        return {"ok": False, "error": str(e)}

    tag = str(data.get("tag_name", ""))
    version = parse_version(tag)
    if version is None:
        return {"ok": False, "error": f"无法解析版本号：{tag}"}

    assets = data.get("assets")
    if not isinstance(assets, list):
        assets = []
    url, size = None, None
    for asset in assets:
        if asset.get("name") == ASSET_NAME:
            url = asset.get("browser_download_url")
            size = asset.get("size")
            break
    if not url:
        return {"ok": False, "error": f"最新版本未包含安装包 {ASSET_NAME}"}

    return {"ok": True, "tag": tag, "version": version,
            "url": str(url), "size": int(size) if size else None}


def _mirror_url(url: str, mirror: str | None) -> str:
    """按镜像配置拼接下载地址；未配置镜像时原样返回。"""
    mirror = (mirror or "").strip().rstrip("/")
    if not mirror:
        return url
    return f"{mirror}/{url}"


# progress_cb 抛出的取消消息；重试逻辑须原样放行，不做重试。
# 公开供 UI 层引用，避免取消消息硬编码两处、后续发散。
CANCEL_MSG = "已取消"


def _cleanup(dest: Path) -> None:
    """尽力删除半成品下载文件，失败静默。"""
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        pass


def _download_once(dest: Path, url: str, source: str,
                   progress_cb, size: int | None,
                   expect_exe: bool) -> str:
    """单次下载 + 校验（不做重试）。

    - 网络 / 协议错误（含 IncompleteRead 等 HTTPException）转 UpdateError 并清理；
    - progress_cb 抛出的 UpdateError（用户取消）原样传播，不清理、不重试；
    - 下载完成后校验 size（提供时）与 PE 头（expect_exe 时）。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp, \
                open(dest, "wb") as f:
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
    except (urllib.error.URLError, socket.timeout, OSError,
            http.client.HTTPException) as e:
        _cleanup(dest)
        raise UpdateError(f"{source}下载失败：{e}") from e

    if size is not None:
        actual = dest.stat().st_size
        if actual != size:
            _cleanup(dest)
            raise UpdateError(f"{source}下载文件不完整（{actual} / {size} 字节）")
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
                "请检查网络或更换下载镜像")
    return str(dest)


def download_asset(url: str, dest: str | Path, mirror: str | None = None,
                   progress_cb=None, size: int | None = None,
                   retries: int = 3, retry_delay: float = 1.0,
                   fallback_to_direct: bool = True,
                   expect_exe: bool = True,
                   abort_event: threading.Event | None = None) -> str:
    """分块下载更新包到 dest，返回 dest 路径。

    progress_cb(downloaded, total) 在主线程之外的调用线程执行。
    下载完成后校验：提供 size 时比对实际大小（不符视为不完整）；
    expect_exe 时校验 PE 头（MZ），防止镜像/服务器返回错误页冒充安装包。
    网络错误与校验失败自动重试 retries 次（指数退避，起始 retry_delay 秒）；
    配置了镜像且失败时（fallback_to_direct）自动改用原始 URL 直连再试。
    用户取消（progress_cb 抛 UpdateError(CANCEL_MSG)）或 abort_event 被置位
    （退避等待期间）时立即中止，不重试，直接传播。
    全部尝试失败后抛 UpdateError，消息含已尝试次数与最后错误。
    """
    retries = max(1, int(retries))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
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
                return _download_once(dest, candidate_url, source,
                                      progress_cb, size, expect_exe)
            except UpdateError as e:
                if e.args and e.args[0] == CANCEL_MSG:
                    raise
                last_error = e
                if attempt < retries:
                    delay = retry_delay * (2 ** (attempt - 1))
                    if abort_event is not None:
                        # 可中断退避：取消被置位时立即中止，不等满整个退避时长
                        if abort_event.wait(delay):
                            raise UpdateError(CANCEL_MSG)
                    else:
                        time.sleep(delay)

    total_attempts = len(candidates) * retries
    raise UpdateError(
        f"下载失败：已自动尝试 {total_attempts} 次。最后错误：{last_error}。"
        "请检查网络连接，或更换/清空下载镜像后重试。") from last_error


def build_replace_command(downloaded: str, current_exe: str,
                          restart: bool = True) -> str:
    """生成更新替换命令：等待主进程退出 → 覆盖 exe → 重启。

    使用 PowerShell -EncodedCommand（base64/UTF-16LE 内嵌整段命令），
    路径含中文/空格/单引号也能正确传递。返回完整可执行的命令行字符串。
    """
    src_esc = downloaded.replace("'", "''")
    dst_esc = current_exe.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$src = '{src_esc}'
$dst = '{dst_esc}'
Start-Sleep -Milliseconds 1500
Copy-Item -LiteralPath $src -Destination $dst -Force
if (-not $?) {{ exit 1 }}
"""
    if restart:
        script += f"Start-Process -FilePath '{dst_esc}'\n"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


def run_replace(downloaded: str, current_exe: str, restart: bool = True) -> None:
    """启动替换进程（不等待）；随后应尽快让主进程退出。"""
    subprocess.Popen(
        build_replace_command(downloaded, current_exe, restart),
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def current_exe_path() -> str:
    """当前运行的 exe 路径（打包模式）；开发模式返回 main.py 路径。"""
    return os.path.abspath(sys.executable)
