"""图纸模板库的纯文件操作（不依赖 GUI）。

提供模板库目录定位、枚举、复制与删除；弹窗提示由 gui 层包装
（gui.tk_widgets.upload_template_file / delete_template_file），
以便本模块可独立单测。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from cadbatchassistant.core.app_config import software_dir
from cadbatchassistant.core.filetypes import CAD_SUFFIXES


def templates_dir(category: str) -> Path:
    """模板库目录：软件目录/templates/<category>（如 fill / catalog）。"""
    return software_dir() / "templates" / category


def template_path(category: str, name: str) -> Path:
    """模板库中某个模板的完整路径。"""
    return templates_dir(category) / name


def list_templates(category: str) -> list[str]:
    """返回模板库（category 子目录）中的 .dwg/.dxf 模板文件名（排序）。"""
    d = templates_dir(category)
    if not d.is_dir():
        return []
    return sorted(
        f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in CAD_SUFFIXES
    )


def copy_template(category: str, src: str) -> str:
    """把 dwg/dxf 复制进模板库（category 子目录），返回模板文件名。

    覆盖已存在的同名文件；非法路径/复制失败抛异常由调用方处理。
    """
    d = templates_dir(category)
    d.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(src)
    shutil.copy2(src, d / name)
    return name


def remove_template(category: str, name: str) -> None:
    """删除模板库（category 子目录）中的模板；失败抛 OSError。"""
    (templates_dir(category) / name).unlink()
