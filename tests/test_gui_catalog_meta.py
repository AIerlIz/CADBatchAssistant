"""目录助手模板 meta 化 GUI 行为单测（上传提取 / 回滚 / 删除联动 / 预检只读 meta）。

不跑真实任务、不碰 ODA：mock 解析与对话框；模板库目录 monkeypatch 到 tmp。
Tk 不可用时 skip（无显示环境）。根窗口用 tk_root 共享夹具。
"""

from __future__ import annotations

import json
from tkinter import ttk
from unittest import mock

from cadbatchassistant.core.catalog.catalog_template_reader import Anchor
from cadbatchassistant.core.common.template_meta import (
    meta_path_for,
    save_template_meta,
)


def _make_panel(root, monkeypatch, tmp_path):
    """构造 CatalogPanel，模板库目录与面板记忆配置全部隔离到 tmp。"""
    import cadbatchassistant.core.common.templates as tpl_mod
    from cadbatchassistant.gui.mixins import gui_shared as gs
    from cadbatchassistant.gui.panels import gui_catalog as gc

    monkeypatch.setattr(tpl_mod, "software_dir", lambda: tmp_path)
    monkeypatch.setattr(gc, "load_panel_config", lambda: {})
    monkeypatch.setattr(gc, "save_panel_config", lambda d: None)
    panel = gc.CatalogPanel(ttk.Frame(root))
    return panel, gc, gs


def _seed_template(tmp_path, name="tpl.dwg"):
    """在隔离的模板库中真实创建模板文件（模拟 upload_template_file 已复制）。"""
    d = tmp_path / "templates" / "catalog"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("x", encoding="utf-8")
    return p


def test_upload_generates_meta(tk_root, monkeypatch, tmp_path):
    """上传成功 → 从源文件提取占位符并写入模板库 meta，模板被选中。"""
    panel, gc, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    anchors = [Anchor(field="图号", is_area=False, min_x=1.0, min_y=2.0,
                      max_x=3.0, max_y=4.0, point_x=2.0, point_y=3.0)]
    with (
        mock.patch.object(
            gs, "upload_template_file",
            return_value=("tpl.dwg", "ignored.dwg"),
        ),
        mock.patch.object(
            gc, "parse_template_anchors", return_value=anchors
        ) as parse_mock,
    ):
        panel._upload_template("ignored.dwg")
        assert parse_mock.call_count == 1
        assert parse_mock.call_args[0][0] == "ignored.dwg"  # 从源文件解析
    meta_p = tmp_path / "templates" / "catalog" / "tpl.dwg.json"
    assert meta_p.is_file()  # 只存 meta，不复制原文件
    assert not (tmp_path / "templates" / "catalog" / "tpl.dwg").exists()
    load_meta = json.loads(meta_p.read_text(encoding="utf-8"))
    assert load_meta["fields"] == ["图号"]
    assert load_meta["anchors"][0]["field"] == "图号"
    assert panel.var_template.get() == "tpl.dwg"


def test_upload_parse_failure_rolls_back(tk_root, monkeypatch, tmp_path):
    """解析失败（如无占位符）→ 回滚删除已入库 meta，弹错、不选中。"""
    panel, gc, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    tpl = _seed_template(tmp_path)
    meta_p = meta_path_for(tpl)
    meta_p.write_text("{}", encoding="utf-8")  # 模拟钩子已写入 meta
    with (
        mock.patch.object(
            gs, "upload_template_file",
            return_value=("tpl.dwg", "ignored.dwg"),
        ),
        mock.patch.object(
            gc, "parse_template_anchors",
            side_effect=ValueError("模板中未找到 [字段名] 占位符"),
        ),
        mock.patch.object(gs.messagebox, "showerror") as err,
    ):
        panel._upload_template("ignored.dwg")
    assert not meta_p.exists()  # 回滚：meta 已删除
    assert tpl.exists()  # 遗留原文件不受影响（回滚只清理 meta）
    err.assert_called_once()
    assert panel.var_template.get() != "tpl.dwg"


def test_delete_removes_meta(tk_root, monkeypatch, tmp_path):
    """删除模板 → 伴生 meta 同步删除。"""
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    tpl = _seed_template(tmp_path)
    save_template_meta(tpl, {"fields": ["图号"], "anchors": []})
    assert meta_path_for(tpl).is_file()
    panel.var_template.set("tpl.dwg")
    with mock.patch.object(
        gs, "delete_template_file", return_value=True
    ):
        panel._delete_template()
    assert not meta_path_for(tpl).exists()


def test_prepare_run_reads_meta_without_parsing(tk_root, monkeypatch, tmp_path):
    """预检：meta 有效时直接使用其锚点/字段，不再现场解析模板。

    模板库只存 meta JSON（无原文件）即可通过预检。
    """
    panel, gc, _ = _make_panel(tk_root, monkeypatch, tmp_path)
    tpl = tmp_path / "templates" / "catalog" / "tpl.dwg"
    save_template_meta(tpl, {
        "fields": ["图号"],
        "anchors": [{
            "field": "图号", "is_area": False,
            "min_x": 1.0, "min_y": 2.0, "max_x": 3.0, "max_y": 4.0,
            "point_x": 2.0, "point_y": 3.0,
        }],
    })
    xlsx = tmp_path / "tpl.xlsx"
    xlsx.write_text("x", encoding="utf-8")
    panel.var_template.set("tpl.dwg")
    panel.var_xlsx.set(str(xlsx))
    panel.scanned_files = [str(tmp_path / "a.dxf")]
    panel.var_out.set(str(tmp_path / "out"))
    fake_wb = mock.Mock()
    with (
        mock.patch.object(gc, "get_app_runtime_config", return_value=("", "ACAD2013")),
        mock.patch.object(gc, "load_catalog_rules", return_value={}),
        mock.patch.object(
            gc, "load_workbook", return_value=fake_wb
        ),
        mock.patch.object(
            gc, "detect_sheet_candidates", return_value=[(3, "Sheet1")]
        ),
        mock.patch.object(
            gc, "parse_template_anchors",
            side_effect=AssertionError("预检不应再解析模板"),
        ) as parse_mock,
    ):
        args = panel._prepare_run()
    assert args is not None
    # 返回的 anchors 来自 meta（field=图号），而非解析
    assert [a.field for a in args[8]] == ["图号"]
    assert args[7] is None  # sheet_name：单候选不弹窗
    assert parse_mock.call_count == 0
    fake_wb.close.assert_called_once()


def test_prepare_run_meta_missing_reports_error(tk_root, monkeypatch, tmp_path):
    """预检：meta 缺失 → 报错「未配置，请重新上传」且不触发解析。"""
    panel, gc, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    _seed_template(tmp_path)
    xlsx = tmp_path / "tpl.xlsx"
    xlsx.write_text("x", encoding="utf-8")
    panel.var_template.set("tpl.dwg")
    panel.var_xlsx.set(str(xlsx))
    panel.scanned_files = [str(tmp_path / "a.dxf")]
    panel.var_out.set(str(tmp_path / "out"))
    with (
        mock.patch.object(gc, "get_app_runtime_config", return_value=("", "")),
        mock.patch.object(gs.messagebox, "showerror") as err,
        mock.patch.object(
            gc, "parse_template_anchors",
            side_effect=AssertionError("meta 缺失时不应解析模板"),
        ) as parse_mock,
    ):
        assert panel._prepare_run() is None
    assert err.call_count == 1
    assert "未配置" in err.call_args[0][1]
    assert parse_mock.call_count == 0
