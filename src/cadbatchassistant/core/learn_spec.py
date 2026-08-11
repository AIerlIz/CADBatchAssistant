# -*- coding: utf-8 -*-
"""图纸模板占位扫描：从模板 DXF 扫描占位文字（[列名]）与数据表表头匹配。

- 占位符文字 = 数据表该列的表头（如列名 'NPD (inch)' → 值格填 [NPD (inch)]）。
- 匹配为**精确匹配**（不做归一化）：占位符内文字去两端空白后与表头完全相同。
- 值生成规则（frac_inch / test_press / design_press / value）按列名内部归一化
  分类判断（仅用于规则，不用于识别匹配）。
"""

from __future__ import annotations

import json
import os
import sys

from cadbatchassistant.core.dxf_processor import read_doc
from cadbatchassistant.core.parse_xlsx import get_headers

LAYERS = ("0",)


def scan_placeholders(dxf_path: str, xlsx_path: str | None = None) -> dict:
    """扫描模板 DXF 占位符（[列名]），与数据表表头精确匹配。

    返回 {图层: {列名: 规格}}：
      x/y/height/style/halign/valign（占位实体属性）、
      value_rule（按列名分类）、sep（压力格默认空格）、entity（占位符实体）。
    """
    headers: list[str] = get_headers(xlsx_path) if xlsx_path else []
    header_map = {h.strip(): h for h in headers}   # 精确匹配（不归一化）

    doc = read_doc(dxf_path)
    spec: dict = {"0": {}}
    for e in doc.modelspace():
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        layer = e.dxf.layer
        if layer not in LAYERS:
            continue
        t = e.dxf.text if e.dxftype() == "TEXT" else e.text
        ts = t.strip()
        if not (ts.startswith("[") and ts.endswith("]")):
            continue
        col = header_map.get(ts[1:-1].strip())
        if col is None:
            continue  # 非数据表表头的占位符忽略
        ins = e.dxf.insert
        height = e.dxf.height if e.dxftype() == "TEXT" else e.dxf.char_height
        spec[layer][col] = {
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


def scan_to_file(dxf_path: str, xlsx_path: str | None = None,
                 out_path: str | None = None) -> str:
    """扫描占位并写入 JSON，返回文件路径。"""
    spec = {"_template": scan_placeholders(dxf_path, xlsx_path)}
    if out_path is None:
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "specs.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
    return out_path


def main() -> None:
    # 用法: learn_spec.py <template.dxf> [数据表.xlsx/.xls]
    dxf = sys.argv[1]
    xlsx = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(scan_placeholders(dxf, xlsx), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
