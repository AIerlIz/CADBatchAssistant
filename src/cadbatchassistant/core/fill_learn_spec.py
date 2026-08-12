# -*- coding: utf-8 -*-
"""图纸模板占位扫描：从模板 DXF 扫描占位文字（[列名]）与数据表表头匹配。

- 占位符文字 = 数据表该列的表头（如列名 'NPD (inch)' → 值格填 [NPD (inch)]）。
- 匹配为**精确匹配**（不做归一化）：占位符内文字去两端空白后与表头完全相同。
- 值生成规则（frac_inch / test_press / design_press / value）按列名内部归一化
  分类判断（仅用于规则，不用于识别匹配）。
"""

from __future__ import annotations

import json
import sys

from cadbatchassistant.core.text_replace import decode_text, read_doc
from cadbatchassistant.core.fill_parse_xlsx import get_headers

def scan_placeholders(dxf_path: str, xlsx_path: str | None = None,
                      sheet: str | None = None) -> dict:
    """扫描模板 DXF 占位符（[列名]），与数据表表头精确匹配。

    sheet：数据表中工作表名（None 默认第一个），与填表 load_xlsx 使用
    同一 sheet 的表头，保证列名来源一致。
    占位符可位于任意图层；返回 {图层: {列名: 规格}}：
      x/y/height/style/halign/valign（占位实体属性）、
      value_rule（按列名分类）、sep（压力格默认空格）、entity（占位符实体）。
    """
    headers: list[str] = get_headers(xlsx_path, sheet) if xlsx_path else []
    header_map = {h.strip(): h for h in headers}   # 精确匹配（不归一化）

    doc = read_doc(dxf_path)
    spec: dict = {}
    for e in doc.modelspace():
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        layer = e.dxf.layer
        t = e.dxf.text if e.dxftype() == "TEXT" else e.text
        ts = decode_text(t).strip()
        if not (ts.startswith("[") and ts.endswith("]")):
            continue
        col = header_map.get(ts[1:-1].strip())
        if col is None:
            continue  # 非数据表表头的占位符忽略
        ins = e.dxf.insert
        height = e.dxf.height if e.dxftype() == "TEXT" else e.dxf.char_height
        spec.setdefault(layer, {})[col] = {
            "x": round(float(ins[0]), 6),
            "y": round(float(ins[1]), 6),
            "height": round(float(height), 6),
            "style": getattr(e.dxf, "style", None) or "",
            "halign": int(getattr(e.dxf, "halign", 0) or 0),
            "valign": int(getattr(e.dxf, "valign", 0) or 0),
            "ref_text": t,
            "value_rule": "value",   # 数据表值原样填入，不分类加工
            "sep": "",
            "entity": e,
        }
    return spec


def main() -> None:
    # 用法: learn_spec.py <template.dxf> [数据表.xlsx/.xls]
    dxf = sys.argv[1]
    xlsx = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(scan_placeholders(dxf, xlsx), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
