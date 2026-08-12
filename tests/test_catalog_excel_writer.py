"""catalog_excel_writer 表头反推与样式模板单测（目录助手）。"""

from openpyxl import Workbook, load_workbook

from cadbatchassistant.core.catalog_excel_writer import (
    detect_header_row,
    detect_sheet,
    detect_sheet_candidates,
    write_style_template,
)


def _ws_with_rows(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    return ws


def test_detect_header_row_first_line():
    ws = _ws_with_rows([["管段编号", "图号", "页码"]])
    assert detect_header_row(ws, ["管段编号", "图号"]) == 1


def test_detect_header_row_after_title_lines():
    ws = _ws_with_rows([["某设计院"], ["项目名称"], ["管段编号", "图号", "页码"]])
    assert detect_header_row(ws, ["管段编号", "图号"]) == 3


def test_detect_header_row_no_match_returns_none():
    ws = _ws_with_rows([["名称", "备注", "说明"]])
    assert detect_header_row(ws, ["管段编号", "图号"]) is None


def test_detect_header_row_picks_best_match_row():
    ws = _ws_with_rows([
        ["图号"],                       # 命中 1 个
        ["管段编号", "图号", "页码"],   # 命中 2 个 → 表头
        ["管段编号"],                   # 命中 1 个
    ])
    assert detect_header_row(ws, ["管段编号", "图号"]) == 2


def test_detect_header_row_empty_fields():
    ws = _ws_with_rows([["管段编号", "图号"]])
    assert detect_header_row(ws, []) is None


def test_write_style_template_with_fields(tmp_path):
    p = tmp_path / "style.xlsx"
    write_style_template(p, fields=["管段编号", "图号", "页码"])
    ws = load_workbook(p).active
    assert detect_header_row(ws, ["管段编号", "图号"]) == 1


def test_write_style_template_default_headers(tmp_path):
    p = tmp_path / "style.xlsx"
    write_style_template(p)
    ws = load_workbook(p).active
    assert ws.cell(row=1, column=1).value == "字段名"
    assert ws.cell(row=1, column=3).value == "页码"


# ---------------- detect_sheet / detect_sheet_candidates ----------------


def _multi_sheet_wb():
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "说明页"
    ws1.append(["无关内容"])
    ws2 = wb.create_sheet("目录表")
    ws2.append(["管段编号", "图号", "页码"])
    return wb


def test_detect_sheet_second_sheet():
    wb = _multi_sheet_wb()
    hit = detect_sheet(wb, ["管段编号", "图号"])
    assert hit is not None
    ws, hr = hit
    assert ws.title == "目录表" and hr == 1


def test_detect_sheet_candidates_sorted_by_score():
    wb = _multi_sheet_wb()
    cands = detect_sheet_candidates(wb, ["管段编号", "图号"])
    assert [(c[0], c[1].title) for c in cands] == [(2, "目录表")]


def test_detect_sheet_tie_keeps_first_and_order_stable():
    wb = Workbook()
    ws_a = wb.active
    ws_a.title = "目录A"
    ws_a.append(["管段编号", "图号", "页码"])
    ws_b = wb.create_sheet("目录B")
    ws_b.append(["管段编号", "图号", "页码"])
    cands = detect_sheet_candidates(wb, ["管段编号", "图号"])
    assert len(cands) == 2
    assert cands[0][0] == cands[1][0] == 2
    assert [c[1].title for c in cands] == ["目录A", "目录B"]
    hit = detect_sheet(wb, ["管段编号", "图号"])
    assert hit[0].title == "目录A"


def test_detect_sheet_no_match():
    wb = Workbook()
    wb.active.title = "无"
    wb.active.append(["名称", "备注"])
    assert detect_sheet(wb, ["管段编号", "图号"]) is None
    assert detect_sheet_candidates(wb, ["管段编号", "图号"]) == []
