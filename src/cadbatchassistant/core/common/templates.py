"""图纸模板库的纯文件操作（不依赖 GUI）。

模板库条目 = 占位符 meta JSON（`<模板名>.json`，如 `图框.dwg.json`）：
上传时只把解析出的占位符配置写入 JSON，不保存原始 dwg/dxf 文件，
运行时全部只读 meta。弹窗提示由 gui 层包装
（gui.tk_widgets.upload_template_file / delete_template_file），
以便本模块可独立单测。
"""

from __future__ import annotations

import json
from pathlib import Path

from cadbatchassistant.core.common.app_config import software_dir


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

    条目取 meta JSON 的 source（原模板文件名，如 `图框.dwg`）；
    模板库只存占位符配置 JSON，不保存原文件。
    """
    d = templates_dir(category)
    if not d.is_dir():
        return []
    names: list[str] = []
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() == ".json":
            names.append(_meta_source(f))
    return sorted(set(names))


def _validate_template_name(name: str) -> None:
    """校验模板名可安全拼接到模板库目录；非法（越界/含分隔符）抛 ValueError。

    模板名可能来自用户篡改的 meta JSON 的 source 字段（模板库目录本地可写），
    拼接前必须校验，防止删除操作逃出模板库目录（路径穿越删任意文件）。
    """
    if not name or name in (".", "..") or any(ch in name for ch in "/\\"):
        raise ValueError(f"非法的模板名：{name!r}")


def remove_template(category: str, name: str) -> None:
    """删除模板库（category 子目录）中的模板条目（meta JSON）。

    条目不存在时抛 FileNotFoundError（由调用方处理）；name 含路径分隔符或
    越界（被篡改的 source 字段）时抛 ValueError，不做任何删除。
    """
    _validate_template_name(name)
    d = templates_dir(category)
    # meta：优先 <name>.json；source 与文件名脱钩时按枚举名（source）扫目录匹配
    meta: Path | None = d / (name + ".json")
    if meta is not None and not meta.is_file():
        meta = next(
            (f for f in d.glob("*.json") if f.is_file() and _meta_source(f) == name),
            None,
        )
    if meta is None:
        raise FileNotFoundError(f"模板不存在: {name}")
    meta.unlink()
