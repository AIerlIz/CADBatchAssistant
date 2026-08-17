"""三个功能面板的轻量冒烟测试（PHASE 2/3 拆分与 Mixin 重构后的组装回归）。

构造面板（不跑真实任务、不碰 ODA），校验关键控件与默认状态存在；
Tk 不可用时 skip（无显示环境，如部分 CI）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest


def _make_root():
    # 面板拖放依赖 tkdnd 扩展，须用 TkinterDnD.Tk()（与 main.py 一致）
    from tkinterdnd2 import TkinterDnD

    try:
        root = TkinterDnD.Tk()
    except tk.TclError as ex:  # 无显示环境
        pytest.skip(f"Tk 不可用（无显示环境）: {ex}")
    root.withdraw()  # 不弹出窗口
    return root


def test_text_panel_builds() -> None:
    from cadbatchassistant.gui.panels.gui_text import CadTextApp

    root = _make_root()
    try:
        panel = CadTextApp(ttk.Frame(root))
        assert panel.scanned_files == []
        assert panel.rules_data == []
        assert panel.file_list is not None
        assert panel.var_scan_info is not None
        assert panel.var_out is not None
        assert panel.btn_start is not None
        assert panel.btn_stop is not None
        assert panel.progress is not None
        assert panel.log_text is not None
        assert panel.running is False
    finally:
        root.destroy()


def test_fill_panel_builds() -> None:
    from cadbatchassistant.gui.panels.gui_fill import IsoFillApp

    root = _make_root()
    try:
        panel = IsoFillApp(ttk.Frame(root))
        assert panel.scanned_files == []
        assert panel.var_xlsx is not None
        assert panel.var_template is not None
        assert panel.tpl_combo is not None
        assert panel.sheet_combo is not None
        assert panel.match_combo is not None
        assert panel.var_out is not None
        assert panel.btn_start is not None
        assert panel.log_text is not None
    finally:
        root.destroy()


def test_catalog_panel_builds() -> None:
    from cadbatchassistant.gui.panels.gui_catalog import CatalogPanel

    root = _make_root()
    try:
        panel = CatalogPanel(ttk.Frame(root))
        assert panel.scanned_files == []
        assert panel.var_xlsx is not None
        assert panel.var_template is not None
        assert panel.tpl_combo is not None
        assert panel.var_out is not None
        assert panel.btn_start is not None
        assert panel.log_text is not None
        assert panel._last_result is None
    finally:
        root.destroy()
