"""DWG 批处理工作流：统一「DWG→DXF 批（供处理）→ 处理后写回」编排。

「改字」「填表」两条流程都需要把一批图纸统一成 DXF 处理，再按源类型写回
（DWG 源转回 DWG，DXF 源保持 DXF）；「目录」流程只用到前一半（单向转换）。
本模块把这套编排抽成两个函数供复用，转换细节委托给 Converter 实现
（默认 ODA，经 get_converter 获取，可切换）。

文件名约定：dwg_files / dxf_files 均为含扩展名的文件名（如 "a.DWG" / "b.dxf"），
与 dwg_converter 的转换接口一致；调用方需自行保证扩展名与文件实际类型匹配。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cadbatchassistant.core.dwg_converter import (
    DEFAULT_OUT_VERSION,
    Converter,
)


def stage_dxf_batch(
    converter: Converter,
    oda_exe: str | Path,
    in_dir: str | Path,
    dxf_out: str | Path,
    dwg_files: list[str],
    dxf_files: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
) -> None:
    """把 DWG（经转换）与 DXF（直接复制）统一成 DXF 批到 dxf_out。

    in_dir   : 输入目录（含源图纸）
    dxf_out  : DXF 批输出目录（DWG 转换产物 + DXF 副本，供后续处理）
    dwg_files: 需要转换的 DWG 文件名列表（含扩展名）
    dxf_files: 需要复制的 DXF 文件名列表（含扩展名）
    """
    Path(dxf_out).mkdir(parents=True, exist_ok=True)
    if dwg_files:
        converter.dwg_to_dxf(oda_exe, in_dir, dxf_out, dwg_files, out_version)
    for name in dxf_files:
        shutil.copy2(Path(in_dir) / name, Path(dxf_out) / name)


def write_back_dxf_batch(
    converter: Converter,
    oda_exe: str | Path,
    processed_dir: str | Path,
    out_dir: str | Path,
    dwg_files: list[str],
    dxf_files: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
    skip: set[str] | frozenset[str] | None = None,
) -> None:
    """把处理后的 DXF 写回输出目录：DWG 源转回 DWG，DXF 源直接复制。

    processed_dir: 处理后的 DXF 批目录（与 stage_dxf_batch 的 dxf_out 对应）
    out_dir       : 输出目录（自动创建）
    skip          : 不写回的图纸名集合（无扩展名 stem，如 "A1"；转换失败/
                    跳过的图纸）。dwg_files/dxf_files 含扩展名，这里按
                    Path(name).stem 比较，与 fill_all 返回的 failed/skipped
                    （stem）一致。
    """
    skip = set(skip or ())
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ok_dwg = [n for n in dwg_files if Path(n).stem not in skip]
    if ok_dwg:
        converter.dxf_to_dwg(oda_exe, processed_dir, out_dir,
                             [Path(n).stem + ".dxf" for n in ok_dwg],
                             out_version)
    for name in dxf_files:
        if Path(name).stem in skip:
            continue
        shutil.copy2(Path(processed_dir) / name, Path(out_dir) / name)
