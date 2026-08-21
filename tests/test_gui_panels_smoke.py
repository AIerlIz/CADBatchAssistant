"""三个功能面板的轻量冒烟测试（PHASE 2/3 拆分为 Mixin 重构后的组装回归）。

构造面板（不跑真实任务、不碰 ODA），校验关键控件与默认状态存在；
Tk 不可用时 skip（无显示环境，如部分 CI）。
"""

from __future__ import annotations

from tkinter import ttk


def test_text_panel_builds(tk_root) -> None:
    from cadbatchassistant.gui.panels.gui_text import TextPanel

    panel = TextPanel(ttk.Frame(tk_root))
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


def test_fill_panel_builds(tk_root) -> None:
    from cadbatchassistant.gui.panels.gui_fill import FillPanel

    panel = FillPanel(ttk.Frame(tk_root))
    assert panel.scanned_files == []
    assert panel.var_xlsx is not None
    assert panel.var_template is not None
    assert panel.tpl_combo is not None
    assert panel.sheet_combo is not None
    assert panel.match_combo is not None
    assert panel.var_out is not None
    assert panel.btn_start is not None
    assert panel.log_text is not None


def test_catalog_panel_builds(tk_root) -> None:
    from cadbatchassistant.gui.panels.gui_catalog import CatalogPanel

    panel = CatalogPanel(ttk.Frame(tk_root))
    assert panel.scanned_files == []
    assert panel.var_xlsx is not None
    assert panel.var_template is not None
    assert panel.tpl_combo is not None
    assert panel.var_out is not None
    assert panel.btn_start is not None
    assert panel.log_text is not None
    assert panel._last_result is None
