"""exe 替换安装（PowerShell 脚本生成与启动）。

- _replace_script : 生成替换脚本（不含外壳调用层），等待退出 → 覆盖 → 校验 → 重启
- build_replace_command : 生成完整命令行（-EncodedCommand 内嵌脚本）
- run_replace          : 启动替换进程（不等待）
- current_exe_path     : 当前 exe / main.py 路径
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys


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
