"""图纸模板占位扫描：从模板 DXF 扫描占位文字（[列名]）。

- 占位符文字 = 数据表该列的表头（如列名 'NPD (inch)' → 值格填 [NPD (inch)]）。
- 匹配为**精确匹配**（不做归一化）：占位符内文字去两端空白后与表头完全相同。
- 值生成规则 value_rule_for：当前恒为原样填入（value，不分类加工），
  保留为纯函数供运行时按本次数据表表头重算（未来可扩展列名分类）。
- scan_all_placeholders：无数据表依赖，扫描全部 [列名] 占位符，返回
  JSON 可序列化规格（含 entity_desc）——供上传时写入模板伴生 meta。
- scan_placeholders：与数据表表头精确匹配的完整规格（含真实实体），
  供 CLI / 命令行兜底路径使用。
"""

from __future__ import annotations

import json
import sys

from cadbatchassistant.core.common.text_replace import decode_text, read_doc
from cadbatchassistant.core.fill.fill_dwg import entity_to_desc
from cadbatchassistant.core.fill.fill_parse_xlsx import get_headers


def _iter_placeholders(dxf_path: str):
    """遍历模型空间 [列名] 占位符文字实体，产出 (原始文本, 去括号列名, 实体)。

    原始文本 = 实体内的完整文本（含方括号）；去括号列名 = 两端去空白后的
    方括号内文字；仅 TEXT/MTEXT 且整段形如 `[...]` 的实体参与。
    """
    doc = read_doc(dxf_path)
    for e in doc.modelspace():
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        # MTEXT 文本经 .text property 取（DXFGraphic 静态类型无该属性，用 getattr）
        t = e.dxf.text if e.dxftype() == "TEXT" else getattr(e, "text", "")
        ts = decode_text(t).strip()
        if not (ts.startswith("[") and ts.endswith("]")):
            continue
        col = ts[1:-1].strip()
        if not col:
            continue
        yield ts, col, e


def _placeholder_spec(e, col: str, ref_text: str) -> dict:
    """单个占位符实体 → 规格 dict（x/y/height/style/对齐/参考文本/实体）。"""
    layer = e.dxf.layer
    ins = e.dxf.insert
    height = e.dxf.height if e.dxftype() == "TEXT" else e.dxf.char_height
    return {
        "text": col,
        "layer": layer,
        "x": round(float(ins[0]), 6),
        "y": round(float(ins[1]), 6),
        "height": round(float(height), 6),
        "style": getattr(e.dxf, "style", None) or "",
        "halign": int(getattr(e.dxf, "halign", 0) or 0),
        "valign": int(getattr(e.dxf, "valign", 0) or 0),
        "ref_text": ref_text,
    }


def value_rule_for(col: str) -> tuple[str, str]:
    """列名 → (value_rule, sep)：当前恒为原样填入（value，不分类加工）。

    历史实现把 value_rule 硬编码为 "value"、sep 为空串；保留为纯函数
    供运行时按本次数据表表头重算，未来如需按列名分类加工在此扩展。
    """
    return "value", ""


def _json_safe(value):
    """把 ezdxf 描述中的 Vec3 等非 JSON 类型递归转为可序列化基础类型。

    仅用于 meta 持久化路径（entity_to_desc 的原始 dict 仍供并行任务
    pickle 使用，保持现状）。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    try:  # Vec3 等序列类型 → list（JSON 原生）
        return [_json_safe(v) for v in value]
    except TypeError:
        return str(value)


def scan_all_placeholders(dxf_path: str) -> list[dict]:
    """扫描模板 DXF 全部 [列名] 占位符（不依赖数据表）。

    返回 JSON 可序列化规格列表（按模型空间出现顺序）：
    text/layer/x/y/height/style/halign/valign/ref_text/entity_desc。
    entity_desc 经 _json_safe 清理（Vec3 → tuple），可直接 json.dumps。
    """
    out: list[dict] = []
    for ts, col, e in _iter_placeholders(dxf_path):
        spec = _placeholder_spec(e, col, ts)
        spec["entity_desc"] = _json_safe(entity_to_desc(e))
        out.append(spec)
    return out


def scan_placeholders(
    dxf_path: str, xlsx_path: str | None = None, sheet: str | None = None
) -> dict:
    """扫描模板 DXF 占位符（[列名]），与数据表表头精确匹配。

    sheet：数据表中工作表名（None 默认第一个），与填表 load_xlsx 使用
    同一 sheet 的表头，保证列名来源一致。
    占位符可位于任意图层；返回 {图层: {列名: 规格}}：
      x/y/height/style/halign/valign（占位实体属性）、
      value_rule（按列名分类）、sep（压力格默认空格）、entity（占位符实体）。
    """
    headers: list[str] = get_headers(xlsx_path, sheet) if xlsx_path else []
    header_map = {h.strip(): h for h in headers}  # 精确匹配（不归一化）

    spec: dict = {}
    for ts, col, e in _iter_placeholders(dxf_path):
        header = header_map.get(col)
        if header is None:
            continue  # 非数据表表头的占位符忽略
        value_rule, sep = value_rule_for(header)
        fs = _placeholder_spec(e, header, ts)
        fs["value_rule"] = value_rule
        fs["sep"] = sep
        fs["entity"] = e
        spec.setdefault(fs["layer"], {})[header] = fs
    return spec


def main() -> None:
    # 用法: learn_spec.py <template.dxf> [数据表.xlsx/.xls]
    dxf = sys.argv[1]
    xlsx = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(scan_placeholders(dxf, xlsx), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
