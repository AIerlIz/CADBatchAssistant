"""输入文件公共工具：重名检测 + 复制到临时目录（fill/catalog 两条 pipeline 共用）。

- check_duplicate_names : 大小写不敏感重名检测（复制到同一临时目录会互相覆盖）
- stage_inputs          : 把文件列表复制到临时输入目录，返回 (目录, 去扩展名列表)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path


def check_duplicate_names(paths: Sequence[str | Path]) -> None:
    """检测输入文件是否重名（大小写不敏感）；重名时抛 ValueError。

    跨目录同名文件复制到同一临时目录会互相覆盖，必须在复制前终止，
    否则后复制的文件覆盖先复制的，导致漏处理或产物错误。
    """
    name_map: dict[str, str] = {}
    for p in paths:
        key = os.path.normcase(os.path.basename(str(p)))
        if key in name_map:
            raise ValueError(
                "输入文件重名（复制到临时目录会互相覆盖，请重命名后重试）："
                f"{name_map[key]} 与 {p}"
            )
        name_map[key] = str(p)


def stage_inputs(
    files: Sequence[str | Path],
    workdir: str | Path | None = None,
    prefix: str = "cad_inputs_",
) -> tuple[str, list[str]]:
    """把文件列表复制到临时输入目录，返回 (输入目录, 去扩展名文件名列表)。

    先做重名检测（check_duplicate_names），通过后复制到
    workdir/inputs 子目录（workdir 为 None 时自动创建临时目录，
    由调用方负责最终清理，通常作为 pipeline 的 workdir 参数传入，
    由 pipeline 的 finally 一并清理）。
    """
    if not files:
        raise ValueError("未选择任何图纸文件")
    check_duplicate_names(files)
    tmp = workdir or tempfile.mkdtemp(prefix=prefix)
    before_dir = os.path.join(tmp, "inputs")
    os.makedirs(before_dir, exist_ok=True)
    stems: list[str] = []
    for f in files:
        name = os.path.basename(str(f))
        shutil.copy2(str(f), os.path.join(before_dir, name))
        stems.append(os.path.splitext(name)[0])
    return before_dir, stems
