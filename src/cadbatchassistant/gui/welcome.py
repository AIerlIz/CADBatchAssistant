"""「首次使用引导」欢迎窗口。

首次启动（或用户在设置页手动打开）时展示：欢迎语、版本号、
三大功能简介与 DWG 处理提示。关闭（按钮或窗口 X）统一走
_close() 写入 welcome_seen 标记，避免每次启动重复弹出。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cadbatchassistant import __version__
from cadbatchassistant.common import (
    WELCOME_SEEN_KEY,
    center_window,
    mark_welcome_seen,
    resource_path,
    save_app_config,
)

# 三大功能简介（文案与 README 保持一致）
_FEATURES = [
    ("改字助手", "批量修改 DWG/DXF 图纸文字（TEXT/MTEXT/块属性），支持正则查找替换"),
    ("填表助手", "把数据表（.xlsx/.xls）按「图纸模板占位」填入图纸标题栏"),
    ("目录助手", "按「图纸模板」从一批图纸取值，生成图纸目录 Excel"),
]


class WelcomeDialog:
    """模态欢迎窗口：展示功能简介，关闭后标记已见。

    关闭路径统一为 _close()：写入 welcome_seen 标记并销毁窗口；
    「开始使用」与「稍后再说」行为一致（仅文案不同）。
    """

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        self._root = parent.winfo_toplevel()
        self._win = tk.Toplevel(parent)
        self._win.title(f"欢迎使用 CAD批处理助手 v{__version__}")
        self._win.transient(self._root)
        self._win.resizable(False, False)
        self._win.protocol("WM_DELETE_WINDOW", self._close)
        self._build_ui()
        center_window(self._win, self._root)  # 相对主窗口居中
        self._win.grab_set()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}
        main = ttk.Frame(self._win, padding=16)
        main.pack(fill="both", expand=True)

        # 顶部 logo（缺失时静默跳过）
        try:
            logo = tk.PhotoImage(file=resource_path("assets/logo.png"))
            self._logo = logo  # 保持引用防 GC
            ttk.Label(main, image=logo).pack()
        except tk.TclError:
            pass

        ttk.Label(main, text="欢迎使用 CAD批处理助手",
                  font=("", 16, "bold")).pack(**pad)
        ttk.Label(
            main,
            text=f"当前版本 v{__version__}。一个窗口完成图纸批处理："
                 "下面三个功能页可按需切换使用。",
            wraplength=440, justify="left",
        ).pack(**pad)

        feat = ttk.LabelFrame(main, text="三大功能", padding=10)
        feat.pack(fill="x", **pad)
        for i, (name, desc) in enumerate(_FEATURES):
            ttk.Label(feat, text=f"· {name}", font=("", 10, "bold")
                      ).grid(row=i, column=0, sticky="nw", padx=(4, 8))
            ttk.Label(feat, text=desc, wraplength=360, justify="left"
                      ).grid(row=i, column=1, sticky="w", pady=2)

        ttk.Label(
            main,
            text="提示：处理 DWG 需在「设置」页配置 ODA File Converter，"
                 "纯 DXF 场景无需。",
            wraplength=440, justify="left", foreground="#555555",
        ).pack(**pad)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill="x", pady=(10, 0))
        ttk.Button(btn_row, text="开始使用",
                   command=self._close).pack(side="right")
        ttk.Button(btn_row, text="稍后再说",
                   command=self._close).pack(side="right", padx=(0, 8))

    # ---------------- 关闭 ----------------
    def _close(self) -> None:
        """写入已见标记并关闭窗口（按钮与窗口 X 统一走此路径）。"""
        mark_welcome_seen()
        self._win.destroy()


def reopen_welcome(parent: tk.Widget) -> None:
    """清空已见标记并重新弹出引导窗口（设置页「重新显示使用引导」用）。

    清标记仅影响「是否需要自动弹出」的判定；用户关闭引导时
    又会写回 True，因此不会导致下次启动重复自动弹出。
    """
    save_app_config({WELCOME_SEEN_KEY: False})
    WelcomeDialog(parent)
