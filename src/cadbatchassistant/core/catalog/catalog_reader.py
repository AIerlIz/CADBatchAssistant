"""DXF 文字实体工具与「按模板锚点取值」模块（目录助手）。

- iter_text_entities / _plain_text / decode_text：遍历与取纯文本基础工具
  （TEXT_TYPES / decode_text / iter_text_entities 复用 text_replace，避免重复定义）
- extract_by_anchors：按模板解析出的锚点（区域/单点）从实际图纸 DXF 中
  提取每个字段的值列表；该位置无值则忽略（目录层按 NA 处理）
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

from cadbatchassistant.core.common.text_replace import (
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


# 取值网格单元尺寸（模型空间单位）：按文字插入点分桶，锚点查询只探测
# 覆盖区域相交的网格单元，把原 O(锚点×全部文字) 降为近 O(文字+锚点×区域文字)。
# 标题栏坐标量级（图幅毫米）下 1 单元即可；过大的矩形区域探测单元数
# = (宽/单元)×(高/单元)，正常模板区域（几十~几百单位）开销可忽略。
_CELL_SIZE = 5.0


def _cell_key(x: float, y: float) -> tuple[int, int]:
    """文字插入点 → 网格键（floor 除法，负坐标同样正确）。"""
    return int(x // _CELL_SIZE), int(y // _CELL_SIZE)


def extract_by_anchors(
    dxf_path: str | Path,
    anchors,
) -> dict[str, list[str]]:
    """按模板锚点从单个 DXF 提取每字段值列表。

    - 所有锚点统一按覆盖矩形取值：区域锚点 = 模板里圈选的小矩形；
      单点锚点 = 占位符文字在模板中的包围盒（_text_bounds）
    - 矩形内仅保留编号型字符（字母/数字/中文/连字符/下划线）
    - 同一字段多个锚点（候选位置）的值合并，保序去重；无值锚点忽略

    性能：文字按插入点分桶到网格（_CELL_SIZE），锚点只探测覆盖区域
    相交的网格单元（原实现对每个锚点线性扫全部文字）。返回顺序与
    文档顺序一致（探测结果按原始索引排序），字段合并保序去重。

    返回 {字段名: [值, ...]}（按锚点出现顺序）。
    """
    doc = ezdxf.readfile(str(dxf_path))

    grid: dict[tuple[int, int], list[tuple[int, float, float, str]]] = {}
    for idx, e in enumerate(iter_text_entities(doc)):
        t = _plain_text(e).strip()
        if not t:
            continue
        x, y = _entity_insert_point(e)
        grid.setdefault(_cell_key(x, y), []).append((idx, x, y, t))

    def _probe(a) -> list[str]:
        """按覆盖矩形取矩形内文字（按文档顺序），已过滤非编号字符。"""
        hits: list[tuple[int, str]] = []
        cx0, cx1 = int(a.min_x // _CELL_SIZE), int(a.max_x // _CELL_SIZE)
        cy0, cy1 = int(a.min_y // _CELL_SIZE), int(a.max_y // _CELL_SIZE)
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for idx, x, y, t in grid.get((cx, cy), ()):
                    if (
                        a.min_x <= x <= a.max_x
                        and a.min_y <= y <= a.max_y
                        and _is_id_chars(t)
                    ):
                        hits.append((idx, t))
        hits.sort()  # 按文档索引恢复原顺序（网格探测顺序与文档顺序无关）
        return [t for _idx, t in hits]

    out: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}  # 字段级去重集合：O(1) 判重（原为 O(n²)）
    for a in anchors:
        bucket = out.setdefault(a.field, [])
        s = seen.setdefault(a.field, set())
        for v in _probe(a):
            if v not in s:
                s.add(v)
                bucket.append(v)
    return out
