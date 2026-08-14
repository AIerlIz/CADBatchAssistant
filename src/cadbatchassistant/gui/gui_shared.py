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
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES

from cadbatchassistant.core.app_config import load_config, save_config
from cadbatchassistant.core.filetypes import CAD_SUFFIXES
from cadbatchassistant.core.templates import list_templates
from cadbatchassistant.gui.tk_util import dedup_paths, parse_dnd_data
from cadbatchassistant.gui.tk_widgets import (
    build_file_list,
    build_log_panel,
    build_output_row,
    delete_template_file,
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
    - 输出目录 StringVar：self.var_out
    """

    scanned_files: list[str]
    file_list: tk.Listbox
    var_scan_info: tk.StringVar
    var_out: tk.StringVar

    # ---------------- 输入：选择 / 追加 ----------------
    def _browse_input_files(self) -> None:
        was_empty = not self.scanned_files  # 导入前待处理列表为空
        files = filedialog.askopenfilenames(
            title="选择要处理的 DWG/DXF 文件（可多次追加选择）",
            filetypes=[
                ("CAD 文件", "*.dwg *.dxf"),
                ("DWG 文件", "*.dwg"),
                ("DXF 文件", "*.dxf"),
                ("所有文件", "*.*"),
            ],
        )
        if not files:
            return
        for f in files:
            if os.path.isfile(f):
                self.scanned_files.append(f)
        self.scanned_files = dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if was_empty:
            # 列表从空变非空：第一个导入文件所在目录/output 作为默认输出
            # （即使输出目录已有值也更新，避免残留上次的目录）
            self._default_output()

    def _on_drop_files(self, event) -> None:
        was_empty = not self.scanned_files  # 拖入前待处理列表为空
        added = False
        for p in parse_dnd_data(event.data):
            if p.lower().endswith(CAD_SUFFIXES) and os.path.isfile(p):
                self.scanned_files.append(p)
                added = True
        if not added:
            messagebox.showwarning("提示", "仅支持拖入 DWG/DXF 文件")
            return
        self.scanned_files = dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if was_empty:
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
            self.var_out.set(str(Path(self.scanned_files[0]).parent / "output"))

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(title="选择目录")
        if d:
            var.set(d)

    def _on_drop_out_dir(self, event) -> None:
        paths = parse_dnd_data(event.data)
        d = next((p for p in paths if os.path.isdir(p)), None)
        if d is not None:
            self.var_out.set(d)
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

    # 依赖 PanelLayoutMixin._add_template_row 创建的控件（在 _build_ui 中赋值）
    var_template: tk.StringVar
    tpl_combo: ttk.Combobox

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
            self.TEMPLATE_CATEGORY, path, title=self.TEMPLATE_UPLOAD_TITLE
        )
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
        hit = next(
            (
                p
                for p in parse_dnd_data(event.data)
                if p.lower().endswith(CAD_SUFFIXES) and os.path.isfile(p)
            ),
            None,
        )
        if hit is None:
            messagebox.showwarning(
                "提示", "仅支持拖入 .dwg/.dxf 图纸模板（将上传到模板库）"
            )
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


def warn_require(condition: bool, message: str, title: str = "提示") -> bool:
    """校验辅助：条件不满足时弹警告并返回 False；满足返回 True。

    把各面板 _start 里反复出现的「if not xxx: messagebox.showwarning(...);
    return」样板收敛为一行：if not warn_require(xxx, "文案"): return None。
    """
    if not condition:
        messagebox.showwarning(title, message)
    return bool(condition)


class PanelLayoutMixin:
    """三个功能面板的公共 UI 骨架（待处理/输出/运行/日志区 + 数据源区行）。

    子类在 _build_ui 中依次调用骨架方法组装界面：
        self._main = self._build_root()
        self._add_input_section(...)          # 「待处理」文件列表区
        ...中间专属区（规则/选项/数据源等）...
        self._add_output_section(var)         # 「输出」目录行
        self._add_run_section(...)            # 开始/停止 + 进度条
        self._add_log_section(...)            # 「日志」面板

    同时提供公共行为：_on_drop_single（拖放单文件到输入框）与默认
    _finish_notify（finish_popup 弹窗，目录助手覆盖为统计弹窗）。
    完成收尾由 AsyncPanel._on_finish 统一触发 _finish_notify 钩子，
    不再依赖 super() 的 MRO 顺序约定。
    """

    # 骨架方法创建的控件字段（FilesPanelMixin / TemplateLibraryMixin /
    # AsyncPanel 亦引用）。类型与各 Mixin 声明保持一致，避免多基类同名
    # 属性类型冲突（mypy incompatible definition）。
    _main: ttk.Frame
    _pad: dict
    _file_menu: tk.Menu
    file_list: tk.Listbox
    var_scan_info: tk.StringVar
    var_xlsx: tk.StringVar
    var_template: tk.StringVar
    tpl_combo: ttk.Combobox
    btn_start: ttk.Button
    btn_stop: ttk.Button
    progress: ttk.Progressbar
    log_text: tk.Text

    def _build_root(self) -> ttk.Frame:
        """创建面板根容器（padding + 布局字典），返回 main frame。"""
        self._pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)
        return main

    def _add_input_section(
        self, width: int | None = None, bind_delete: bool = False
    ) -> None:
        """「待处理」输入区：选择文件 + 统计提示 + 多选文件列表（拖放追加）。

        width 传给文件列表（None 用 build_file_list 默认）；
        bind_delete 为 True 时额外绑定 Delete 键删除选中文件。
        """
        in_frame = ttk.LabelFrame(self._main, text="待处理", padding=8)
        in_frame.pack(fill="x", **self._pad)
        top = ttk.Frame(in_frame)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="选择文件", command=self._browse_input_files).pack(
            side="left"
        )
        self.var_scan_info = tk.StringVar(value="尚未选择文件")
        ttk.Label(top, textvariable=self.var_scan_info).pack(side="left", padx=10)

        self.file_list, self._file_menu = build_file_list(
            in_frame,
            height=6,
            on_delete=self._delete_selected_files,
            fill="both",
            width=width,
        )
        if bind_delete:
            self.file_list.bind("<Delete>", lambda _e: self._delete_selected_files())
        # 拖拽 DWG/DXF 到列表追加
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop_files)

    def _add_output_section(self, var: tk.StringVar) -> None:
        """「输出」区：输出目录行（浏览/默认/拖放文件夹）。"""
        out_frame = ttk.LabelFrame(self._main, text="输出", padding=8)
        out_frame.pack(fill="x", **self._pad)
        build_output_row(
            out_frame,
            var,
            on_browse=lambda: self._browse_dir(var),
            on_default=self._default_output,
            entry_hook=lambda e: (
                e.drop_target_register(DND_FILES),
                e.dnd_bind("<<Drop>>", self._on_drop_out_dir),
            ),
        )

    def _add_run_section(self, maximum: int | None = None) -> None:
        """「运行」区：开始/停止按钮 + 进度条。

        maximum 用于进度条上限（None 为默认 determinate 0-100）。
        """
        run_frame = ttk.Frame(self._main)
        run_frame.pack(fill="x", **self._pad)
        self.btn_start = ttk.Button(run_frame, text="开始处理", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(
            run_frame, text="停止", command=self._stop, state="disabled"
        )
        self.btn_stop.pack(side="left", padx=6)
        kw: dict = {"mode": "determinate"}
        if maximum is not None:
            kw["maximum"] = maximum
        self.progress = ttk.Progressbar(run_frame, **kw)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

    def _add_log_section(self, height: int = 8) -> None:
        """「日志」区。"""
        log_frame, self.log_text = build_log_panel(self._main, height=height)
        log_frame.pack(fill="both", expand=True, **self._pad)

    # ---------------- 数据源区（填表 / 目录助手共用） ----------------
    def _add_src_section(
        self, xlsx_label: str, exts: tuple, on_xlsx_hit=None, tpl_width: int = 16
    ) -> ttk.LabelFrame:
        """「数据源」区：xlsx 行 + 图纸模板行；返回 frame 供追加专属行。

        xlsx_label 为 xlsx 输入行文案（如 "数据表格:" / "表格模板:"）；
        exts 为拖放接受的扩展名（如 (".xlsx", ".xls")）；
        on_xlsx_hit 为拖放/选择 xlsx 后的回调（如刷新下拉/记忆配置）；
        tpl_width 为图纸模板下拉宽度。
        """
        src_frame = ttk.LabelFrame(self._main, text="数据源", padding=8)
        src_frame.pack(fill="x", **self._pad)
        self._add_xlsx_row(src_frame, xlsx_label, exts, on_hit=on_xlsx_hit)
        self._add_template_row(src_frame, width=tpl_width)
        return src_frame

    def _add_xlsx_row(self, parent, xlsx_label: str, exts: tuple, on_hit=None) -> None:
        """xlsx 输入行：Label + Entry(拖放) + 浏览按钮；命中时回调 on_hit(路径)。"""
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Label(row, text=xlsx_label).pack(side="left")
        self.var_xlsx = tk.StringVar()
        e = ttk.Entry(row, textvariable=self.var_xlsx)
        e.pack(side="left", fill="x", expand=True, padx=4)
        e.drop_target_register(DND_FILES)
        e.dnd_bind(
            "<<Drop>>",
            lambda ev: self._on_drop_single(ev, self.var_xlsx, exts, on_hit=on_hit),
        )
        ttk.Button(row, text="浏览", command=self._browse_xlsx).pack(
            side="left", padx=4
        )

    def _add_template_row(self, parent, width: int = 16) -> None:
        """图纸模板行：下拉（拖放上传）+ 上传/删除按钮。"""
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(6, 0))
        ttk.Label(row, text="图纸模板:").pack(side="left")
        self.var_template = tk.StringVar()
        self.tpl_combo = ttk.Combobox(
            row, textvariable=self.var_template, state="readonly", width=width
        )
        self.tpl_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.tpl_combo.drop_target_register(DND_FILES)
        self.tpl_combo.dnd_bind("<<Drop>>", self._on_drop_upload_template)
        ttk.Button(row, text="上传", command=self._upload_template).pack(
            side="left", padx=4
        )
        ttk.Button(row, text="删除", command=self._delete_template).pack(
            side="left", padx=4
        )

    # ---------------- 公共行为 ----------------
    def _on_drop_single(
        self, event, var: tk.StringVar, exts: tuple, on_hit=None
    ) -> None:
        """拖放单个文件到输入框；命中时设置 var 并调用可选 on_hit(路径)。

        供 xlsx/表格模板等输入行复用（命中后的附加处理如刷新下拉/记忆
        配置由 on_hit 注入，避免各面板重复实现）。
        """
        paths = parse_dnd_data(event.data)
        hit = next((p for p in paths if p.lower().endswith(exts)), None)
        if hit is not None:
            var.set(hit)
            if on_hit is not None:
                on_hit(hit)
        elif paths:
            messagebox.showwarning("提示", f"仅支持 {', '.join(exts)} 文件")

    def _finish_notify(self, success: bool) -> None:
        """完成提示钩子：默认弹窗汇总（目录助手覆盖为自己的统计弹窗）。"""
        finish_popup(success)


class RunStartMixin:
    """「开始处理」统一起动骨架：校验(_prepare_run) → begin_run → 启动 worker。

    子类实现：
    - _prepare_run() -> tuple | None：校验输入并收集 worker 参数（tuple）；
      校验失败弹窗提示并返回 None（不启动）。
    - _run_maximum() -> int | None：进度条上限（改字按文件数，填表/目录
      为 0-100 百分比，默认 None）。
    - 可选覆盖 _after_begin_run(args)：begin_run 之后、启动 worker 之前的
      钩子（如输出 sheet 定位日志）。

    _start 由本类统一提供，消除三个面板重复的「running 检查 / begin_run /
    启动 worker / 启动异常复位」样板。依赖 AsyncPanel 提供 _start_worker /
    _emit / running / _cancel_event（由面板的 MRO 一并继承）。
    """

    def _run_maximum(self) -> int | None:
        return None

    def _reset_run_state(self) -> None:
        """复位运行态：running 置 False、恢复开始按钮、禁用停止按钮。

        供启动阶段兜底使用（worker 启动本身异常时）；_on_finish 也有
        等价逻辑（AsyncPanel），此处为 begin_run 之后、_start_worker 之前
        的异常路径服务。
        """
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _after_begin_run(self, args: tuple) -> None:
        """begin_run 之后、启动 worker 之前的钩子（默认空）。"""

    def _start(self) -> None:
        """统一起动入口：校验通过才 begin_run 并启动后台 worker。"""
        if self.running:
            return
        args = self._prepare_run()
        if args is None:
            return  # 校验失败/取消，已弹窗提示
        begin_run(self, maximum=self._run_maximum())
        try:
            self._after_begin_run(args)
            self._start_worker(args)
        except Exception as ex:  # noqa: BLE001 - 启动异常兜底复位，避免面板卡死
            self._reset_run_state()
            self._emit(f"启动失败：{ex}")
