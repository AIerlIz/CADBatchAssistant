# -*- coding: utf-8 -*-
"""按 specs.json + 数据表.xlsx 填充 修改前 DXF 的标题栏值格。

流程（对每张图）：
1. 加载 before DXF（ACAD2004）
2. 删除 GT_1 层 text=='barg' 的压力占位实体
3. 对 specs.json 中每个字段：
   - 用 value_rule 从 xlsx 值生成显示文本
   - 目标位置已有相同文本实体 → 跳过（避免重复）
   - 否则在规格位置新建 TEXT（图层/坐标/字高/样式/对齐）
4. 保存为 filled DXF

用法：
    python fill_dwg.py <before_dxf_dir> <out_dxf_dir>
"""

from __future__ import annotations

import json
import os
import sys

from cadbatchassistant.core.text_replace import read_doc
from cadbatchassistant.core.fill_parse_xlsx import load_xlsx

HERE = os.path.dirname(os.path.abspath(__file__))
SPECS = os.path.join(HERE, "specs.json")
XLSX = r"D:\ISO图\数据表.xlsx"


def make_text(value_rule: str, val: str, sep: str) -> str:
    """数据表值原样填入（不分类加工）。"""
    return val.strip()


def find_texts(msp, layer: str, x: float, y: float, tol: float = 0.01):
    """在 (layer, x, y) 容差内查找 TEXT/MTEXT 实体。"""
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        if e.dxf.layer != layer:
            continue
        ins = e.dxf.insert
        if abs(ins[0] - x) < tol and abs(ins[1] - y) < tol:
            yield e




def fill_one(before_dxf: str, out_dxf: str, spec: dict, row: dict) -> list[str]:
    doc = read_doc(before_dxf)
    msp = doc.modelspace()
    log: list[str] = []
    # 注意：不删除处理图纸压力格的 'barg' 单位占位——
    # 值原样填入（不含单位），'barg' 作为图纸预置单位与值共存显示。

    # 按规格填值
    for layer, fields in spec.items():
        for field, fspec in fields.items():
            val = row.get(field, "")
            if val.strip():
                text = make_text(fspec["value_rule"], val, fspec.get("sep", ""))
            else:
                text = ""   # xlsx 值为空：占位符的值也置空（仍克隆占位符）
            x, y = fspec["x"], fspec["y"]

            if text:
                # 排除空文本与压力格单位 'barg'（均不算已占位内容，允许写入值）
                existing = [e for e in find_texts(msp, layer, x, y)
                            if getattr(e, "text", "").strip() not in ("", "barg")]
                if existing:
                    same = any(
                        "".join(getattr(e, "text", "").split()) == "".join(text.split())
                        for e in existing
                    )
                    if same:
                        log.append(f"跳过 {field}（已存在 {text!r}）")
                    else:
                        cur = getattr(existing[0], "text", "") or ""
                        log.append(f"跳过 {field}（位置已有内容，不覆盖：{cur!r}）")
                    continue

            ent = fspec.get("entity")
            if ent is not None:
                # 克隆占位符实体，只替换文字：格式（图层/字高/字体/对齐/
                # 旋转/颜色等）与模板占位符完全一致
                new = ent.copy()
                msp.add_entity(new)
                # 校验目标文档存在同名图层/样式，缺失则补齐（避免悬空引用）
                layer = new.dxf.layer
                if layer and layer not in doc.layers:
                    src_layer = ent.doc.layers.get(layer) if ent.doc else None
                    if src_layer is not None:
                        doc.layers.add(layer, dxfattribs=dict(
                            (k, v) for k, v in src_layer.dxfattribs().items()
                            if k in ("color", "linetype", "lineweight")))
                    else:
                        doc.layers.add(layer)
                style = getattr(new.dxf, "style", None)
                if style and style not in doc.styles:
                    src_style = ent.doc.styles.get(style) if ent.doc else None
                    if src_style is not None:
                        doc.styles.add(style, dxfattribs={
                            k: v for k, v in src_style.dxfattribs().items()
                            if k in ("font", "height", "width", "oblique")})
                    else:
                        doc.styles.add(style)
                new.dxf.text = text
                note = "（xlsx 值为空，置空）" if not text else "（替换占位符）"
                log.append(f"填写 {field} = {text!r} {note} [{layer}]")
                continue

            attribs = {
                "layer": layer,
                "insert": (x, y, 0.0),
                "height": fspec["height"],
                "style": fspec["style"],
                "halign": fspec["halign"],
                "valign": fspec["valign"],
            }
            if fspec["valign"] != 0 or fspec["halign"] != 0:
                attribs["align_point"] = (x, y, 0.0)
            msp.add_text(text, dxfattribs=attribs)
            log.append(f"填写 {field} = {text!r} @({x},{y}) [{layer}]")

    doc.saveas(out_dxf)
    log.append(f"保存 {out_dxf}")
    return log


def fill_all(before_dxf_dir: str, out_dxf_dir: str, xlsx: str,
             specs: dict, emit=print, progress=None,
             match_col: str | None = None,
             sheet: str | None = None,
             cancel=None) -> list[str]:
    """批量填充：对 specs 中每张图执行 fill_one。

    单张图失败不中断，记录后继续处理其余图纸。
    cancel   : 可选 threading.Event；置位时在当前图处理完后停止
               （未开始的图不再处理，调用方需自行处理剩余图纸）。
    progress : 可选回调 progress(done_index, total)，每处理一张图（成败均）调用一次。
    match_col: 数据表中图纸名列（None 默认第一列）。
    sheet    : 数据表中工作表名（None 默认第一个）。
    返回失败图纸名列表（处理失败的）。
    """
    data = load_xlsx(xlsx, match_col, sheet)
    stems = sorted(specs)
    failed: list[str] = []
    for i, stem in enumerate(stems, 1):
        if cancel is not None and cancel.is_set():
            emit("[WARN] 收到取消请求，停止填表")
            break
        try:
            if stem not in data:
                emit(f"[WARN] {stem} 不在 xlsx 中，跳过")
                continue
            before = os.path.join(before_dxf_dir, stem + ".dxf")
            out = os.path.join(out_dxf_dir, stem + ".dxf")
            if not os.path.isfile(before):
                emit(f"[WARN] 缺少 before DXF: {before}")
                continue
            emit(f"===== {stem}")
            for line in fill_one(before, out, specs[stem], data[stem]):
                emit("  " + line)
        except Exception as ex:  # noqa: BLE001 - 单图失败不中断整体
            emit(f"[ERROR] {stem} 处理失败：{ex}")
            failed.append(stem)
        finally:
            if progress:
                progress(i, len(stems))
    emit(f"      完成 {len(stems) - len(failed)}/{len(stems)} 张，"
         + (f"失败 {len(failed)} 张：{', '.join(failed)}" if failed else "全部成功"))
    return failed


def main() -> None:
    # 用法: fill_dwg.py <before_dxf_dir> <out_dxf_dir> [xlsx] [specs.json]
    before_dir = sys.argv[1]
    out_dir = sys.argv[2]
    xlsx = sys.argv[3] if len(sys.argv) > 3 else XLSX
    specs_path = sys.argv[4] if len(sys.argv) > 4 else SPECS
    os.makedirs(out_dir, exist_ok=True)

    with open(specs_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    fill_all(before_dir, out_dir, xlsx, spec)

if __name__ == "__main__":
    main()
