"""图纸模板库的纯文件操作（不依赖 GUI）。

模板库条目 = 占位符 meta JSON（`<模板名>.json`，如 `图框.dwg.json`）：
上传时只把解析出的占位符配置写入 JSON，不保存原始 dwg/dxf 文件，
运行时全部只读 meta。历史版本入库的原文件（.dwg/.dxf 无对应 meta）
仍可枚举与删除（兼容旧库）。弹窗提示由 gui 层包装
（gui.tk_widgets.upload_template_file / delete_template_file），
以便本模块可独立单测。
"""

from __future__ import annotations

import json
from pathlib import Path

from cadbatchassistant.core.app_config import software_dir
from cadbatchassistant.core.filetypes import CAD_SUFFIXES


def templates_dir(category: str) -> Path:
    """模板库目录：软件目录/templates/<category>（如 fill / catalog）。"""
    return software_dir() / "templates" / category


def template_path(category: str, name: str) -> Path:
    """模板库中某个模板的完整路径（虚拟：原文件不入库，仅用于 meta 定位）。"""
    return templates_dir(category) / name


def meta_file_for(category: str, name: str) -> Path:
    """模板库中某个模板的占位符 meta JSON 路径（`<name>.json`）。"""
    return templates_dir(category) / (name + ".json")


def _meta_source(f: Path) -> str:
    """meta JSON 的枚举名：优先 source 字段（原模板文件名），缺省回退文件名。"""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    src = data.get("source") if isinstance(data, dict) else None
    return src if isinstance(src, str) and src else f.name[: -len(".json")]


def list_templates(category: str) -> list[str]:
    """返回模板库（category 子目录）中的模板名（排序、去重）。

    条目优先取 meta JSON 的 source（原模板文件名，如 `图框.dwg`）；
    历史版本直接入库的 .dwg/.dxf 原文件（无对应 meta）也一并列出。
    """
    d = templates_dir(category)
    if not d.is_dir():
        return []
    names: list[str] = []
    for f in d.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() == ".json":
            names.append(_meta_source(f))
        elif f.suffix.lower() in CAD_SUFFIXES:
            names.append(f.name)
    return sorted(set(names))


def remove_template(category: str, name: str) -> None:
    """删除模板库（category 子目录）中的模板条目（meta JSON + 同名遗留原文件）。

    条目不存在时抛 FileNotFoundError（由调用方处理）。
    """
    d = templates_dir(category)
    removed = False
    # meta：优先 <name>.json；source 与文件名脱钩时按枚举名（source）扫目录匹配
    meta: Path | None = d / (name + ".json")
    if meta is not None and not meta.is_file():
        meta = next(
            (f for f in d.glob("*.json") if f.is_file() and _meta_source(f) == name),
            None,
        )
    if meta is not None:
        meta.unlink()
        removed = True
    legacy = d / name
    if legacy.is_file() and legacy.suffix.lower() in CAD_SUFFIXES:
        legacy.unlink()
        removed = True
    if not removed:
        raise FileNotFoundError(f"模板不存在: {name}")
