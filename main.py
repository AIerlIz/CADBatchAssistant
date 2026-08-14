"""程序入口：启动「CAD批处理助手」统一窗口（Notebook 三个功能 tab + 设置）。

- 「改字助手」：CadTextApp（gui_text.py）—— DWG/DXF 文字正则替换
- 「填表助手」：IsoFillApp（gui_fill.py）—— 数据表填入图纸标题栏
- 「目录助手」：CatalogPanel（gui_catalog.py）—— 按图纸模板取值生成图纸目录 Excel
- 「设置」：SettingsPanel（settings.py）—— ODA 路径与 DWG 输出版本（全局共享）

根窗口使用 TkinterDnD.Tk，为各面板提供文件拖放支持。

命令行诊断模式（不启动 GUI）：
    CADBatchAssistant.exe --selftest <图纸模板DWG> <图纸文件...>
将「转换 + 取值 + 生成目录」过程中每个文件的异常堆栈写入 exe 同目录
selftest_log.txt，用于定位打包环境下偶发的解析问题。
"""

from __future__ import annotations

import argparse
import contextlib
import multiprocessing
import os
import shutil
import sys
import tempfile
import threading
import tkinter as tk
import traceback as _traceback
from pathlib import Path
from tkinter import ttk

# Windows 防御：chown 在 Windows 无意义，且 shutil.chown 在无 os.chown 时
# 会先抛 LookupError("no such group")；屏蔽其调用避免运行时误触发。
if os.name == "nt":
    shutil.chown = lambda path, user=None, group=None: None  # type: ignore[assignment]

from tkinterdnd2 import TkinterDnD

from cadbatchassistant import __version__
from cadbatchassistant.core import updater
from cadbatchassistant.core.app_config import (
    APP_CONFIG_FILE,
    load_config,
    resource_path,
)
from cadbatchassistant.gui import gui_catalog, gui_fill, gui_text, settings
from cadbatchassistant.gui.tk_util import center_window, default_font_family

APP_TITLE = "CAD批处理助手"


def _selftest(template_dwg: str, dwg_files: list[str]) -> int:
    """诊断模式：图纸模板 + 图纸 → 完整目录流程，把日志/异常写入 selftest_log.txt。"""
    from cadbatchassistant.core import catalog_excel_writer
    from cadbatchassistant.core.app_config import get_oda
    from cadbatchassistant.core.catalog_pipeline import (
        parse_template_fields,
        run_pipeline,
    )

    log_path = Path(__file__).resolve().parent / "selftest_log.txt"
    if getattr(sys, "frozen", False):
        log_path = Path(sys.executable).parent / "selftest_log.txt"

    lines: list[str] = []
    failed = False
    try:
        lines.append(f"[selftest] 模板: {template_dwg}  图纸: {len(dwg_files)} 个")
        logs: list[str] = []

        def _log(m: str) -> None:
            logs.append(m)

        # 先解析模板字段名，动态生成「表头 = 字段名 + 页码」的临时表格模板。
        # 表头行由占位符字段名反推，生成时传入实际字段名必然命中；
        # 诊断模式依旧不依赖用户提供表格模板。
        with tempfile.TemporaryDirectory(prefix="cad_selftest_") as td:
            tmp = Path(td)
            oda = get_oda()
            fields = parse_template_fields(template_dwg, oda=oda)
            style_tpl = tmp / "style.xlsx"
            catalog_excel_writer.write_style_template(
                style_tpl, fields=[*fields, "页码"]
            )
            out_xlsx = tmp / "out.xlsx"
            res = run_pipeline(
                template_dwg,
                style_tpl,
                dwg_files,
                out_xlsx,
                oda=oda,
                log=_log,
                progress=lambda p: None,
            )
            lines.append(
                f"[selftest] 结果: ok={res.ok}  图纸={res.total_files}  "
                f"NA={res.na_rows}  页={res.total_pages}"
            )
            for m in logs:
                lines.append(f"[selftest] {m}")
            if res.error:
                lines.append(f"[selftest] 错误: {res.error}")
            if res.failed_files:
                lines.append(f"[selftest] 失败文件: {res.failed_files}")
            if not res.ok:
                failed = True
    except Exception:  # noqa: BLE001 - 诊断模式整体失败也写日志
        failed = True
        lines.append(f"[selftest] 整体失败:\n{_traceback.format_exc()}")

    try:
        log_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[selftest] 日志已写入: {log_path}")
    except OSError as ex:
        print(f"[selftest] 无法写日志: {ex}")
        failed = True
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument(
        "--selftest",
        nargs="+",
        metavar="PATH",
        help="诊断模式：第一个参数为图纸模板 DWG，其余为图纸文件；"
        "日志写入 selftest_log.txt",
    )
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest(args.selftest[0], args.selftest[1:])

    root = TkinterDnD.Tk()
    root.title(APP_TITLE)
    root.geometry("820x900")
    root.minsize(720, 700)
    with contextlib.suppress(Exception):  # 图标缺失时使用默认图标
        root.iconbitmap(resource_path("assets/logo.ico"))  # 窗口/任务栏图标
    center_window(root)  # 主窗口屏幕居中
    root.option_add("*Font", (default_font_family(), 10))

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    tab_text = ttk.Frame(notebook)
    tab_fill = ttk.Frame(notebook)
    tab_catalog = ttk.Frame(notebook)
    tab_settings = ttk.Frame(notebook)
    notebook.add(tab_text, text="改字助手")
    notebook.add(tab_fill, text="填表助手")
    notebook.add(tab_catalog, text="目录助手")
    notebook.add(tab_settings, text="设置")

    app_text = gui_text.CadTextApp(tab_text)
    app_fill = gui_fill.IsoFillApp(tab_fill)
    app_catalog = gui_catalog.CatalogPanel(tab_catalog)
    settings.SettingsPanel(tab_settings)

    def _on_close() -> None:
        # 先通知各面板停止后台线程，再销毁窗口
        app_text._on_close()
        app_fill._on_close()
        app_catalog._on_close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.on_close = _on_close  # 供更新流程替换前调用（停止后台线程后再销毁）
    root.after(2000, lambda: _auto_check_update(root))
    root.mainloop()
    return 0


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
        if updater.is_ignored(result["tag"], updater.ignored_version()):
            return  # 用户已忽略此版本，不再提示
        root.after(0, lambda: _prompt_update(root, result, mirror))

    threading.Thread(target=_work, daemon=True).start()


def _prompt_update(root: tk.Tk, latest: dict, mirror: str) -> None:
    """主线程弹窗：有新版本时三选（立即更新 / 忽略此版本 / 取消）。"""
    from cadbatchassistant.gui.updater_dialog import (
        ask_update_choice,
        start_update_download,
    )

    choice = ask_update_choice(root, latest["tag"])
    if choice == "update":
        start_update_download(root, latest, mirror)
    elif choice == "ignore":
        updater.set_ignored_version(latest["tag"])


if __name__ == "__main__":
    # PyInstaller 打包后，multiprocessing 子进程以 --multiprocessing-fork
    # 参数重启 exe；必须调用 freeze_support() 才能正常启动并行 worker
    # （否则子进程会重复进入主流程导致并行处理失效/异常）。
    multiprocessing.freeze_support()
    raise SystemExit(main())
