"""catalog_reader.extract_by_anchors 正则过滤单测。"""

import ezdxf

from cadbatchassistant.core.catalog.catalog_reader import extract_by_anchors
from cadbatchassistant.core.catalog.catalog_template_reader import Anchor


def _make_dxf(path, texts: list[tuple[tuple[float, float], str]]) -> str:
    doc = ezdxf.new("R2013")
    m = doc.modelspace()
    for (x, y), t in texts:
        m.add_text(t, dxfattribs={"insert": (x, y), "height": 1.0})
    p = str(path)
    doc.saveas(p)
    return p


def _point_anchor(
    field: str, x: float = 0.0, y: float = 0.0, half: float = 5.0,
    from_attribute: bool = False, regex: str = "",
) -> Anchor:
    """单点锚点：支持 from_attribute 和 regex 参数。"""
    return Anchor(
        field=field,
        is_area=False,
        min_x=x - half,
        min_y=y - half,
        max_x=x + half,
        max_y=y + half,
        point_x=x,
        point_y=y,
        from_attribute=from_attribute,
        regex=regex,
    )


def test_regex_filter_values(tmp_path):
    """regex 非空时仅保留匹配该正则的值。"""
    dxf = _make_dxf(
        tmp_path / "r.dxf",
        [((0, 0), "DW-1001"), ((2, 0), "ABC-9999"), ((4, 0), "DW-2002")],
    )
    anchor = _point_anchor("图号", regex=r"^DW-")
    out = extract_by_anchors(dxf, [anchor])
    assert out["图号"] == ["DW-1001", "DW-2002"]


def test_regex_no_match_returns_empty(tmp_path):
    """regex 不匹配时返回空列表。"""
    dxf = _make_dxf(tmp_path / "nr.dxf", [((0, 0), "DW-1001")])
    anchor = _point_anchor("图号", regex=r"^ABC-")
    out = extract_by_anchors(dxf, [anchor])
    assert not out.get("图号")


def test_regex_partial_match(tmp_path):
    """正则部分匹配：保留包含特定子串的值。"""
    dxf = _make_dxf(
        tmp_path / "p.dxf",
        [((0, 0), "DRAWING-001"), ((2, 0), "DETAIL-A"), ((4, 0), "DRAWING-002")],
    )
    anchor = _point_anchor("图号", regex=r"DRAWING")
    out = extract_by_anchors(dxf, [anchor])
    assert out["图号"] == ["DRAWING-001", "DRAWING-002"]
