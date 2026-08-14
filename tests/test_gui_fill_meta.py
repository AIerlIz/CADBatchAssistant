"""填表助手模板 meta 化 GUI 行为单测（上传提取 / 无占位符拒绝 / 删除联动）。

不跑真实任务、不碰 ODA：mock 转换与对话框；模板库目录 monkeypatch 到 tmp。
Tk 不可用时 skip（无显示环境）。
"""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk
from unittest import mock

import ezdxf
import pytest

from cadbatchassistant.core.template_meta import meta_path_for, save_template_meta


def _make_root():
    from tkinterdnd2 import TkinterDnD

    try:
        root = TkinterDnD.Tk()
    except tk.TclError as ex:  # 无显示环境
        pytest.skip(f"Tk 不可用（无显示环境）: {ex}")
    root.withdraw()  # 不弹出窗口
    return root


def _make_panel(root, monkeypatch, tmp_path):
    """构造 IsoFillApp，模板库目录与面板记忆配置全部隔离到 tmp。"""
    import cadbatchassistant.core.templates as tpl_mod
    from cadbatchassistant.gui import gui_fill as gf
    from cadbatchassistant.gui import gui_shared as gs

    monkeypatch.setattr(tpl_mod, "software_dir", lambda: tmp_path)
    monkeypatch.setattr(gs, "load_panel_config", lambda: {})
    monkeypatch.setattr(gs, "save_panel_config", lambda d: None)
    panel = gf.IsoFillApp(ttk.Frame(root))
    return panel, gf, gs


def _seed_template(tmp_path, name="tpl.dxf", with_placeholder=True):
    """在隔离的模板库中真实创建模板 DXF（模拟 upload_template_file 已复制）。"""
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    doc = ezdxf.new("R2004")
    if with_placeholder:
        doc.modelspace().add_text(
            "[图号]", dxfattribs={"insert": (10, 10), "height": 3.0}
        )
    doc.saveas(p)
    return p


def test_upload_generates_meta(monkeypatch, tmp_path):
    """上传成功 → 扫描全部占位符写入伴生 meta，模板被选中。"""
    root = _make_root()
    try:
        panel, gf, gs = _make_panel(root, monkeypatch, tmp_path)
        tpl = _seed_template(tmp_path)
        conv = mock.Mock()
        conv.resolve.return_value = ""
        conv.template_to_dxf.return_value = str(tpl)  # DXF 模板直接扫描
        with (
            mock.patch.object(
                gs, "upload_template_file", return_value="tpl.dxf"
            ),
            mock.patch.object(gf.dc, "get_converter", return_value=conv),
        ):
            panel._upload_template("ignored.dxf")
        meta = json.loads(meta_path_for(tpl).read_text(encoding="utf-8"))
        assert meta["placeholders"][0]["text"] == "图号"
        assert meta["placeholders"][0]["entity_desc"]["dxftype"] == "TEXT"
        assert panel.var_template.get() == "tpl.dxf"
    finally:
        root.destroy()


def test_upload_no_placeholder_rejected(monkeypatch, tmp_path):
    """模板无 [列名] 占位符 → 拒绝上传（回滚删除 + 弹错 + 不选中）。"""
    root = _make_root()
    try:
        panel, gf, gs = _make_panel(root, monkeypatch, tmp_path)
        tpl = _seed_template(tmp_path, with_placeholder=False)
        conv = mock.Mock()
        conv.resolve.return_value = ""
        conv.template_to_dxf.return_value = str(tpl)
        with (
            mock.patch.object(
                gs, "upload_template_file", return_value="tpl.dxf"
            ),
            mock.patch.object(gf.dc, "get_converter", return_value=conv),
            mock.patch.object(gs.messagebox, "showerror") as err,
        ):
            panel._upload_template("ignored.dxf")
        assert not tpl.exists()  # 回滚：模板文件已删除
        assert not meta_path_for(tpl).exists()
        err.assert_called_once()
        assert panel.var_template.get() != "tpl.dxf"
    finally:
        root.destroy()


def test_delete_removes_meta(monkeypatch, tmp_path):
    """删除模板 → 伴生 meta 同步删除。"""
    root = _make_root()
    try:
        panel, _, gs = _make_panel(root, monkeypatch, tmp_path)
        tpl = _seed_template(tmp_path)
        save_template_meta(tpl, {"placeholders": []})
        assert meta_path_for(tpl).is_file()
        panel.var_template.set("tpl.dxf")
        with mock.patch.object(
            gs, "delete_template_file", return_value=True
        ):
            panel._delete_template()
        assert not meta_path_for(tpl).exists()
    finally:
        root.destroy()
