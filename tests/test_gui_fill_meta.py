"""填表助手模板 meta 化 GUI 行为单测（上传提取 / 无占位符拒绝 / 删除联动）。

不跑真实任务、不碰 ODA：mock 转换与对话框；模板库目录 monkeypatch 到 tmp。
Tk 不可用时 skip（无显示环境）。根窗口用 tk_root 共享夹具。
"""

from __future__ import annotations

import json
from tkinter import ttk
from unittest import mock

import ezdxf

from cadbatchassistant.core.common.template_meta import (
    meta_path_for,
    save_template_meta,
)
from cadbatchassistant.core.common.templates import template_path


def _make_panel(root, monkeypatch, tmp_path):
    """构造 IsoFillApp，模板库目录与面板记忆配置全部隔离到 tmp。"""
    import cadbatchassistant.core.common.templates as tpl_mod
    from cadbatchassistant.gui.mixins import gui_shared as gs
    from cadbatchassistant.gui.panels import gui_fill as gf

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


def test_upload_generates_meta(tk_root, monkeypatch, tmp_path):
    """上传成功 → 从源文件扫描全部占位符写入模板库 meta，模板被选中。"""
    panel, gf, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    src = tmp_path / "src.dxf"  # 源文件在模板库之外
    doc = ezdxf.new("R2004")
    doc.modelspace().add_text(
        "[图号]", dxfattribs={"insert": (10, 10), "height": 3.0}
    )
    doc.saveas(src)
    conv = mock.Mock()
    conv.resolve.return_value = ""
    conv.template_to_dxf.return_value = str(src)  # DXF 模板直接扫描
    with (
        mock.patch.object(
            gs, "upload_template_file",
            return_value=("tpl.dxf", str(src)),
        ),
        mock.patch.object(gf.dc, "get_converter", return_value=conv),
    ):
        panel._upload_template("ignored.dxf")
    meta_p = tmp_path / "templates" / "fill" / "tpl.dxf.json"
    assert meta_p.is_file()  # 只存 meta，不复制原文件
    assert not (tmp_path / "templates" / "fill" / "tpl.dxf").exists()
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    assert meta["placeholders"][0]["text"] == "图号"
    assert meta["placeholders"][0]["entity_desc"]["dxftype"] == "TEXT"
    assert panel.var_template.get() == "tpl.dxf"


def test_upload_no_placeholder_rejected(tk_root, monkeypatch, tmp_path):
    """模板无 [列名] 占位符 → 拒绝上传（回滚删除 meta + 弹错 + 不选中）。"""
    panel, gf, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    tpl = _seed_template(tmp_path, with_placeholder=False)
    conv = mock.Mock()
    conv.resolve.return_value = ""
    conv.template_to_dxf.return_value = str(tpl)
    with (
        mock.patch.object(
            gs, "upload_template_file",
            return_value=("tpl.dxf", "ignored.dxf"),
        ),
        mock.patch.object(gf.dc, "get_converter", return_value=conv),
        mock.patch.object(gs.messagebox, "showerror") as err,
    ):
        panel._upload_template("ignored.dxf")
    assert not meta_path_for(tpl).exists()  # 回滚：未留下 meta
    assert tpl.exists()  # 遗留原文件不受影响
    err.assert_called_once()
    assert panel.var_template.get() != "tpl.dxf"


def test_prepare_run_accepts_meta_without_template_file(tk_root, monkeypatch, tmp_path):
    """模板库只存 meta（无原文件）时，选中模板即可通过预检开始处理。

    回归：上一轮改为「上传只存占位符 JSON」后，_prepare_run 仍用
    os.path.isfile 检查库内原文件 → 恒失败误报「请从图纸模板下拉框选择模板」。
    """
    panel, gf, _ = _make_panel(tk_root, monkeypatch, tmp_path)
    src = tmp_path / "src.dxf"  # 库外源文件（模拟已上传）
    doc = ezdxf.new("R2004")
    doc.modelspace().add_text(
        "[图号]", dxfattribs={"insert": (10, 10), "height": 3.0}
    )
    doc.saveas(src)
    save_template_meta(
        template_path("fill", "tpl.dxf"),
        {
            "placeholders": [
                {
                    "text": "图号",
                    "layer": "0",
                    "x": 10.0,
                    "y": 10.0,
                    "height": 3.0,
                    "style": "",
                    "halign": 0,
                    "valign": 0,
                    "ref_text": "[图号]",
                    "entity_desc": {
                        "dxftype": "TEXT",
                        "attribs": {
                            "layer": "0",
                            "insert": (10.0, 10.0, 0.0),
                            "height": 3.0,
                            "style": "",
                            "halign": 0,
                            "valign": 0,
                        },
                        "layer_attribs": None,
                        "style_attribs": None,
                    },
                }
            ]
        },
    )
    assert not (tmp_path / "templates" / "fill" / "tpl.dxf").exists()  # 无原文件
    xlsx = tmp_path / "tpl.xlsx"
    xlsx.write_text("x", encoding="utf-8")
    panel.var_template.set("tpl.dxf")
    panel.var_xlsx.set(str(xlsx))
    panel.var_sheet.set("")
    panel.var_match_col.set("")
    panel.scanned_files = [str(tmp_path / "a.dxf")]
    panel.var_out.set(str(tmp_path / "out"))
    with mock.patch.object(
        gf, "get_app_runtime_config", return_value=("", "ACAD2013")
    ):
        args = panel._prepare_run()
    assert args is not None  # 预检通过，不再误报「请选择模板」
    assert args[1].endswith("tpl.dxf")  # 模板虚拟路径传入 pipeline


