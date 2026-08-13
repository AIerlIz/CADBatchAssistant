"""tkinter GUI：ISO 图纸标题栏填表工具。

选择 数据表.xlsx/.xls 与图纸文件（DWG/DXF 多选），一键执行：
准备 DXF → 推断规格 → 填表 → 输出（DWG 转回 DWG，DXF 保持 DXF）。
后台线程执行，日志与进度经队列回传，界面不卡顿。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cadbatchassistant.common import (
    AsyncPanel,
    get_oda,
    get_out_version,
    templates_dir,
)
from cadbatchassistant.core.dwg_converter import require_oda_for_dwg
from cadbatchassistant.core.fill_pipeline import run_pipeline_files
from cadbatchassistant.gui.gui_shared import (
    FilesPanelMixin,
    PanelLayoutMixin,
    TemplateLibraryMixin,
    begin_run,
)


class IsoFillApp(FilesPanelMixin, TemplateLibraryMixin, PanelLayoutMixin,
                 AsyncPanel):
    """「填表助手」面板：文件列表/模板库/拖放/输出目录复用共享组件。"""

    TEMPLATE_CATEGORY = "fill"
    TEMPLATE_CONFIG_KEY = "fill_template"
    TEMPLATE_UPLOAD_TITLE = "上传图纸模板（未填图框 + 值格 [列名] 占位）"

    def __init__(self, parent: tk.Widget) -> None:
        """构建「填表助手」面板；parent 为嵌入容器（如 Notebook 的 tab 页）。"""
        super().__init__(parent)
        self.scanned_files: list[str] = []
        self._sheet_headers: dict[str, list[str]] = {}  # 工作表名 → 首行表头缓存
        self._build_ui()
        # 仅恢复上次选择的图纸模板；输入输出路径不设默认值、不记忆恢复
        # （ODA 路径与输出版本为全局设置，见「设置」tab）
        self._refresh_templates()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self._main = self._build_root()

        # 1. 输入区（与「改字助手」一致：仅选择文件 + 文件列表）
        self._add_input_section(width=60, bind_delete=True)

        # 2. 数据源区（数据表 + 图纸模板，置于输入区正下方）
        src_frame = ttk.LabelFrame(self._main, text="数据源", padding=8)
        src_frame.pack(fill="x", **self._pad)
        self._add_xlsx_row(
            src_frame, "数据表格:", (".xlsx", ".xls"),
            on_hit=lambda h: self._refresh_sources())

        # 工作表 + 匹配列（同一行）
        row_sheet = ttk.Frame(src_frame)
        row_sheet.pack(fill="x", pady=(6, 0))
        ttk.Label(row_sheet, text="工作表格:").pack(side="left")
        self.var_sheet = tk.StringVar()
        self.sheet_combo = ttk.Combobox(row_sheet, textvariable=self.var_sheet,
                                        state="readonly", width=16)
        self.sheet_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_changed)
        ttk.Label(row_sheet, text="匹配列:").pack(side="left")
        self.var_match_col = tk.StringVar()
        self.match_combo = ttk.Combobox(row_sheet, textvariable=self.var_match_col,
                                        state="readonly", width=16)
        self.match_combo.pack(side="left", fill="x", expand=True, padx=4)

        # 图纸模板（模板库下拉选择）
        self._add_template_row(src_frame)

        # 3. 输出区
        self.var_out = tk.StringVar()
        self._add_output_section(self.var_out)

        # 4. 运行区（ODA 路径与输出版本已移至「设置」tab，全局共享）
        self._add_run_section(maximum=100)

        # 5. 日志区
        self._add_log_section()

    # ---------------- 输入 ----------------
    def _browse_xlsx(self) -> None:
        f = filedialog.askopenfilename(
            title="选择数据表格", filetypes=[("Excel 数据表", "*.xlsx *.xls"), ("所有文件", "*.*")]
        )
        if f:
            self.var_xlsx.set(f)
            self._refresh_sources()

    def _refresh_sources(self) -> None:
        """读取数据表工作表与表头，刷新「工作表格」「匹配列」下拉；默认第一个/第一列。

        一次打开工作簿同时取得工作表名与各表首行表头（load_sheet_meta），
        避免 list_sheets + get_headers 的重复全量加载；某个 sheet 为空
        （无表头）只清空匹配列，不连带清空工作表格下拉。
        """
        path = self.var_xlsx.get().strip()
        sheets: list[str] = []
        headers: dict[str, list[str]] = {}
        if path and os.path.isfile(path):
            try:
                from cadbatchassistant.core.fill_parse_xlsx import load_sheet_meta

                sheets, headers = load_sheet_meta(path)
            except Exception:  # noqa: BLE001 - 文件损坏/不可读时无工作表可选
                sheets = []
                headers = {}
        self._sheet_headers = headers
        cur_sheet = self.var_sheet.get()
        if cur_sheet in sheets:
            sheet = cur_sheet
        elif sheets:
            sheet = sheets[0]  # 默认第一个
        else:
            sheet = None
        cols = self._sheet_headers.get(sheet, []) if sheet else []
        self.sheet_combo["values"] = sheets
        if cur_sheet in sheets:
            self.var_sheet.set(cur_sheet)
        elif sheets:
            self.var_sheet.set(sheets[0])
        else:
            self.var_sheet.set("")
        cur_col = self.var_match_col.get()
        self.match_combo["values"] = cols
        if cur_col in cols:
            self.var_match_col.set(cur_col)
        elif cols:
            self.var_match_col.set(cols[0])  # 默认第一列
        else:
            self.var_match_col.set("")

    def _on_sheet_changed(self, _event=None) -> None:
        """切换工作表后按该 sheet 表头刷新「匹配列」下拉（用缓存，不重新加载文件）。"""
        cols = self._sheet_headers.get(self.var_sheet.get(), [])
        self.match_combo["values"] = cols
        cur_col = self.var_match_col.get()
        if cur_col in cols:
            self.var_match_col.set(cur_col)
        elif cols:
            self.var_match_col.set(cols[0])  # 默认第一列
        else:
            self.var_match_col.set("")

    # ---------------- 运行 ----------------
    def _start(self) -> None:
        if self.running:
            return
        xlsx = self.var_xlsx.get().strip()
        tpl_name = self.var_template.get().strip()
        template = str(templates_dir("fill") / tpl_name) if tpl_name else ""
        sheet = self.var_sheet.get().strip() or None
        match_col = self.var_match_col.get().strip() or None
        files = list(self.scanned_files)
        out = self.var_out.get().strip()
        oda = get_oda()
        out_version = get_out_version()

        if not xlsx or not os.path.isfile(xlsx):
            messagebox.showwarning("提示", "请选择有效的数据表格文件")
            return
        if not tpl_name or not os.path.isfile(template):
            messagebox.showwarning("提示", "请从图纸模板下拉框选择模板（可先「上传」）")
            return
        if not files:
            messagebox.showwarning("提示", "请选择要处理的 DWG/DXF 文件")
            return
        if not out:
            messagebox.showwarning("提示", "请设置输出目录")
            return
        has_dwg = (any(f.lower().endswith(".dwg") for f in files)
                   or template.lower().endswith(".dwg"))
        err = require_oda_for_dwg(has_dwg, oda)
        if err:
            messagebox.showerror("缺少 ODA File Converter", err)
            return

        begin_run(self)
        self._start_worker((xlsx, template, files, out, oda,
                            out_version, self._cancel_event, match_col, sheet))

    def _work(self, xlsx: str, template: str, files: list[str], out: str,
              oda: str, version: str, cancel, match_col: str | None,
              sheet: str | None) -> bool:
        summary = run_pipeline_files(
            xlsx, files, out, oda=oda or None, out_version=version,
            emit=self._emit, cancel=cancel, template=template,
            match_col=match_col, sheet=sheet,
            progress=lambda p: self._emit("", p),
        )
        failed = summary.get("failed", [])
        if failed:
            self._emit(f"==== 完成 {summary['ok']}/{summary['count']} 张，"
                       f"失败 {len(failed)} 张：{', '.join(failed)}，"
                       f"输出见 {summary['output']} ====")
        else:
            self._emit(f"==== 全部完成：{summary['count']} 张图纸，"
                       f"输出见 {summary['output']} ====")
        return not failed
