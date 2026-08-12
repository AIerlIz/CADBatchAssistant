"""catalog_reader.extract_by_anchors 单测：锚点取值逻辑。

- 图号类字段（fig_fields）单点锚点：容差内只取距离最近 1 个文字
- 非图号字段单点锚点：取容差内全部文字
- 区域锚点：取区域内全部编号型文字
- 文件名不参与取值过滤（无 exclude 参数）；非编号型文字被过滤
"""

import ezdxf

from cadbatchassistant.core.catalog_reader import extract_by_anchors
from cadbatchassistant.core.catalog_template_reader import Anchor


def _make_dxf(path, texts: list[tuple[tuple[float, float], str]]) -> str:
    doc = ezdxf.new("R2013")
    m = doc.modelspace()
    for (x, y), t in texts:
        m.add_text(t, dxfattribs={"insert": (x, y), "height": 1.0})
    p = str(path)
    doc.saveas(p)
    return p


def test_fig_single_point_takes_nearest_only(tmp_path):
    """图号类单点锚点：容差内多个文字时只取距离最近 1 个。"""
    dxf = _make_dxf(tmp_path / "a.dxf",
                    [((0, 0), "DW-1001"), ((2, 0), "DW-9999")])
    anchors = [Anchor(field="图号", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0,
                             fig_fields=frozenset({"图号"}))
    assert out["图号"] == ["DW-1001"]


def test_fig_single_point_out_of_tolerance_empty(tmp_path):
    """图号类单点锚点：容差内无文字时该字段缺省（NA 由上层处理）。"""
    dxf = _make_dxf(tmp_path / "b.dxf", [((10, 10), "DW-1001")])
    anchors = [Anchor(field="图号", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0,
                             fig_fields=frozenset({"图号"}))
    assert not out.get("图号")  # 空值列表，上层按 NA 处理


def test_non_fig_single_point_takes_all_within_tolerance(tmp_path):
    """非图号单点锚点：容差内全部文字都取（多值字段）。"""
    dxf = _make_dxf(tmp_path / "c.dxf",
                    [((0, 0), "PIPE-1"), ((2, 0), "PIPE-2")])
    anchors = [Anchor(field="管段", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0)
    assert out["管段"] == ["PIPE-1", "PIPE-2"]


def test_area_anchor_takes_all_in_rect(tmp_path):
    """区域锚点：取矩形内全部编号型文字，矩形外忽略。"""
    dxf = _make_dxf(tmp_path / "d.dxf",
                    [((1, 1), "PIPE-1"), ((5, 2), "PIPE-2"),
                     ((30, 30), "PIPE-3")])
    anchors = [Anchor(field="管段", is_area=True,
                      min_x=0.0, min_y=0.0, max_x=10.0, max_y=10.0,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors)
    assert out["管段"] == ["PIPE-1", "PIPE-2"]


def test_non_id_chars_filtered(tmp_path):
    """含空格/中文等非编号字符的文字被过滤，即使落在锚点内。"""
    dxf = _make_dxf(tmp_path / "e.dxf",
                    [((0, 0), "PIPE-1 中文"), ((2, 0), "PIPE-2")])
    anchors = [Anchor(field="管段", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0)
    assert out["管段"] == ["PIPE-2"]


def test_file_name_like_text_is_not_excluded(tmp_path):
    """与文件名相同的文字不再被排除（文件名不参与取值过滤）。"""
    dxf = _make_dxf(tmp_path / "f.dxf", [((0, 0), "024707VA2292_HAFT-A2DK-B")])
    anchors = [Anchor(field="图号", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0,
                             fig_fields=frozenset({"图号"}))
    assert out["图号"] == ["024707VA2292_HAFT-A2DK-B"]


def test_chinese_id_chars_kept(tmp_path):
    """M7：中文图号/编号被保留（不再被整体过滤）。"""
    dxf = _make_dxf(tmp_path / "cn.dxf",
                    [((0, 0), "图号甲-01"), ((2, 0), "管段乙_02")])
    anchors = [Anchor(field="图号", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0,
                             fig_fields=frozenset({"图号"}))
    # 最近 1 个：纯中文+连字符（"-"保留），"图号甲-01"
    assert out["图号"] == ["图号甲-01"]


def test_chinese_with_space_still_filtered(tmp_path):
    """M7：中文放行但含空格等分隔符的文字仍被过滤。"""
    dxf = _make_dxf(tmp_path / "cnsp.dxf",
                    [((0, 0), "图号 甲-01"), ((2, 0), "管段乙-02")])
    anchors = [Anchor(field="图号", is_area=False,
                      point_x=0.0, point_y=0.0)]
    out = extract_by_anchors(dxf, anchors, point_tolerance=5.0,
                             fig_fields=frozenset({"图号"}))
    assert out["图号"] == ["管段乙-02"]  # 空格版被过滤，无空格版被取到
