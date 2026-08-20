"""catalog_template_reader.parse_template 单测：锚点生成与覆盖区域计算。

- 单点 [字段名] 占位符 → 锚点带覆盖矩形（文字包围盒）
- TEXT 占位符：按字符数×字高估算包围盒
- MTEXT 占位符：优先 get_bounding_box，失败回退估算
- 区域锚点（矩形圈选）：is_area=True，矩形即取值区域
- 无占位符模板抛 ValueError
"""

import ezdxf
import pytest

from cadbatchassistant.core.catalog.catalog_template_reader import (
    anchor_to_dict,
    anchors_from_dict,
    parse_template,
)


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


def test_anchor_to_dict_roundtrip():
    """anchor_to_dict → anchors_from_dict 往返字段一致。"""
    from cadbatchassistant.core.catalog.catalog_template_reader import Anchor

    src = [
        Anchor(field="图号", is_area=False, min_x=1.0, min_y=2.0,
               max_x=3.0, max_y=4.0, point_x=2.0, point_y=3.0),
        Anchor(field="管段编号", is_area=True, min_x=0.0, min_y=0.0,
               max_x=10.0, max_y=5.0, point_x=5.0, point_y=2.5),
    ]
    data = [anchor_to_dict(a) for a in src]
    back = anchors_from_dict(data)
    assert [
        (a.field, a.is_area, a.min_x, a.min_y, a.max_x, a.max_y,
         a.point_x, a.point_y)
        for a in back
    ] == [
        (a.field, a.is_area, a.min_x, a.min_y, a.max_x, a.max_y,
         a.point_x, a.point_y)
        for a in src
    ]


def test_anchors_from_dict_accepts_numeric_strings():
    """坐标允许数字字符串（手工编辑 meta JSON 的宽容性）。"""
    back = anchors_from_dict(
        [{"field": "图号", "is_area": False, "min_x": "1.5", "max_y": "2"}]
    )
    assert back[0].min_x == 1.5 and back[0].max_y == 2.0


def test_anchors_from_dict_rejects_bad_data():
    """损坏数据抛异常：非列表 / 非对象 / field 缺失 / is_area 非布尔。"""
    with pytest.raises((ValueError, TypeError)):
        anchors_from_dict("not-a-list")
    with pytest.raises((ValueError, TypeError)):
        anchors_from_dict([42])
    with pytest.raises((ValueError, TypeError)):
        anchors_from_dict([{"is_area": False}])
    with pytest.raises((ValueError, TypeError)):
        anchors_from_dict([{"field": "图号", "is_area": "yes"}])
    with pytest.raises((ValueError, TypeError)):
        anchors_from_dict([{"field": "图号", "is_area": False, "min_x": "abc"}])


def test_curly_brace_placeholder_sets_from_attribute(tmp_path):
    """花括号占位符 {字段名}：from_attribute=True。"""
    doc, m = _make_tpl(tmp_path / "c.dxf")
    m.add_text("{管段}", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "c.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "管段"
    assert a.from_attribute is True


def test_square_brace_placeholder_sets_from_attribute_false(tmp_path):
    """方括号占位符 [字段名]：from_attribute=False。"""
    doc, m = _make_tpl(tmp_path / "s.dxf")
    m.add_text("[图号]", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "s.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "图号"
    assert a.from_attribute is False


def test_placeholder_with_regex_square_brace(tmp_path):
    """方括号占位符带正则：[图号#^DW-] 解析出 field 和 regex。"""
    doc, m = _make_tpl(tmp_path / "r.dxf")
    m.add_text("[图号#^DW-]", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "r.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "图号"
    assert a.regex == "^DW-"
    assert a.from_attribute is False


def test_placeholder_with_regex_curly_brace(tmp_path):
    """花括号占位符带正则：{管段#ABC} 解析出 field、regex 和 from_attribute。"""
    doc, m = _make_tpl(tmp_path / "rc.dxf")
    m.add_text("{管段#ABC}", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "rc.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    a = anchors[0]
    assert a.field == "管段"
    assert a.regex == "ABC"
    assert a.from_attribute is True


def test_placeholder_without_regex_has_empty_regex(tmp_path):
    """不带正则的占位符：regex 为空字符串。"""
    doc, m = _make_tpl(tmp_path / "nrg.dxf")
    m.add_text("[图号]", dxfattribs={"insert": (0, 0), "height": 1.0})
    p = str(tmp_path / "nrg.dxf")
    doc.saveas(p)

    anchors = parse_template(p)
    assert len(anchors) == 1
    assert anchors[0].regex == ""
