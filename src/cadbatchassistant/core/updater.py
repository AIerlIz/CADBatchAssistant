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
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from cadbatchassistant import __version__

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


def _request_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    """GET JSON（带 UA），失败时抛出带用户可读信息的 UpdateError。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise UpdateError(f"服务器返回 {e.code}（{e.reason}）") from e
    except (urllib.error.URLError, socket.timeout, OSError) as e:
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


def download_asset(url: str, dest: str | Path, mirror: str | None = None,
                   progress_cb=None, size: int | None = None) -> str:
    """分块下载更新包到 dest，返回 dest 路径。

    progress_cb(downloaded, total) 在主线程之外的调用线程执行；
    下载完成后若提供了期望 size 则做大小校验，不符抛 UpdateError。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final_url = _mirror_url(url, mirror)
    req = urllib.request.Request(final_url, headers={"User-Agent": USER_AGENT})
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
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(f"下载失败：{e}") from e

    if size is not None:
        actual = dest.stat().st_size
        if actual != size:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise UpdateError(f"下载文件不完整（{actual} / {size} 字节）")
    return str(dest)


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
