"""「设置」面板：全局配置（ODA File Converter 路径、DWG 输出版本）。

放在统一窗口第三个 tab；配置自动保存到 APP_CONFIG_FILE，
「改字助手」与「填表助手」两个处理面板共用。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cadbatchassistant.common import (
    APP_CONFIG_FILE,
    OUT_VERSION_CHOICES,
    build_oda_row,
    check_oda,
    load_config,
    save_config,
)


class SettingsPanel:
    """全局设置面板：ODA 路径与 DWG 输出版本，改动自动保存。"""

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        self.var_oda: tk.StringVar | None = None
        self.var_oda_info: tk.StringVar | None = None
        self.var_version: tk.StringVar | None = None
        self._build_ui()
        self._load()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)

        cfg_frame = ttk.LabelFrame(main, text="全局设置", padding=8)
        cfg_frame.pack(fill="x", **pad)

        self.var_oda, self.var_oda_info = build_oda_row(cfg_frame)

        ttk.Label(cfg_frame, text="DWG 输出版本:").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.var_version = tk.StringVar()
        version_cb = ttk.Combobox(
            cfg_frame, textvariable=self.var_version,
            values=OUT_VERSION_CHOICES, state="readonly", width=14,
        )
        version_cb.grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        cfg_frame.columnconfigure(1, weight=1)

        ttk.Label(
            main,
            text="改动自动保存；「改字助手」与「填表助手」处理 DWG 时共用此配置。",
            foreground="#666666",
        ).pack(anchor="w", **pad)

    # ---------------- 配置 ----------------
    def _load(self) -> None:
        cfg = load_config(APP_CONFIG_FILE)
        if cfg.get("oda"):
            self.var_oda.set(cfg["oda"])
        self.var_version.set(cfg.get("version", OUT_VERSION_CHOICES[0]))
        # 输入框后续任何写入（手动输入 / 浏览选择 / 版本下拉）自动保存
        self.var_oda.trace_add("write", lambda *a: self._on_change())
        self.var_version.trace_add("write", lambda *a: self._on_change())
        check_oda(self.var_oda, self.var_oda_info)  # 探测并刷新状态提示

    def _on_change(self, _event=None) -> None:
        """自动保存到全局配置（手动输入 / 浏览选择 / 版本下拉均触发）。"""
        save_config(APP_CONFIG_FILE, {
            "oda": self.var_oda.get().strip(),
            "version": self.var_version.get(),
        })
