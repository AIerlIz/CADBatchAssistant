"""pytest 共享夹具（GUI 面板/对话框测试的 Tk 根窗口统一样板）。

多个 GUI 测试文件各自复制「TkinterDnD.Tk() + 无显示环境 skip + withdraw +
destroy」样板（test_gui_fill_meta / test_gui_catalog_meta / test_gui_panels_smoke），
这里收敛为一个 tk_root 夹具：Tk 不可用（无显示环境）时整测 skip，
测试结束自动销毁窗口，削减重复样板。
"""

from __future__ import annotations

import tkinter as tk

import pytest


@pytest.fixture
def tk_root():
    """构造一个已 withdraw 的 TkinterDnD 根窗口；无显示环境（TclError）
    时整测 skip（与 main.py 一致使用 TkinterDnD.Tk 以支持拖放）。
    测试结束自动销毁，测试体无需手写 try/finally。
    """
    from tkinterdnd2 import TkinterDnD

    try:
        root = TkinterDnD.Tk()
    except tk.TclError as ex:  # 无显示环境
        pytest.skip(f"Tk 不可用（无显示环境）: {ex}")
    root.withdraw()  # 不弹出窗口
    yield root
    root.destroy()
