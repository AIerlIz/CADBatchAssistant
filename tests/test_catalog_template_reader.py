"""catalog_template_reader.parse_template 单测：锚点生成与覆盖区域计算。

- 单点 [字段名] 占位符 → 锚点带覆盖矩形（文字包围盒）
- TEXT 占位符：按字符数×字高估算包围盒
- MTEXT 占位符：优先 get_bounding_box，失败回退估算
- 区域锚点（矩形圈选）：is_area=True，矩形即取值区域
- 无占位符模板抛 ValueError
"""

import ezdxf
import pytest

from cadbatchassistant.core.catalog_template_reader import parse_template


def _make_tpl(path, add_rect=False):
    doc = ezdxf.new("R2013")
    m = doc.modelspace()
    if add_rect:
        m.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True)
    return doc, m


def test_text_placeholder_gets_cover_rect(tmp_path):
    """TEXT 单点占位符：锚点带覆盖矩形（按字符数×字高估算，以插入点为中心）。"""
    doc, m = _make_tpl(tmp_path / "t.dxf")
    # [图纸号] 5 字符（3 中文宽 1.0 + 2 括号宽 0.6）× height 2.0 = 8.4
    m.add_text("[图纸号]", dxfattribs={"insert": (10, 10), "height": 2.0})
    p = str(tmp_path / "t.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "图纸号"
    assert a.is_area is False
    # 覆盖矩形 = 中心 (10,10) ± (宽/2, 高/2)
    assert abs(a.min_x - (10 - 8.4 / 2)) < 0.01
    assert abs(a.max_x - (10 + 8.4 / 2)) < 0.01
    assert abs(a.min_y - (10 - 2.0 / 2)) < 0.01
    assert abs(a.max_y - (10 + 2.0 / 2)) < 0.01


def test_mtext_placeholder_gets_cover_rect(tmp_path):
    """MTEXT 单点占位符：锚点带覆盖矩形（get_bounding_box 优先，失败回退估算）。"""
    doc, m = _make_tpl(tmp_path / "m.dxf")
    m.add_mtext("[管段编号]", dxfattribs={"insert": (5, 5), "char_height": 1.0})
    p = str(tmp_path / "m.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "管段编号"
    assert a.is_area is False
    # 无论走 bounding box 还是估算，覆盖矩形都应非退化且包含插入点
    assert a.min_x < a.max_x and a.min_y < a.max_y
    assert a.min_x <= 5.0 <= a.max_x
    assert a.min_y <= 5.0 <= a.max_y


def test_rect_placeholder_becomes_area_anchor(tmp_path):
    """占位符落在小矩形内 → 区域锚点（is_area=True），矩形即取值区域。"""
    doc, m = _make_tpl(tmp_path / "r.dxf", add_rect=True)
    m.add_text("[图号]", dxfattribs={"insert": (10, 5), "height": 1.0})
    p = str(tmp_path / "r.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "图号"
    assert a.is_area is True
    assert a.min_x == 0.0 and a.min_y == 0.0
    assert a.max_x == 20.0 and a.max_y == 10.0


def test_multiple_placeholders_keep_order(tmp_path):
    """多个占位符按出现顺序生成锚点（图号在前，管段在后）。"""
    doc, m = _make_tpl(tmp_path / "o.dxf")
    m.add_text("[图号]", dxfattribs={"insert": (0, 0), "height": 1.0})
    m.add_text("[管段]", dxfattribs={"insert": (0, 10), "height": 1.0})
    p = str(tmp_path / "o.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert [a.field for a in anchors] == ["图号", "管段"]


def test_no_placeholder_raises(tmp_path):
    """模板无任何 [字段名] 占位符时抛 ValueError。"""
    doc, m = _make_tpl(tmp_path / "n.dxf")
    m.add_text("普通文字", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "n.dxf")
    doc.saveas(p)

    with pytest.raises(ValueError):
        parse_template(p)
