"""「设置」面板：全局配置（ODA File Converter 路径、DWG 输出版本、软件更新）。

放在统一窗口第三个 tab；配置自动保存到 APP_CONFIG_FILE，
「改字助手」与「填表助手」两个处理面板共用。
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from cadbatchassistant import __version__
from cadbatchassistant.core import updater
from cadbatchassistant.core.common.app_config import (
    OUT_VERSION_CHOICES,
    load_app_config,
    save_app_config,
)
from cadbatchassistant.gui.components.tk_widgets import build_oda_row, check_oda


class SettingsPanel:
    """全局设置面板：ODA 路径、DWG 输出版本与软件更新，改动自动保存。"""

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        # 控件字段由 _build_ui 创建（在 _load 前），此处仅声明类型避免 Optional 噪音
        self.var_oda: tk.StringVar
        self.var_oda_info: tk.StringVar
        self.var_version: tk.StringVar
        self.var_update_info: tk.StringVar
        self.var_update_mirror: tk.StringVar
        self.var_ignore_info: tk.StringVar
        self.btn_check: ttk.Button
        self.btn_clear_ignore: ttk.Button
        self._ignored_tag: str | None = None
        self._build_ui()
        self._load()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)

        cfg_frame = ttk.LabelFrame(main, text="全局设置", padding=8)
        cfg_frame.pack(fill="x", padx=8, pady=4)

        self.var_oda, self.var_oda_info = build_oda_row(cfg_frame)

        ttk.Label(cfg_frame, text="DWG 输出版本:").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.var_version = tk.StringVar()
        version_cb = ttk.Combobox(
            cfg_frame,
            textvariable=self.var_version,
            values=OUT_VERSION_CHOICES,
            state="readonly",
            width=14,
        )
        version_cb.grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        cfg_frame.columnconfigure(1, weight=1)

        # ---- 软件更新 ----
        upd_frame = ttk.LabelFrame(main, text="软件更新", padding=8)
        upd_frame.pack(fill="x", padx=8, pady=4)

        ttk.Label(upd_frame, text=f"当前版本: v{__version__}").grid(
            row=0, column=0, sticky="w"
        )
        # 「检查更新」「清除忽略」放在同一 Frame 内紧挨，避免 column 扩展把按钮推开
        btn_row = ttk.Frame(upd_frame)
        btn_row.grid(row=0, column=1, sticky="w", padx=4)
        self.btn_check = ttk.Button(
            btn_row, text="检查更新", command=self._check_update
        )
        self.btn_check.pack(side="left")
        self.btn_clear_ignore = ttk.Button(
            btn_row, text="清除忽略", command=self._clear_ignore, state="disabled"
        )
        self.btn_clear_ignore.pack(side="left", padx=(4, 0))
        self.var_update_info = tk.StringVar()
        # 更新状态提示（检查结果/失败原因/已忽略等），必须有显示控件才可见
        ttk.Label(upd_frame, textvariable=self.var_update_info, foreground="#555").grid(
            row=0, column=2, sticky="w", padx=8
        )

        ttk.Label(upd_frame, text="下载镜像(可选):").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.var_update_mirror = tk.StringVar()
        mirror_entry = ttk.Entry(upd_frame, textvariable=self.var_update_mirror)
        mirror_entry.grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=4, pady=(6, 0)
        )
        upd_frame.columnconfigure(1, weight=1)

        self.var_ignore_info = tk.StringVar()
        # 「已忽略版本」提示行（与清除按钮同列下方），必须有显示控件才可见
        ttk.Label(upd_frame, textvariable=self.var_ignore_info, foreground="#a00").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=4
        )

    # ---------------- 配置 ----------------
    def _load(self) -> None:
        cfg = load_app_config()
        if cfg.get("oda"):
            self.var_oda.set(cfg["oda"])
        self.var_version.set(cfg.get("version", OUT_VERSION_CHOICES[0]))
        self.var_update_mirror.set(cfg.get("update_mirror", ""))
        self._ignored_tag = updater.ignored_version()
        self._refresh_ignore_ui()
        # 输入框后续任何写入（手动输入 / 浏览选择 / 版本下拉）自动保存
        self.var_oda.trace_add("write", lambda *a: self._on_change())
        self.var_version.trace_add("write", lambda *a: self._on_change())
        self.var_update_mirror.trace_add("write", lambda *a: self._on_change())
        check_oda(self.var_oda, self.var_oda_info)  # 探测并刷新状态提示

    def _on_change(self, _event=None) -> None:
        """自动保存到全局配置（合并写入，保留 update_ignore 等其他配置项）。"""
        save_app_config(
            {
                "oda": self.var_oda.get().strip(),
                "version": self.var_version.get(),
                "update_mirror": self.var_update_mirror.get().strip(),
            }
        )

    # ---------------- 软件更新 ----------------
    def _check_update(self) -> None:
        """后台线程查询 GitHub 最新版本，结果回主线程更新提示。"""
        if not updater.is_frozen():
            self.var_update_info.set("开发模式（python main.py）下不检查更新")
            return
        self.btn_check.config(state="disabled")
        self.var_update_info.set("正在检查更新...")

        def _work() -> None:
            try:
                result = updater.check_latest()
            except Exception as e:  # noqa: BLE001 - 意外异常统一按失败处理
                result = {"ok": False, "error": str(e)}
            self._parent.after(0, lambda: self._on_check_result(result))

        threading.Thread(target=_work, daemon=True).start()

    def _on_check_result(self, result: dict) -> None:
        self.btn_check.config(state="normal")
        if not result.get("ok"):
            self.var_update_info.set(f"检查失败：{result['error']}")
            return
        current = updater.parse_version(__version__)
        if updater.is_newer(result["version"], current):
            tag = result["tag"]
            if updater.is_ignored(tag, self._ignored_tag):
                self.var_update_info.set(f"已忽略版本 {tag}（可在下方清除忽略）")
                return
            from cadbatchassistant.gui.dialogs.updater_dialog import ask_update_choice

            choice = ask_update_choice(self._parent, tag)
            if choice == "update":
                self._start_update(result)
            elif choice == "ignore":
                updater.set_ignored_version(tag)
                self._ignored_tag = tag
                self.var_update_info.set(f"已忽略版本 {tag}")
                self._refresh_ignore_ui()
            else:
                self.var_update_info.set(f"发现新版本 {tag}（暂不更新）")
        else:
            self.var_update_info.set(f"已是最新版本（v{__version__}）")

    def _start_update(self, latest: dict) -> None:
        """进入下载更新流程（UpdaterDialog 展示进度，完成后替换重启）。"""
        from cadbatchassistant.gui.dialogs.updater_dialog import start_update_download

        start_update_download(
            self._parent, latest, self.var_update_mirror.get().strip()
        )

    # ---------------- 忽略版本 ----------------
    def _refresh_ignore_ui(self) -> None:
        """按忽略状态刷新「已忽略版本」提示与清除按钮。"""
        if self._ignored_tag:
            self.var_ignore_info.set(f"已忽略版本：{self._ignored_tag}")
            self.btn_clear_ignore.config(state="normal")
        else:
            self.var_ignore_info.set("")
            self.btn_clear_ignore.config(state="disabled")

    def _clear_ignore(self) -> None:
        """清除忽略记录，恢复该版本的更新提示。"""
        updater.set_ignored_version("")
        self._ignored_tag = None
        self.var_update_info.set("")
        self._refresh_ignore_ui()
