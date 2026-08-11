"""程序入口：启动「CAD批处理助手」统一窗口（Notebook 三个 tab 切换）。

- 「改字助手」：CadTextApp（gui.py）—— DWG/DXF 文字正则替换
- 「填表助手」：IsoFillApp（gui_fill.py）—— 数据表填入图纸标题栏
- 「设置」：SettingsPanel（settings.py）—— ODA 路径与 DWG 输出版本（全局共享）

根窗口使用 TkinterDnD.Tk，为填表面板提供文件拖放支持。
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from tkinterdnd2 import TkinterDnD

from cadbatchassistant import __version__
from cadbatchassistant.common import APP_CONFIG_FILE, default_font_family, load_config
from cadbatchassistant.core import updater
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
    root.on_close = _on_close  # 供更新流程替换前调用（停止后台线程后再销毁）
    root.after(2000, lambda: _auto_check_update(root))
    root.mainloop()


def _auto_check_update(root: tk.Tk) -> None:
    """启动后静默检查更新（仅打包版；失败不打扰用户），发现新版才提示。"""
    if not updater.is_frozen():
        return
    mirror = str(load_config(APP_CONFIG_FILE).get("update_mirror", "")).strip()

    def _work() -> None:
        try:
            result = updater.check_latest()
        except Exception:  # noqa: BLE001 - 意外异常静默失败，不打扰
            return
        if not result.get("ok"):
            return  # 静默失败（网络等原因），不打扰
        current = updater.parse_version(__version__)
        if not updater.is_newer(result["version"], current):
            return
        root.after(0, lambda: _prompt_update(root, result, mirror))

    threading.Thread(target=_work, daemon=True).start()


def _prompt_update(root: tk.Tk, latest: dict, mirror: str) -> None:
    """主线程弹窗：有新版本时询问是否立即更新。"""
    if not messagebox.askyesno(
        "发现新版本",
        f"发现新版本 {latest['tag']}（当前 v{__version__}）\n\n是否立即下载并更新？",
        parent=root,
    ):
        return
    from cadbatchassistant.gui.updater_dialog import start_update_download

    start_update_download(root, latest, mirror)


if __name__ == "__main__":
    main()
