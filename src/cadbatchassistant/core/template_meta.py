"""模板占位符 meta 的通用存取（目录助手 / 填表助手共用）。

每个图纸模板文件同目录伴生同名 `.json`（如 `xxx.dwg.json`），由各助手在
上传时一次性提取占位符信息写入；GUI 运行时优先只读该 JSON，不再重复把
模板转 DXF 解析。CLI / selftest 等直接传模板路径的场景在 meta 缺失时
现场解析（由各 pipeline 兜底）。

- meta_path_for(template_path) : 伴生 JSON 路径（同目录、同名 + .json）
- save_template_meta(template_path, payload) : 包装写入 version/source
- load_template_meta(template_path) -> dict | None :
  缺失 / JSON 损坏 / version 不符 → None（静默容错，由调用方决定报错）
- remove_template_meta(template_path) : 删除伴生 JSON（不存在时静默）
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

META_VERSION = 1


def meta_path_for(template_path: str | Path) -> Path:
    """模板伴生 meta JSON 路径：同目录、同名 + `.json`。"""
    p = Path(str(template_path))
    return p.with_name(p.name + ".json")


def save_template_meta(template_path: str | Path, payload: dict) -> Path:
    """把占位符载荷写入伴生 JSON（含 version/source），返回 meta 路径。"""
    p = Path(str(template_path))
    data = {"version": META_VERSION, "source": p.name, **payload}
    out = meta_path_for(p)
    out.parent.mkdir(parents=True, exist_ok=True)  # 模板库目录可能尚不存在
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_template_meta(template_path: str | Path) -> dict | None:
    """读取伴生 JSON；缺失 / JSON 损坏 / version 不符 → None。"""
    out = meta_path_for(template_path)
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != META_VERSION:
        return None
    return data


def remove_template_meta(template_path: str | Path) -> None:
    """删除伴生 JSON；不存在时静默（幂等）。"""
    with contextlib.suppress(FileNotFoundError):
        meta_path_for(template_path).unlink()
