"""tkinter GUI：ISO 图纸标题栏填表工具。

选择 数据表.xlsx/.xls 与图纸文件（DWG/DXF 多选），一键执行：
准备 DXF → 推断规格 → 填表 → 输出（DWG 转回 DWG，DXF 保持 DXF）。
后台线程执行，日志与进度经队列回传，界面不卡顿。
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.app_config import get_oda, get_out_version
from cadbatchassistant.core.dwg_converter import require_oda_for_dwg
from cadbatchassistant.core.filetypes import XLSX_SUFFIXES
from cadbatchassistant.core.fill_learn_spec import scan_all_placeholders
from cadbatchassistant.core.fill_pipeline import run_pipeline_files
from cadbatchassistant.core.template_meta import (
    load_template_meta,
    remove_template_meta,
    save_template_meta,
)
from cadbatchassistant.core.templates import template_path, templates_dir
from cadbatchassistant.gui.async_panel import AsyncPanel
from cadbatchassistant.gui.gui_shared import (
    FilesPanelMixin,
    PanelLayoutMixin,
    RunStartMixin,
    TemplateLibraryMixin,
    warn_require,
)


class IsoFillApp(
    FilesPanelMixin, TemplateLibraryMixin, PanelLayoutMixin, RunStartMixin, AsyncPanel
):
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

    # ---------------- 模板库钩子 ----------------
    def _after_upload(self, name: str, src: str) -> None:
        """上传后从源文件扫描全部 [列名] 占位符写入模板库 meta。

        模板为 DWG 时需 ODA 转 DXF（缺失时 template_to_dxf 抛 ODAError）；
        无任何 [列名] 占位符的模板无法用于填表 → 抛 ValueError。
        两者都会使基类回滚删除已入库 meta 并弹错（拒绝上传）。
        原文件不入库，模板库只保留解析出的占位符 JSON。
        """
        converter = dc.get_converter()
        oda = converter.resolve(get_oda())
        with tempfile.TemporaryDirectory(prefix="cad_fill_meta_") as td:
            t_dxf = converter.template_to_dxf(Path(src), Path(td), oda)
            placeholders = scan_all_placeholders(t_dxf)
        if not placeholders:
            raise ValueError("模板中未找到 [列名] 占位符，无法用于填表")
        save_template_meta(
            template_path(self.TEMPLATE_CATEGORY, name), {"placeholders": placeholders}
        )

    def _after_delete(self, name: str) -> None:
        """删除模板时同步删除伴生 meta（不存在时静默）。"""
        remove_template_meta(template_path(self.TEMPLATE_CATEGORY, name))

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self._main = self._build_root()

        # 1. 输入区（与「改字助手」一致：仅选择文件 + 文件列表）
        self._add_input_section(width=60, bind_delete=True)

        # 2. 数据源区（数据表 + 图纸模板，置于输入区正下方）
        src_frame = ttk.LabelFrame(self._main, text="数据源", padding=8)
        src_frame.pack(fill="x", **self._pad)
        self._add_xlsx_row(
            src_frame,
            "数据表格:",
            XLSX_SUFFIXES,
            on_hit=lambda h: self._refresh_sources(),
        )

        # 工作表 + 匹配列（同一行）
        row_sheet = ttk.Frame(src_frame)
        row_sheet.pack(fill="x", pady=(6, 0))
        ttk.Label(row_sheet, text="工作表格:").pack(side="left")
        self.var_sheet = tk.StringVar()
        self.sheet_combo = ttk.Combobox(
            row_sheet, textvariable=self.var_sheet, state="readonly", width=16
        )
        self.sheet_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_changed)
        ttk.Label(row_sheet, text="匹配列:").pack(side="left")
        self.var_match_col = tk.StringVar()
        self.match_combo = ttk.Combobox(
            row_sheet, textvariable=self.var_match_col, state="readonly", width=16
        )
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
            title="选择数据表格",
            filetypes=[("Excel 数据表", "*.xlsx *.xls"), ("所有文件", "*.*")],
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
    def _prepare_run(self) -> tuple | None:
        """校验输入并收集 worker 参数；校验失败弹窗提示并返回 None。"""
        xlsx = self.var_xlsx.get().strip()
        tpl_name = self.var_template.get().strip()
        template = str(templates_dir("fill") / tpl_name) if tpl_name else ""
        sheet = self.var_sheet.get().strip() or None
        match_col = self.var_match_col.get().strip() or None
        files = list(self.scanned_files)
        out = self.var_out.get().strip()
        oda = get_oda()
        out_version = get_out_version()

        if not warn_require(
            bool(xlsx) and os.path.isfile(xlsx), "请选择有效的数据表格文件"
        ):
            return None
        if not warn_require(
            bool(tpl_name),
            "请从图纸模板下拉框选择模板（可先「上传」）",
        ):
            return None
        meta = load_template_meta(template)
        if meta is None:
            messagebox.showerror(
                "填表助手",
                f"模板「{tpl_name}」未配置，请删除后重新上传",
            )
            return None
        if not warn_require(
            isinstance(meta.get("placeholders"), list) and meta["placeholders"],
            f"模板「{tpl_name}」未配置任何 [列名] 占位符，请删除后重新上传",
        ):
            return None
        # 逐项校验占位符结构（手改 JSON 缺键会在 fill_pipeline 抛 KeyError，
        # 后台线程只报「处理中断」；此处提前定位到具体条目并友好报错）
        bad = next(
            (
                i
                for i, ph in enumerate(meta["placeholders"])
                if not isinstance(ph, dict)
                or not all(
                    k in ph
                    for k in (
                        "text", "layer", "x", "y", "height", "style",
                        "halign", "valign", "ref_text", "entity_desc",
                    )
                )
            ),
            None,
        )
        if bad is not None:
            messagebox.showerror(
                "填表助手",
                f"模板「{tpl_name}」配置损坏"
                f"（第 {bad + 1} 个占位符缺字段），请删除后重新上传",
            )
            return None
        if not warn_require(bool(files), "请选择要处理的 DWG/DXF 文件"):
            return None
        if not warn_require(bool(out), "请设置输出目录"):
            return None
        # 模板只读 meta（不再转换模板），仅处理图纸为 DWG 时才需要 ODA
        has_dwg = any(f.lower().endswith(".dwg") for f in files)
        err = require_oda_for_dwg(has_dwg, oda)
        if err:
            messagebox.showerror("缺少 ODA File Converter", err)
            return None

        return (
            xlsx,
            template,
            files,
            out,
            oda,
            out_version,
            self._cancel_event,
            match_col,
            sheet,
        )

    def _work(
        self,
        xlsx: str,
        template: str,
        files: list[str],
        out: str,
        oda: str,
        version: str,
        cancel,
        match_col: str | None,
        sheet: str | None,
    ) -> bool:
        summary = run_pipeline_files(
            xlsx,
            files,
            out,
            oda=oda or None,
            out_version=version,
            emit=self._emit,
            cancel=cancel,
            template=template,
            match_col=match_col,
            sheet=sheet,
            progress=lambda p: self._emit("", p),
        )
        failed = summary.get("failed", [])
        if failed:
            self._emit(
                f"==== 完成 {summary['ok']}/{summary['count']} 张，"
                f"失败 {len(failed)} 张：{', '.join(failed)}，"
                f"输出见 {summary['output']} ===="
            )
        else:
            self._emit(
                f"==== 全部完成：{summary['count']} 张图纸，"
                f"输出见 {summary['output']} ===="
            )
        return not failed
