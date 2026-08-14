"""GUI 通用工具：字体 / 主题 / 窗口居中 / 路径去重 / 拖放数据解析。"""

from __future__ import annotations

import contextlib
import os
import tkinter as tk
from tkinter import ttk


def default_font_family() -> str:
    """Windows 上优先使用微软雅黑，保证中文显示清晰。"""
    try:
        from tkinter import font as tkfont

        installed = set(tkfont.families())
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
            if name in installed:
                return name
    except Exception:  # noqa: BLE001
        pass
    return "TkDefaultFont"


def dedup_paths(paths) -> list:
    """路径去重（Windows 大小写不敏感），保持原顺序。"""
    seen: set[str] = set()
    out = []
    for p in paths:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def apply_vista_theme(style: tk.ttk.Style | None = None) -> None:
    """尝试使用 vista 主题；不可用时静默回退默认主题。"""
    if style is None:
        style = ttk.Style()
    with contextlib.suppress(tk.TclError):
        style.theme_use("vista")


def center_window(win, parent: tk.Misc | None = None) -> None:
    """让窗口相对 parent 居中；parent 为空时相对屏幕居中。

    需在窗口内容布局完成后调用（内部 update_idletasks 取实际尺寸），
    供主窗口（屏幕居中）与各 Toplevel 弹窗（相对主窗口居中）复用。
    """
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if parent is not None:
        root = parent.winfo_toplevel()
        base_x, base_y = root.winfo_rootx(), root.winfo_rooty()
        base_w, base_h = root.winfo_width(), root.winfo_height()
    else:
        base_x = base_y = 0
        base_w, base_h = win.winfo_screenwidth(), win.winfo_screenheight()
    x = base_x + max(0, (base_w - w) // 2)
    y = base_y + max(0, (base_h - h) // 2)
    win.geometry(f"+{x}+{y}")


def parse_dnd_data(data: str) -> list[str]:
    """解析 tkdnd 拖拽数据为路径列表（优先用 tkdnd 标准 splitlist）。"""
    try:
        r = tk.Tcl()
        return [p for p in r.splitlist(data) if p.strip()]
    except Exception:  # noqa: BLE001 - 回退手写解析
        out: list[str] = []
        i = 0
        while i < len(data):
            if data[i] == "{":
                j = data.find("}", i)
                if j == -1:
                    break
                out.append(data[i + 1 : j])
                i = j + 1
            else:
                j = data.find(" ", i)
                if j == -1:
                    out.append(data[i:])
                    break
                out.append(data[i:j])
                i = j + 1
        return [p for p in out if p.strip()]
