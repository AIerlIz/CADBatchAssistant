"""程序入口：启动「CAD批处理助手」统一窗口（Notebook 三个 tab 切换）。

- 「改字助手」：CadTextApp（gui.py）—— DWG/DXF 文字正则替换
- 「填表助手」：IsoFillApp（gui_fill.py）—— 数据表填入图纸标题栏
- 「设置」：SettingsPanel（settings.py）—— ODA 路径与 DWG 输出版本（全局共享）

根窗口使用 TkinterDnD.Tk，为填表面板提供文件拖放支持。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from tkinterdnd2 import TkinterDnD

from cadbatchassistant.common import default_font_family
from cadbatchassistant.gui import gui, gui_fill, settings

APP_TITLE = "CAD批处理助手"


def main() -> None:
    root = TkinterDnD.Tk()
    root.title(APP_TITLE)
    root.geometry("800x880")
    root.minsize(720, 700)
    root.option_add("*Font", (default_font_family(), 10))

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    tab_text = ttk.Frame(notebook)
    tab_fill = ttk.Frame(notebook)
    tab_settings = ttk.Frame(notebook)
    notebook.add(tab_text, text="改字助手")
    notebook.add(tab_fill, text="填表助手")
    notebook.add(tab_settings, text="设置")

    app_text = gui.CadTextApp(tab_text)
    app_fill = gui_fill.IsoFillApp(tab_fill)
    settings.SettingsPanel(tab_settings)

    def _on_close() -> None:
        # 先通知两个面板停止后台线程，再销毁窗口
        app_text._on_close()
        app_fill._on_close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
