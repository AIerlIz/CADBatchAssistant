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


class Tooltip:
    """通用的鼠标悬停提示（tooltip）：绑定到任意 widget，悬停延迟弹出。

    用法：
        Tooltip(widget, text="说明文案")
        tip = Tooltip(widget)          # 之后 tip.set_text("更新文案")
        tip.set_text("...")            # 可传任意值；动态更新已显示的提示
    悬停经 delay 毫秒后显示带边框的小窗口，移出/点击即隐藏。
    仅新增 <Enter>/<Leave>/<ButtonPress> 绑定（add="+"），不覆盖 widget
    原绑定。全屏坐标取 winfo_pointerx/y 以支持多屏。
    """

    def __init__(
        self,
        widget: tk.Misc,
        text: str = "",
        delay: int = 500,
        bg: str = "#FFFDE7",
        fg: str = "#333333",
        wrap: int = 480,
    ) -> None:
        self.widget = widget
        self._text = str(text)
        self._delay = delay
        self._bg = bg
        self._fg = fg
        self._wrap = wrap
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def set_text(self, text) -> None:
        """更新提示文案（任意值转 str；动态更新已显示的 tip 文案）。"""
        self._text = str(text)

    def _on_enter(self, _event) -> None:
        if not self._text:
            return
        if self._after is not None:
            self.widget.after_cancel(self._after)
        self._after = self.widget.after(self._delay, self._show)

    def _on_leave(self, _event=None) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        self._hide()

    def _show(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
        x = self.widget.winfo_pointerx() + 12
        y = self.widget.winfo_pointery() + 12
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tip,
            text=self._text,
            justify="left",
            background=self._bg,
            foreground=self._fg,
            relief="solid",
            borderwidth=1,
            wraplength=self._wrap,
            anchor="w",
            padx=6,
            pady=4,
        ).pack()
        self._tip = tip

    def _hide(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
