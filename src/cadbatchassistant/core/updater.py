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
import contextlib
import hashlib
import http.client
import json
import os
import shutil
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
# 随 Release 一并发布的 sha256 校验和资产（名 = 安装包名 + ".sha256"）
SHA256_ASSET_NAME = ASSET_NAME + ".sha256"
API_TIMEOUT = 15
USER_AGENT = "CADBatchAssistant-Updater"
# L6：响应体大小上限（10MB），防止恶意服务器返回超大 body 耗尽内存
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class UpdateError(Exception):
    """更新流程失败（网络 / 数据 / 校验），message 可直接展示给用户。"""


def is_frozen() -> bool:
    """是否打包运行（PyInstaller）；开发模式（python main.py）下返回 False。"""
    return bool(getattr(sys, "frozen", False))


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """把 tag（如 v1.2.3 / 1.2.3）解析为 (major, minor, patch)；解析失败返回 None。"""
    text = tag.strip()
    text = text.removeprefix("v")
    parts = text.split(".")
    if len(parts) < 3:
        return None
    try:
        nums = tuple(int(p) for p in parts[:3])
    except ValueError:
        return None
    return nums  # type: ignore[return-value]


def is_newer(
    latest: tuple[int, int, int] | None, current: tuple[int, int, int] | None
) -> bool:
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
    from cadbatchassistant.core.app_config import load_app_config

    v = load_app_config().get(IGNORE_KEY, "")
    return str(v).strip() or None


def set_ignored_version(tag: str) -> None:
    """保存（或传空字符串清除）用户忽略的版本 tag。

    合并写入配置，保留 update_mirror 等其他配置项。
    """
    from cadbatchassistant.core.app_config import save_app_config

    save_app_config({IGNORE_KEY: tag})


def _request_json(url: str, timeout: int = API_TIMEOUT) -> dict:
    """GET JSON（带 UA），失败时抛出带用户可读信息的 UpdateError。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise UpdateError("更新信息响应过大，已中止读取")  # noqa: TRY301
    except UpdateError:
        raise
    except urllib.error.HTTPError as e:
        raise UpdateError(f"服务器返回 {e.code}（{e.reason}）") from e
    except (
        TimeoutError,
        urllib.error.URLError,
        OSError,
        http.client.HTTPException,
    ) as e:
        raise UpdateError(f"无法连接 GitHub：{e}") from e
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise UpdateError("更新信息解析失败") from e
    if not isinstance(data, dict):
        raise UpdateError("更新信息格式异常")
    return data


def check_latest(repo: str = GITHUB_REPO, timeout: int = API_TIMEOUT) -> dict:
    """查询 GitHub 最新 Release。

    返回：
    - 成功: {"ok": True, "tag": "v1.1.0", "version": (1,1,0),
             "url": "...", "size": 123456}
    - 失败: {"ok": False, "error": "用户可读原因"}
    """
    try:
        data = _request_json(
            f"https://api.github.com/repos/{repo}/releases/latest", timeout
        )
    except UpdateError as e:
        return {"ok": False, "error": str(e)}

    tag = str(data.get("tag_name", ""))
    version = parse_version(tag)
    if version is None:
        return {"ok": False, "error": f"无法解析版本号：{tag}"}

    assets = data.get("assets")
    if not isinstance(assets, list):
        assets = []
    url, size, sha256_url = None, None, None
    for asset in assets:
        name = asset.get("name")
        if name == ASSET_NAME:
            url = asset.get("browser_download_url")
            size = asset.get("size")
        elif name == SHA256_ASSET_NAME:
            sha256_url = asset.get("browser_download_url")
        if url and sha256_url:  # 两者都命中即可提前结束
            break
    if not url:
        return {"ok": False, "error": f"最新版本未包含安装包 {ASSET_NAME}"}
    if not sha256_url:
        # 更新强制 sha256 强校验：缺少校验和资产的 Release 视为不可安全更新
        return {"ok": False, "error": f"最新版本未包含校验和资产 {SHA256_ASSET_NAME}"}

    return {
        "ok": True,
        "tag": tag,
        "version": version,
        "url": str(url),
        "size": int(size) if size else None,
        "sha256_url": str(sha256_url),
    }


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


def _fetch_sha256(
    sha256_url: str, timeout: int = API_TIMEOUT
) -> str:
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


# progress_cb 抛出的取消消息；重试逻辑须原样放行，不做重试。
# 公开供 UI 层引用，避免取消消息硬编码两处、后续发散。
CANCEL_MSG = "已取消"


def _cleanup(dest: Path) -> None:
    """尽力删除半成品下载文件，失败静默。"""
    with contextlib.suppress(OSError):
        dest.unlink(missing_ok=True)


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


def _replace_script(
    downloaded: str,
    current_exe: str,
    restart: bool,
    expected_sha256: str | None,
) -> str:
    """生成 PowerShell 替换脚本（不含外壳调用层）。

    供 build_replace_command（字符串形式）与 run_replace（列表传参）共用，
    避免两处维护同一段脚本。
    """
    src_esc = downloaded.replace("'", "''")
    dst_esc = current_exe.replace("'", "''")
    log_esc = os.path.join(
        os.environ.get("TEMP", os.environ.get("TMP", ".")),
        "CADBatchAssistant_update.log",
    ).replace("'", "''")
    verify_ps = ""
    if expected_sha256:
        expected_ps = expected_sha256.lower()
        verify_ps = f"""
