"""三个功能面板共享的 GUI 组件（文件列表 / 图纸模板库 / 启动样板）。

- load_panel_config / save_panel_config : 面板记忆配置（填表与目录助手共用
  CadFill/config.json，catalog_ 前缀键互不干扰）
- FilesPanelMixin : 图纸文件列表公共实现（多选/追加/右键与 Delete 删除/拖放/输出目录）
- TemplateLibraryMixin : 图纸模板库公共实现（下拉/上传/删除/拖放）
- begin_run / finish_popup : 启动后台任务前的按钮/进度/日志样板 与 完成弹窗
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from cadbatchassistant.common import (
    dedup_paths,
    delete_template_file,
    list_templates,
    load_config,
    parse_dnd_data,
    save_config,
    upload_template_file,
)

# 「填表助手」与「目录助手」共用的面板记忆配置（与旧版 CadFill/config.json 路径一致）
PANEL_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "CadFill"
PANEL_CONFIG_FILE = PANEL_CONFIG_DIR / "config.json"


def load_panel_config() -> dict:
    """读取面板记忆配置（填表/目录助手共用）。"""
    return load_config(PANEL_CONFIG_FILE)


def save_panel_config(data: dict) -> None:
    """写入面板记忆配置。"""
    save_config(PANEL_CONFIG_FILE, data)


class FilesPanelMixin:
    """图纸文件列表的公共实现（多选/追加/删除/拖放/输出目录）。

    约定（由继承方在 _build_ui 中提供）：
    - self.scanned_files : list[str]（全路径，显示时取文件名）
    - self.file_list / self.var_scan_info : 文件列表与统计提示
    - 输出目录 StringVar：self.var_out（缺省回退 self.var_output）
    """

    scanned_files: list[str] = []
    file_list: tk.Listbox | None = None
    var_scan_info: tk.StringVar | None = None
    var_out: tk.StringVar | None = None
    var_output: tk.StringVar | None = None

    def _out_var(self) -> tk.StringVar:
        """输出目录 StringVar（var_out 优先，兼容 var_output 命名）。"""
        return self.var_out if self.var_out is not None else self.var_output

    # ---------------- 输入：选择 / 追加 ----------------
    def _browse_input_files(self) -> None:
        files = filedialog.askopenfilenames(
            title="选择要处理的 DWG/DXF 文件（可多次追加选择）",
            filetypes=[("CAD 文件", "*.dwg *.dxf"), ("DWG 文件", "*.dwg"),
                       ("DXF 文件", "*.dxf"), ("所有文件", "*.*")],
        )
        if not files:
            return
        for f in files:
            if os.path.isfile(f):
                self.scanned_files.append(f)
        self.scanned_files = dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if not self._out_var().get().strip():
            self._default_output()

    def _on_drop_files(self, event) -> None:
        added = False
        for p in parse_dnd_data(event.data):
            if p.lower().endswith((".dwg", ".dxf")) and os.path.isfile(p):
                self.scanned_files.append(p)
                added = True
        if not added:
            messagebox.showwarning("提示", "仅支持拖入 DWG/DXF 文件")
            return
        self.scanned_files = dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if not self._out_var().get().strip():
            self._default_output()

    def _delete_selected_files(self) -> None:
        sel = sorted(self.file_list.curselection(), reverse=True)
        for idx in sel:
            if 0 <= idx < len(self.scanned_files):
                del self.scanned_files[idx]
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, "end")
        for p in self.scanned_files:
            self.file_list.insert("end", os.path.basename(p))
        n_dxf = sum(1 for p in self.scanned_files if p.lower().endswith(".dxf"))
        n_dwg = len(self.scanned_files) - n_dxf
        self.var_scan_info.set(
            f"共 {len(self.scanned_files)} 个文件：DXF {n_dxf} 个，DWG {n_dwg} 个"
        )

    # ---------------- 输出目录 ----------------
    def _default_output(self) -> None:
        if self.scanned_files:
            self._out_var().set(str(Path(self.scanned_files[0]).parent / "output"))

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(title="选择目录")
        if d:
            var.set(d)

    def _on_drop_out_dir(self, event) -> None:
        paths = parse_dnd_data(event.data)
        d = next((p for p in paths if os.path.isdir(p)), None)
        if d is not None:
            self._out_var().set(d)
        elif paths:
            messagebox.showwarning("提示", "输出目录请拖入文件夹")


class TemplateLibraryMixin:
    """图纸模板库公共实现（下拉/上传/删除/拖放）。

    子类需设置类属性：
    - TEMPLATE_CATEGORY : 模板库子目录名（"fill" / "catalog"）
    - TEMPLATE_CONFIG_KEY : 记忆配置键（如 "fill_template"）
    - TEMPLATE_UPLOAD_TITLE : 上传对话框标题
    依赖 self.tpl_combo / self.var_template（在 _build_ui 中创建）。
    """

    TEMPLATE_CATEGORY: str = ""
    TEMPLATE_CONFIG_KEY: str = ""
    TEMPLATE_UPLOAD_TITLE: str = "上传图纸模板"

    def _refresh_templates(self) -> None:
        """刷新下拉框并恢复上次选择（面板 config 存模板文件名）。"""
        names = list_templates(self.TEMPLATE_CATEGORY)
        self.tpl_combo["values"] = names
        last = load_panel_config().get(self.TEMPLATE_CONFIG_KEY, "")
        if last in names:
            self.var_template.set(last)
        elif names and not self.var_template.get():
            self.var_template.set(names[0])
        else:
            self.var_template.set("")

    def _upload_template(self, path: str | None = None) -> None:
        """把 dwg/dxf 复制进图纸模板库并选中。"""
        name = upload_template_file(
            self.TEMPLATE_CATEGORY, path, title=self.TEMPLATE_UPLOAD_TITLE)
        if name:
            self._refresh_templates()
            self.var_template.set(name)
            save_panel_config({self.TEMPLATE_CONFIG_KEY: name})

    def _delete_template(self) -> None:
        name = self.var_template.get().strip()
        if delete_template_file(self.TEMPLATE_CATEGORY, name):
            self._refresh_templates()
            save_panel_config({self.TEMPLATE_CONFIG_KEY: self.var_template.get()})

    def _on_drop_upload_template(self, event) -> None:
        hit = next((p for p in parse_dnd_data(event.data)
                    if p.lower().endswith((".dwg", ".dxf")) and os.path.isfile(p)), None)
        if hit is None:
            messagebox.showwarning("提示", "仅支持拖入 .dwg/.dxf 图纸模板（将上传到模板库）")
            return
        self._upload_template(hit)


def begin_run(panel, maximum: int | None = None) -> None:
    """启动后台任务前的统一样板：置运行标志、复位按钮与进度、清空日志。

    panel 需提供 running / _cancel_event / btn_start / btn_stop / progress / log_text。
    maximum 用于复位进度条上限（改字助手按文件数，填表/目录为 0-100 百分比）。
    """
    panel.running = True
    panel._cancel_event.clear()
    panel.btn_start.config(state="disabled")
    panel.btn_stop.config(state="normal")
    if maximum is not None:
        panel.progress.config(maximum=maximum, value=0)
    else:
        panel.progress.config(value=0)
    panel.log_text.delete("1.0", "end")


def finish_popup(success: bool) -> None:
    """完成收尾弹窗（改字/填表共用；目录助手覆盖为自己的统计弹窗）。"""
    if success:
        messagebox.showinfo("完成", "处理完成，请查看日志。")
    else:
        messagebox.showwarning("完成", "处理中断，详见日志。")
