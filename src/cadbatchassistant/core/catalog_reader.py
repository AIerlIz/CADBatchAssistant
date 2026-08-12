"""DXF 文字实体工具与「按模板锚点取值」模块（目录助手）。

- iter_text_entities / _plain_text / decode_text：遍历与取纯文本基础工具
  （TEXT_TYPES / decode_text / iter_text_entities 复用 text_replace，避免重复定义）
- extract_by_anchors：按模板解析出的锚点（区域/单点）从实际图纸 DXF 中
  提取每个字段的值列表；该位置无值则忽略（目录层按 NA 处理）
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

from cadbatchassistant.core.text_replace import (
    decode_text,
    iter_text_entities,
)


def _plain_text(e) -> str:
    """取实体纯文本（MTEXT 去掉格式码）。"""
    if e.dxftype() == "MTEXT":
        try:
            return e.plain_text()
        except Exception:  # noqa: BLE001 - 个别 MTEXT 结构异常时回退原始文本
            return decode_text(e.text)
    return decode_text(str(e.dxf.text))


def _entity_insert_point(e) -> tuple[float, float]:
    """取文字实体插入点坐标（TEXT/MTEXT/ATTDEF 均支持 dxf.insert）。"""
    try:
        insert = e.dxf.insert
        return float(insert.x), float(insert.y)
    except Exception:  # noqa: BLE001 - 兜底取 0,0（正常不会发生）
        return 0.0, 0.0


def _is_id_chars(s: str) -> bool:
    """编号型字符：字母/数字/中文/连字符/下划线（无空格、括号等）。

    放行中文（isalnum 对中文为 True），否则中文图号/管段编号会被整体
    过滤导致取值恒为 NA；仍排除空白与括号等分隔符号。
    """
    return all(c.isalnum() or c in "_-" for c in s)


def extract_by_anchors(
    dxf_path: str | Path,
    anchors,
    point_tolerance: float = 5.0,
    fig_fields: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """按模板锚点从单个 DXF 提取每字段值列表。

    - 区域锚点：取矩形内全部文字纯文本；单点锚点：取坐标 ± point_tolerance 内文字
    - 仅保留编号型字符（字母/数字/连字符/下划线）；文件名不参与取值过滤
    - 图号类字段（fig_fields）的单点锚点：只取距离最近 1 个文字（一个图号位
      只对应一个图号，避免把相邻位置的其他文字误取进来）；其他字段取容差内全部
    - 同一字段多个锚点（候选位置）的值合并，保序去重；无值锚点忽略
    返回 {字段名: [值, ...]}（按锚点出现顺序）。
    """
    doc = ezdxf.readfile(str(dxf_path))

    texts: list[tuple[float, float, str]] = []
    for e in iter_text_entities(doc):
        t = _plain_text(e).strip()
        if not t:
            continue
        x, y = _entity_insert_point(e)
        texts.append((x, y, t))

    out: dict[str, list[str]] = {}
    for a in anchors:
        vals: list[str] = []
        is_fig = a.field in fig_fields  # 图号类字段：单点锚点只取最近 1 个
        if a.is_area:
            for x, y, t in texts:
                if a.min_x <= x <= a.max_x and a.min_y <= y <= a.max_y:
                    if _is_id_chars(t):
                        vals.append(t)
        else:
            hits: list[tuple[float, str]] = []
            for x, y, t in texts:
                if abs(x - a.point_x) <= point_tolerance \
                        and abs(y - a.point_y) <= point_tolerance:
                    if _is_id_chars(t):
                        hits.append((max(abs(x - a.point_x), abs(y - a.point_y)), t))
            if is_fig:
                # 图号类单点锚点：容差内只取距离最近 1 个文字
                if hits:
                    vals.append(min(hits, key=lambda h: h[0])[1])
            else:
                vals = [t for _, t in hits]
        # 保序去重后并入该字段（多候选锚点合并）
        bucket = out.setdefault(a.field, [])
        for v in vals:
            if v not in bucket:
                bucket.append(v)
    return out
