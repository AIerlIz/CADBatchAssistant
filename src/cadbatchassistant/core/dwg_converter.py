"""ODA File Converter 集成模块。

负责 DWG <-> DXF 转换：
- 自动探测 ODAFileConverter.exe（常见安装路径 + 环境变量 ODA_PATH）
- 调用其命令行接口执行批量转换（subprocess 列表传参，含空格路径安全）
- 转换完成后轮询输出目录确认产物生成

ODAFileConverter 命令行格式：
    ODAFileConverter <InputFolder> <OutputFolder> <OutputVersion> <OutputType> <Recurse> <Audit> [InputFilter] [OutputFilter]
OutputVersion: ACAD9|ACAD10|ACAD12|ACAD13|ACAD14|ACAD2000|ACAD2004|ACAD2007|ACAD2010|ACAD2013|ACAD2018
OutputType: DWG|DXF|DXF_B|DXF_A
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_OUT_VERSION = "ACAD2018"

_CANDIDATE_GLOBS = [
    r"%ProgramFiles%\ODA\ODAFileConverter*\ODAFileConverter.exe",
    r"%ProgramFiles(x86)%\ODA\ODAFileConverter*\ODAFileConverter.exe",
    r"%LOCALAPPDATA%\Programs\ODA\ODAFileConverter*\ODAFileConverter.exe",
    r"%LOCALAPPDATA%\ODA\ODAFileConverter*\ODAFileConverter.exe",
    r"%ProgramFiles%\ODA\ODAFileConverter.exe\ODAFileConverter.exe",
]


def find_oda_converter() -> Path | None:
    """探测 ODAFileConverter.exe 的路径；未找到返回 None。"""
    env_path = os.environ.get("ODA_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p
        if (p / "ODAFileConverter.exe").is_file():
            return p / "ODAFileConverter.exe"

    seen: set[str] = set()
    for pattern in _CANDIDATE_GLOBS:
        expanded = os.path.expandvars(pattern)
        for match in glob.glob(expanded):
            if match.lower() in seen:
                continue
            seen.add(match.lower())
            if os.path.isfile(match):
                return Path(match)
    return None


class ODAError(RuntimeError):
    """ODA 转换失败。"""


def require_oda_for_dwg(has_dwg: bool, oda: str) -> str | None:
    """需处理 DWG 但未配置有效 ODAFileConverter 时，返回错误文案；否则返回 None。

    纯文本、不依赖 GUI，供各面板启动前校验（错误文案直接弹窗展示）；
    has_dwg 为 False（纯 DXF 流程）时一律通过。
    """
    if not has_dwg:
        return None
    oda = (oda or "").strip()
    if not oda or not Path(oda).is_file():
        return ("输入包含 DWG 文件，未找到 ODAFileConverter.exe，"
                "请安装或在「设置」页配置其路径。（仅 DXF 文件无需 ODA）")
    return None


def convert_batch(
    oda_exe: str | Path,
    in_dir: str | Path,
    out_dir: str | Path,
    out_version: str = DEFAULT_OUT_VERSION,
    out_type: str = "DXF",
    recursive: int = 0,
    timeout: float = 900,
    input_filter: str | None = None,
) -> None:
    """调用 ODAFileConverter 批量转换，阻塞直至进程退出。

    参数不合法（找不到 exe / 目录不存在 / 版本或类型非法）直接抛 ODAError。
    """
    oda_exe = Path(oda_exe)
    if not oda_exe.is_file():
        raise ODAError(f"ODAFileConverter 不存在: {oda_exe}")

    in_dir = Path(in_dir)
    out_dir = Path(out_dir)
    if not in_dir.is_dir():
        raise ODAError(f"输入目录不存在: {in_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    valid_versions = {
        "ACAD9", "ACAD10", "ACAD12", "ACAD13", "ACAD14",
        "ACAD2000", "ACAD2004", "ACAD2007", "ACAD2010", "ACAD2013", "ACAD2018",
    }
    if out_version not in valid_versions:
        raise ODAError(f"不支持的输出版本: {out_version}")
    if out_type not in {"DWG", "DXF", "DXF_B", "DXF_A"}:
        raise ODAError(f"不支持的输出类型: {out_type}")

    cmd = [
        str(oda_exe),
        str(in_dir),
        str(out_dir),
        out_version,
        out_type,
        str(int(recursive)),
        "1",  # audit/fix
    ]
    if input_filter:
        cmd.append(input_filter)

    # Windows 下隐藏 ODAFileConverter 的窗口，后台静默转换
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            startupinfo=startupinfo, creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as ex:
        raise ODAError(f"ODA 转换超时（{timeout}s）: {ex}") from ex
    except OSError as ex:
        raise ODAError(f"无法启动 ODAFileConverter: {ex}") from ex

    if proc.returncode not in (0, None):
        raise ODAError(f"ODA 转换失败，退出码 {proc.returncode}")


def wait_for_outputs(
    out_dir: str | Path,
    expected_names: set[str],
    timeout: float = 120,
    interval: float = 2,
) -> None:
    """轮询输出目录，直到全部期望文件存在或超时。"""
    out_dir = Path(out_dir)
    deadline = time.time() + timeout
    missing: set[str] = set(expected_names)
    while time.time() < deadline:
        missing = {
            name
            for name in expected_names
            if not (out_dir / name).is_file()
        }
        if not missing:
            return
        time.sleep(interval)
    raise ODAError(f"等待 ODA 输出超时，仍缺失 {len(missing)} 个文件: {sorted(missing)[:5]} ...")


def convert_template_to_dxf(
    template: str | Path,
    workdir: str | Path,
    oda: str | Path = "",
    out_version: str = DEFAULT_OUT_VERSION,
    subdir: str = "_tmpl_dxf",
) -> str:
    """把图纸模板（.dwg/.dxf）转成 DXF，返回模板 DXF 路径。

    模板复制到 workdir 根目录（保持原名）。
    - .dwg：经 ODA 转 DXF，产物输出到 workdir/subdir（ODA 要求输入输出
      目录不同，故用独立子目录承接）；oda 为空时自动探测，仍未找到抛 ODAError。
    - .dxf：直接返回复制后的路径。
    """
    template = Path(template)
    workdir = Path(workdir)
    dst = workdir / template.name
    shutil.copy2(template, dst)
    if template.suffix.lower() != ".dwg":
        return str(dst)
    oda = (oda or "").strip()
    if not oda or not Path(oda).is_file():
        found = find_oda_converter()
        oda = str(found) if found else ""
    out_dir = workdir / subdir
    out_dir.mkdir(parents=True, exist_ok=True)
    convert_dwg_batch_to_dxf(oda, workdir, out_dir, [template.name], out_version)
    return str(out_dir / (template.stem + ".dxf"))


def convert_dwg_batch_to_dxf(
    oda_exe: str | Path,
    in_dir: str | Path,
    out_dir: str | Path,
    dwg_names: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
    timeout: float = 900,
) -> None:
    """把一批 DWG 转成同名的 DXF（ASCII），并确认全部产物生成。

    timeout 同时用于 ODA 进程等待与产物轮询，避免大图转换时
    产物等待（旧默认 120s）早于进程超时（900s）误报失败。
    """
    convert_batch(oda_exe, in_dir, out_dir, out_version, "DXF", 0, timeout,
                  input_filter="*.DWG")
    expected = {Path(n).stem + ".dxf" for n in dwg_names}
    wait_for_outputs(out_dir, expected, timeout=timeout)


def convert_dxf_batch_to_dwg(
    oda_exe: str | Path,
    in_dir: str | Path,
    out_dir: str | Path,
    dxf_names: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
    timeout: float = 900,
) -> None:
    """把一批 DXF 转成同名的 DWG，并确认全部产物生成。

    timeout 同时用于 ODA 进程等待与产物轮询，避免大图转换时
    产物等待（旧默认 120s）早于进程超时（900s）误报失败。
    """
    convert_batch(oda_exe, in_dir, out_dir, out_version, "DWG", 0, timeout,
                  input_filter="*.DXF")
    expected = {Path(n).stem + ".dwg" for n in dxf_names}
    wait_for_outputs(out_dir, expected, timeout=timeout)
