"""模板解析（目录助手）：从图纸模板 DWG 提取 [字段名] 占位符与矩形取值区域。

模板制作约定（在 DWG 模板图上）：
- 在需要取值的位置放一个文字占位符，内容为 `[字段名]`（半角方括号），
  如 [图号]、[管段编号]；可放多个候选位置（同一字段名可出现多次）。
- 若要圈定一个区域取多个值（如管段编号列表），用一个闭合矩形
  （4 点闭合 LWPOLYLINE / RECTANG）圈住区域，占位符放在矩形内。
- 占位符落在矩形内 → 该矩形为该字段的取值区域（取区域内全部文字）；
  未落在任何矩形 → 单点锚点（取该坐标 ± 容差内文字）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import ezdxf

from cadbatchassistant.core.catalog_reader import (
    _entity_insert_point,
    _plain_text,
    iter_text_entities,
)

# 占位符：整段文本为 [字段名]
_PLACEHOLDER_RE = re.compile(r"^\[([^\]]+)\]$")

# 矩形判定容差（浮点坐标误差）
_EPS = 1e-4


def _text_bounds(e) -> tuple[float, float, float, float]:
    """占位符文字包围盒（覆盖区域）：单点锚点按此矩形取值。

    MTEXT 优先用 ezdxf 的 get_bounding_box；TEXT/回退按字符数估算
    （中文字符宽 ≈ 字高，ASCII 宽 ≈ 0.6×字高），以插入点为中心扩展。
    覆盖区域完全由占位符文字在模板中的大小决定——把占位符文字调大
    即可覆盖更大的取值范围，不再依赖 point_tolerance。
    """
    x, y = _entity_insert_point(e)
    if e.dxftype() == "MTEXT":
        try:
            bb = e.get_bounding_box()
            if bb.has_data:
                return bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y
        except Exception:  # noqa: BLE001 - 个别 MTEXT 无包围盒时回退估算
            pass
    height = float(getattr(e.dxf, "height", 1.0) or 1.0)
    text = _plain_text(e)
    width = sum(1.0 if ord(ch) > 0x2E7F else 0.6 for ch in text) * height
    return x - width / 2, y - height / 2, x + width / 2, y + height / 2


@dataclass
class Anchor:
    """一个取值锚点（模板中的一个占位符）。"""

    field: str  # 字段名，如 图号 / 管段编号
    is_area: bool  # True=矩形区域；False=单点
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    point_x: float = 0.0  # 单点锚点的占位符坐标
    point_y: float = 0.0


def _collect_rects(
    doc, area_max_size: float
) -> list[tuple[float, float, float, float]]:
    """收集模型空间中 4 点闭合的轴对齐矩形，返回 (minx,miny,maxx,maxy)。

    只保留面积 ≤ area_max_size 的小矩形（圈取值区域用）——图框边框等
    大矩形不会误识别为取值区域。
    """
    rects: list[tuple[float, float, float, float]] = []
    for e in doc.modelspace():
        if e.dxftype() != "LWPOLYLINE":
            continue
        if not e.closed:
            continue
        pts = list(e.get_points("xy"))
        # 闭合多段线首尾可能重复，去掉重复尾点
        if (
            len(pts) >= 5
            and abs(pts[0][0] - pts[-1][0]) < _EPS
            and abs(pts[0][1] - pts[-1][1]) < _EPS
        ):
            pts = pts[:-1]
        if len(pts) != 4:
            continue
        xs = {round(p[0], 6) for p in pts}
        ys = {round(p[1], 6) for p in pts}
        if len(xs) == 2 and len(ys) == 2:
            # 四点恰为两 x × 两 y 的组合 → 轴对齐矩形
            combos = {(round(p[0], 6), round(p[1], 6)) for p in pts}
            if len(combos) == 4:
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                w = max_x - min_x
                h = max_y - min_y
                if w > _EPS and h > _EPS and w * h <= area_max_size:
                    rects.append((min_x, min_y, max_x, max_y))
    return rects


def parse_template(
    template_path: str | Path, area_max_size: float = 10000.0
) -> list[Anchor]:
    """解析模板 DWG/DXF，返回按出现顺序排列的锚点列表。

    area_max_size：视为取值区域的矩形最大面积（图框边框等大矩形被排除，
    只把用户特意圈取值区域的小矩形当区域）。

    模板无任何 [字段名] 占位符时抛 ValueError。
    """
    doc = ezdxf.readfile(str(template_path))
    rects = _collect_rects(doc, area_max_size)

    anchors: list[Anchor] = []
    for e in iter_text_entities(doc):
        text = _plain_text(e).strip()
        m = _PLACEHOLDER_RE.match(text)
        if not m:
            continue
        field = m.group(1).strip()
        if not field:
            continue
        x, y = _entity_insert_point(e)
        # 关联矩形：占位符坐标落在某矩形内
        hit = next((r for r in rects if r[0] <= x <= r[2] and r[1] <= y <= r[3]), None)
        if hit is not None:
            anchors.append(
                Anchor(
                    field=field,
                    is_area=True,
                    min_x=hit[0],
                    min_y=hit[1],
                    max_x=hit[2],
                    max_y=hit[3],
                    point_x=x,
                    point_y=y,
                )
            )
        else:
            min_x, min_y, max_x, max_y = _text_bounds(e)
            anchors.append(
                Anchor(
                    field=field,
                    is_area=False,
                    min_x=min_x,
                    min_y=min_y,
                    max_x=max_x,
                    max_y=max_y,
                    point_x=x,
                    point_y=y,
                )
            )

    if not anchors:
        raise ValueError(
            f"模板中未找到 [字段名] 占位符: {template_path}"
            "（请在取值位置放 [字段名] 文字）"
        )
    return anchors


def collect_fields(anchors: list[Anchor]) -> list[str]:
    """返回按出现顺序去重后的字段名列表（供解析模板与生成表头共用）。"""
    fields: list[str] = []
    for a in anchors:
        if a.field not in fields:
            fields.append(a.field)
    return fields
