"""catalog_reader.extract_by_anchors 单测：锚点取值逻辑。

- 所有锚点统一按覆盖矩形取值：区域锚点 = 圈选矩形；单点锚点 =
  占位符文字包围盒（覆盖区域取值，不使用坐标容差）
- 矩形内仅保留编号型文字；文件名不参与取值过滤
- 同一字段多锚点合并保序去重
"""

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


def _point_anchor(field: str, x: float = 0.0, y: float = 0.0,
                  half: float = 5.0) -> Anchor:
    """单点锚点：占位符覆盖区域 = (x±half, y±half) 的矩形。"""
    return Anchor(
        field=field,
        is_area=False,
        min_x=x - half,
        min_y=y - half,
        max_x=x + half,
        max_y=y + half,
        point_x=x,
        point_y=y,
    )


def test_single_point_anchor_takes_all_in_cover_rect(tmp_path):
    """单点锚点：按占位符覆盖矩形取矩形内全部文字。"""
    dxf = _make_dxf(tmp_path / "a.dxf", [((0, 0), "DW-1001"), ((2, 0), "DW-9999")])
    out = extract_by_anchors(dxf, [_point_anchor("图号")])
    assert out["图号"] == ["DW-1001", "DW-9999"]


def test_single_point_anchor_outside_rect_empty(tmp_path):
    """单点锚点：覆盖矩形外无文字时该字段缺省（NA 由上层处理）。"""
    dxf = _make_dxf(tmp_path / "b.dxf", [((10, 10), "DW-1001")])
    out = extract_by_anchors(dxf, [_point_anchor("图号")])
    assert not out.get("图号")  # 空值列表，上层按 NA 处理


def test_area_anchor_takes_all_in_rect(tmp_path):
    """区域锚点：取矩形内全部编号型文字，矩形外忽略。"""
    dxf = _make_dxf(
        tmp_path / "d.dxf",
        [((1, 1), "PIPE-1"), ((5, 2), "PIPE-2"), ((30, 30), "PIPE-3")],
    )
    anchors = [
        Anchor(
            field="管段",
            is_area=True,
            min_x=0.0,
            min_y=0.0,
            max_x=10.0,
            max_y=10.0,
            point_x=0.0,
            point_y=0.0,
        )
    ]
    out = extract_by_anchors(dxf, anchors)
    assert out["管段"] == ["PIPE-1", "PIPE-2"]


def test_non_id_chars_filtered(tmp_path):
    """含空格/中文等非编号字符的文字被过滤，即使落在覆盖区域内。"""
    dxf = _make_dxf(tmp_path / "e.dxf", [((0, 0), "PIPE-1 中文"), ((2, 0), "PIPE-2")])
    out = extract_by_anchors(dxf, [_point_anchor("管段")])
    assert out["管段"] == ["PIPE-2"]


def test_file_name_like_text_is_not_excluded(tmp_path):
    """与文件名相同的文字不再被排除（文件名不参与取值过滤）。"""
    dxf = _make_dxf(tmp_path / "f.dxf", [((0, 0), "024707VA2292_HAFT-A2DK-B")])
    out = extract_by_anchors(dxf, [_point_anchor("图号")])
    assert out["图号"] == ["024707VA2292_HAFT-A2DK-B"]


def test_chinese_id_chars_kept(tmp_path):
    """M7：中文图号/编号被保留（不再被整体过滤）。"""
    dxf = _make_dxf(tmp_path / "cn.dxf", [((0, 0), "图号甲-01"), ((2, 0), "管段乙_02")])
    out = extract_by_anchors(dxf, [_point_anchor("图号")])
    assert out["图号"] == ["图号甲-01", "管段乙_02"]


def test_chinese_with_space_still_filtered(tmp_path):
    """M7：中文放行但含空格等分隔符的文字仍被过滤。"""
    dxf = _make_dxf(
        tmp_path / "cnsp.dxf", [((0, 0), "图号 甲-01"), ((2, 0), "管段乙-02")]
    )
    out = extract_by_anchors(dxf, [_point_anchor("图号")])
    assert out["图号"] == ["管段乙-02"]  # 空格版被过滤，无空格版被取到


def test_multiple_anchors_merge_dedup(tmp_path):
    """同一字段多个锚点（候选位置）的值合并，保序去重。"""
    dxf = _make_dxf(
        tmp_path / "m.dxf",
        [((0, 0), "PS-1"), ((0, 10), "PS-2"), ((0, 20), "PS-1")],
    )
    anchors = [
        _point_anchor("管段", x=0.0, y=0.0),
        _point_anchor("管段", x=0.0, y=10.0),
        _point_anchor("管段", x=0.0, y=20.0),
    ]
    out = extract_by_anchors(dxf, anchors)
    assert out["管段"] == ["PS-1", "PS-2"]


def test_area_rect_spanning_grid_cells_negative_coords(tmp_path):
    """网格分桶：矩形跨多个网格单元 + 负坐标时取值正确（O(A×T) 优化回归）。

    旧实现逐锚点线性扫全部文字；网格化后按单元探测，跨单元矩形必须
    仍取到落在矩形内的全部文字，且保持文档顺序（插入顺序）。
    """
    dxf = _make_dxf(
        tmp_path / "grid.dxf",
        [
            ((-4.0, -4.0), "NEG-1"),
            ((-1.0, 2.0), "SPAN-2"),
            ((3.0, -1.0), "SPAN-3"),
            ((4.0, 3.0), "SPAN-4"),
            ((30.0, 30.0), "OUT-5"),
        ],
    )
    anchors = [
        Anchor(
            field="区域",
            is_area=True,
            min_x=-5.0,
            min_y=-5.0,
            max_x=5.0,
            max_y=5.0,
            point_x=0.0,
            point_y=0.0,
        )
    ]
    out = extract_by_anchors(dxf, anchors)
    # 矩形 (-5..5, -5..5) 跨多个 5×5 网格单元：取到内部全部、忽略外部
    assert out["区域"] == ["NEG-1", "SPAN-2", "SPAN-3", "SPAN-4"]


def test_large_area_rect_many_texts_order_preserved(tmp_path):
    """大面积区域 + 多文字：结果按文档顺序返回（网格探测不改变取值顺序）。"""
    texts = [((i * 1.0, i // 5 * 1.0), f"V-{i:03d}") for i in range(40)]
    dxf = _make_dxf(tmp_path / "big.dxf", texts)
    anchors = [
        Anchor(
            field="编号",
            is_area=True,
            min_x=-1.0,
            min_y=-1.0,
            max_x=10.0,
            max_y=10.0,
            point_x=0.0,
            point_y=0.0,
        )
    ]
    out = extract_by_anchors(dxf, anchors)
    # 前 11 个点落在矩形内（x<10 或 y<10 的约束下按序保留）
    expect = [t for (x, y), t in texts if -1 <= x <= 10 and -1 <= y <= 10]
    assert out["编号"] == expect
