"""图纸模板库的纯文件操作（不依赖 GUI）。

模板库条目 = 占位符 meta JSON（`<模板名>.json`，如 `图框.dwg.json`）：
上传时只把解析出的占位符配置写入 JSON，不保存原始 dwg/dxf 文件，
运行时全部只读 meta。弹窗提示由 gui 层包装
（gui.tk_widgets.upload_template_file / delete_template_file /
edit_template_file），以便本模块可独立单测。

- list_templates / remove_template : 枚举与删除模板条目
- load_template_json / save_template_json : 读写完整库内 meta dict
- TEMPLATE_EDIT_COLUMNS / editable_rows / merge_editable_rows :
  「编辑」占位符行的通用模型（填表=placeholders，目录=anchors）
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


def _find_meta_file(category: str, name: str) -> Path | None:
    """按模板名定位库内 meta JSON（优先 <name>.json，source 脱钩时扫目录匹配）。

    name 非空校验交由调用方；找不到返回 None。
    """
    d = templates_dir(category)
    meta = d / (name + ".json")
    if meta.is_file():
        return meta
    return next(
        (f for f in d.glob("*.json") if f.is_file() and _meta_source(f) == name),
        None,
    )


def remove_template(category: str, name: str) -> None:
    """删除模板库（category 子目录）中的模板条目（meta JSON）。

    条目不存在时抛 FileNotFoundError（由调用方处理）；name 含路径分隔符或
    越界（被篡改的 source 字段）时抛 ValueError，不做任何删除。
    """
    _validate_template_name(name)
    meta = _find_meta_file(category, name)
    if meta is None:
        raise FileNotFoundError(f"模板不存在: {name}")
    meta.unlink()


def load_template_json(category: str, name: str) -> dict | None:
    """读取模板库（category 子目录）中某模板的完整 meta JSON dict。

    条目不存在 / JSON 损坏时返回 None（由调用方决定报错）。name 含路径
    分隔符或越界（被篡改的 source 字段）时抛 ValueError，不做任何读取，
    防止越界访问模板库目录之外。
    """
    _validate_template_name(name)
    meta = _find_meta_file(category, name)
    if meta is None:
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_template_json(category: str, name: str, data: dict) -> Path:
    """把完整 meta dict 写入模板库（category 子目录）的 `<name>.json`。

    自动补全 version/source（source 取 name，与上传时一致），保持文件
    名与模板名绑定；name 含路径分隔符或越界时抛 ValueError，防止越界写入。
    """
    _validate_template_name(name)
    payload = {
        "version": 1,
        "source": name,
        **data,
    }
    out = templates_dir(category) / (name + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ---------------- 模板编辑（GUI「编辑」） ----------------
#
# 每个模板库分类的占位符「可编辑行」列定义：
#   元组 = (meta 键, 表头文案, 类型)
#   类型 ∈ {"str", "float", "int", "bool"}，保存时按类型校验/转换。
# 填表助手编辑 [列名] 占位符规格；目录助手编辑 [字段名] 取值锚点。
TEMPLATE_EDIT_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "fill": [
        ("text", "列名", "str"),
        ("layer", "图层", "str"),
        ("x", "X", "float"),
        ("y", "Y", "float"),
        ("height", "字高", "float"),
        ("style", "样式", "str"),
        ("halign", "水平对齐", "int"),
        ("valign", "垂直对齐", "int"),
        ("ref_text", "占位文字", "str"),
    ],
    "catalog": [
        ("field", "字段名", "str"),
        ("is_area", "区域(取值矩形)", "bool"),
        ("min_x", "minX", "float"),
        ("min_y", "minY", "float"),
        ("max_x", "maxX", "float"),
        ("max_y", "maxY", "float"),
        ("point_x", "点X", "float"),
        ("point_y", "点Y", "float"),
    ],
}


def _edit_list_key(category: str) -> str:
    """可编辑行所在的 meta 键：填表=placeholders，目录=anchors。"""
    if category == "fill":
        return "placeholders"
    return "anchors"


def editable_rows(category: str, data: dict) -> list[dict]:
    """从模板 meta dict 提取可编辑行（每行仅含可编辑列，缺失键补类型默认值）。

    fill 的 entity_desc 等不可编辑字段不参与展示；保存时由
    merge_editable_rows 按原样保留。data 为 None 时返回空列表。
    """
    if not isinstance(data, dict):
        return []
    cols = TEMPLATE_EDIT_COLUMNS.get(category, [])
    items = data.get(_edit_list_key(category))
    if not isinstance(items, list):
        return []
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row: dict = {}
        for key, _header, kind in cols:
            row[key] = item.get(key, _default_for(kind))
        rows.append(row)
    return rows


def _default_for(kind: str):
    if kind == "bool":
        return False
    if kind == "int":
        return 0
    if kind == "float":
        return 0.0
    return ""


def merge_editable_rows(category: str, data: dict, rows: list[dict]) -> dict:
    """把编辑后的行合并回模板 meta dict，返回待保存的 payload。

    类型按列定义校验/宽容转换（坐标允许数字或数字字符串）；非法抛
    ValueError（GUI 弹错提示）。fill 保留原行 entity_desc；catalog 按
    编辑后的锚点 field 重新生成 fields 列表。
    """
    if not isinstance(data, dict):
        data = {}
    cols = TEMPLATE_EDIT_COLUMNS.get(category, [])
    key = _edit_list_key(category)
    orig_items = data.get(key)
    if not isinstance(orig_items, list):
        orig_items = []

    def _coerce(kind: str, value, idx: int) -> object:
        if kind == "str":
            return "" if value is None else str(value)
        if kind == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "是", "yes"):
                    return True
                if low in ("0", "false", "否", "no"):
                    return False
            raise ValueError(f"第 {idx + 1} 行「{'是/否'}」应为是/否")
        try:
            return float(value) if kind == "float" else int(float(value))
        except (TypeError, ValueError) as ex:
            raise ValueError(f"第 {idx + 1} 行数值非法（{ex}）") from ex

    new_items: list[dict] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        merged: dict = {}
        if idx < len(orig_items) and isinstance(orig_items[idx], dict):
            merged = dict(orig_items[idx])  # 保留 entity_desc 等不可编辑字段
        for k, _h, kind in cols:
            merged[k] = _coerce(kind, row.get(k, _default_for(kind)), idx)
        new_items.append(merged)

    payload = dict(data)
    payload[key] = new_items
    if category == "catalog":
        fields: list[str] = []
        for item in new_items:
            f = str(item.get("field", "")).strip()
            if f and f not in fields:
                fields.append(f)
        payload["fields"] = fields
    return payload