def test_prepare_run_bad_placeholder_structure_rejected(tk_root, monkeypatch, tmp_path):
    """手改 meta：占位符缺键 → 预检报「配置损坏」并返回 None（不进入后台）。"""
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    src = tmp_path / "src.dxf"
    doc = ezdxf.new("R2004")
    doc.modelspace().add_text(
        "[图号]", dxfattribs={"insert": (10, 10), "height": 3.0}
    )
    doc.saveas(src)
    save_template_meta(
        template_path("fill", "tpl.dxf"),
        {"placeholders": [{"text": "图号"}]},  # 缺 layer/x/y 等键
    )
    xlsx = tmp_path / "tpl.xlsx"
    xlsx.write_text("x", encoding="utf-8")
    panel.var_template.set("tpl.dxf")
    panel.var_xlsx.set(str(xlsx))
    panel.var_sheet.set("")
    panel.var_match_col.set("")
    panel.scanned_files = [str(tmp_path / "a.dxf")]
    panel.var_out.set(str(tmp_path / "out"))
    with mock.patch.object(gs.messagebox, "showerror") as err:
        assert panel._prepare_run() is None
    err.assert_called_once()
    assert "配置损坏" in err.call_args[0][1]


def test_delete_removes_meta(tk_root, monkeypatch, tmp_path):
    """删除模板 → 伴生 meta 同步删除。"""
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    tpl = _seed_template(tmp_path)
    save_template_meta(tpl, {"placeholders": []})
    assert meta_path_for(tpl).is_file()
    panel.var_template.set("tpl.dxf")
    with mock.patch.object(
        gs, "delete_template_file", return_value=True
    ):
        panel._delete_template()
    assert not meta_path_for(tpl).exists()


def _seed_meta_entry(tmp_path, name="tpl.dxf"):
    """在隔离的模板库中写入一条 meta JSON（无原文件，模拟已上传）。"""
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True, exist_ok=True)
    (d / (name + ".json")).write_text(
        json.dumps({"version": 1, "source": name, "placeholders": []}),
        encoding="utf-8",
    )


def test_edit_template_saves_and_refreshes(tk_root, monkeypatch, tmp_path):
    """编辑保存成功 → 对话框以本面板分类/模板名/宿主调用，并刷新下拉。"""
    _seed_meta_entry(tmp_path)  # 先入库，供构造时下拉枚举
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    panel.var_template.set("tpl.dxf")
    with mock.patch.object(
        gs, "edit_template_file", return_value=True
    ) as edit_mock:
        panel._edit_template()
    assert edit_mock.call_count == 1
    call = edit_mock.call_args
    assert call.args[0] == "fill"
    assert call.args[1] == "tpl.dxf"
    assert call.kwargs["parent"] is panel._root
    # 保存后刷新下拉：库内那条 meta 仍被枚举保留
    assert "tpl.dxf" in list(panel.tpl_combo["values"])


def test_edit_template_cancel_does_not_save(tk_root, monkeypatch, tmp_path):
    """编辑取消（返回 False）→ 不刷新、不写面板记忆。"""
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    _seed_meta_entry(tmp_path)
    panel.var_template.set("tpl.dxf")
    with (
        mock.patch.object(
            gs, "edit_template_file", return_value=False
        ) as edit_mock,
        mock.patch.object(gs, "save_panel_config") as save_cfg,
    ):
        panel._edit_template()
    edit_mock.assert_called_once()
    save_cfg.assert_not_called()


def test_edit_template_no_selection_warns(tk_root, monkeypatch, tmp_path):
    """未选择模板时点「编辑」→ 弹错提示，且不打开编辑对话框。"""
    panel, _, gs = _make_panel(tk_root, monkeypatch, tmp_path)
    panel.var_template.set("")
    with (
        mock.patch.object(gs, "edit_template_file") as edit_mock,
        mock.patch.object(gs.messagebox, "showwarning") as warn,
    ):
        panel._edit_template()
    edit_mock.assert_not_called()
    warn.assert_called_once()
    assert "编辑" in warn.call_args[0][1]