# 3) 校验落盘 exe 的 SHA-256，防止复制环节产生损坏文件后仍重启
$expected = '{expected_ps}'
$actual = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $expected) {{
    Write-Log ('更新失败：替换后的 exe 校验失败（SHA-256 不匹配），已停止重启。')
    exit 1
}}
"""
    script = f"""
$ErrorActionPreference = 'Stop'
$src = '{src_esc}'
$dst = '{dst_esc}'
$log = '{log_esc}'
function Write-Log($msg) {{
    try {{ Add-Content -LiteralPath $log -Value $msg -Encoding UTF8 }} catch {{}}
}}
# 1) 轮询等待目标 exe 不再被占用（主进程退出释放文件句柄），最长 60s
$deadline = (Get-Date).AddSeconds(60)
$ready = $false
while ((Get-Date) -lt $deadline) {{
    try {{
        $fs = [System.IO.File]::Open($dst, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        $ready = $true
        break
    }} catch {{
        Start-Sleep -Milliseconds 300
    }}
}}
if (-not $ready) {{
    Write-Log '更新失败：等待原程序退出超时（60s），未覆盖 exe。'
    exit 1
}}
# 2) 覆盖 exe（重试最多 10 次，应对句柄延迟释放等瞬时占用）
$copied = $false
$lastErr = $null
for ($i = 0; $i -lt 10; $i++) {{
    try {{
        Copy-Item -LiteralPath $src -Destination $dst -Force
        $copied = $true
        break
    }} catch {{
        $lastErr = $_.Exception.Message
        Start-Sleep -Milliseconds 500
    }}
}}
if (-not $copied) {{
    Write-Log ('更新失败：覆盖 exe 失败：' + $lastErr)
    exit 1
}}
{verify_ps}Write-Log '更新成功：exe 已替换。'
"""
    if restart:
        script += f"Start-Process -FilePath '{dst_esc}'\n"
    return script


def build_replace_command(
    downloaded: str,
    current_exe: str,
    restart: bool = True,
    expected_sha256: str | None = None,
) -> str:
    """生成更新替换命令：等待主进程退出 → 覆盖 exe → 校验 → 重启。

    使用 PowerShell -EncodedCommand（base64/UTF-16LE 内嵌整段命令），
    路径含中文/空格/单引号也能正确传递。返回完整可执行的命令行字符串。

    M6 加固（替换旧版固定 Start-Sleep 1500ms + 单次 Copy-Item 的竞态）：
    - 轮询探测目标 exe 是否仍被占用（文件句柄不可写 = 主进程未退出），
      最长等待 60 秒，避免主进程退出慢于固定延时导致覆盖失败；
    - Copy-Item 失败自动重试（最多 10 次 × 500ms）；
    - 复制后校验目标 exe 的 SHA-256（expected_sha256 提供时）：
      落盘/复制环节损坏的文件不会被重启（否则就是"升级后启动即崩"），
      校验失败写日志并 exit 1，保留旧 exe 由用户手动处理；
    - 最终仍失败时把原因写入 %TEMP%\\CADBatchAssistant_update.log 供用户查看
      （PowerShell 窗口默认隐藏，静默失败用户无从得知）。
    """
    script = _replace_script(downloaded, current_exe, restart, expected_sha256)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


def run_replace(
    downloaded: str,
    current_exe: str,
    restart: bool = True,
    expected_sha256: str | None = None,
) -> None:
    """启动替换进程（不等待）；随后应尽快让主进程退出。

    expected_sha256 : 下载 exe 的 SHA-256（可选）。提供时替换脚本在
        覆盖后、重启前先校验落盘文件，防止复制环节损坏导致"升级后启动即崩"
        （如 Failed to load Python DLL）。
    """
    script = _replace_script(downloaded, current_exe, restart, expected_sha256)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # 列表传参：shell=False 下传整串字符串依赖 Windows CreateProcess 的
    # 命令行解析（行为不规范），显式拆分为参数列表更稳妥；
    # powershell 解析为绝对路径（shutil.which），避免依赖 PATH 查找。
    powershell = shutil.which("powershell") or "powershell"
    subprocess.Popen(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def current_exe_path() -> str:
    """当前运行的 exe 路径（打包模式）；开发模式返回 main.py 路径。"""
    return os.path.abspath(sys.executable)
