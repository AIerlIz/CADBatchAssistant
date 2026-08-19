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
from tkinter import filedialog, ttk

from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.common.app_config import get_oda
from cadbatchassistant.core.common.filetypes import XLSX_SUFFIXES
from cadbatchassistant.core.common.template_meta import (
    remove_template_meta,
    save_template_meta,
)
from cadbatchassistant.core.common.templates import template_path, templates_dir
from cadbatchassistant.core.fill.fill_learn_spec import scan_all_placeholders
from cadbatchassistant.core.fill.fill_pipeline import run_pipeline_files
from cadbatchassistant.gui.components.async_panel import AsyncPanel
from cadbatchassistant.gui.mixins.gui_shared import (
    FilesPanelMixin,
    PanelLayoutMixin,
    RunStartMixin,
    TemplateLibraryMixin,
    get_app_runtime_config,
    validate_template_meta,
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
        self._sync_sheet_row_retry = 0  # _sync_sheet_row 重试计数器
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
        # 「图纸模板」行沿用共享 _add_template_row（不改动）。为使「工作表格」
        # 下拉框与「图纸模板」下拉框左右边缘同宽对齐（且高度正常），把「工作
        # 表格」下拉框放进定宽定高容器（_sync_sheet_row 同步为图纸模板下拉框
        # 的宽高）；「匹配列」标签+下拉靠右贴行端，右侧不留白。
        row_sheet = ttk.Frame(src_frame)
        row_sheet.pack(fill="x", pady=(6, 0))
        ttk.Label(row_sheet, text="工作表格:").pack(side="left")
        self._sheet_box = ttk.Frame(row_sheet, width=0, height=0)
        self._sheet_box.pack_propagate(False)  # 尺寸由 _sync_sheet_row 精确给定
        # 左侧 padx=4 与图纸模板下拉框（_add_template_row 内 padx=4）对齐
        self._sheet_box.pack(side="left", padx=(4, 0))
        self.var_sheet = tk.StringVar()
        self.sheet_combo = ttk.Combobox(
            self._sheet_box, textvariable=self.var_sheet, state="readonly", width=16
        )
        self.sheet_combo.pack(fill="both", expand=True)  # 填满容器（宽=图纸模板宽）
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_changed)
        # 匹配列：下拉框放入定宽容器，左右边缘对齐图纸模板的「上传/编辑/删除」
        # 按钮区（左=上传左，右=删除右）；「匹配列:」标签在容器内左侧，下拉
        # 在容器内右侧填满剩余宽度。
        self._match_box = ttk.Frame(row_sheet, width=0, height=0)
        self._match_box.pack_propagate(False)  # 尺寸由 _sync_sheet_row 精确给定
        self._match_box.pack(side="right")
        ttk.Label(self._match_box, text="匹配列:").pack(side="left")
        self.var_match_col = tk.StringVar()
        self.match_combo = ttk.Combobox(
            self._match_box, textvariable=self.var_match_col, state="readonly", width=10
        )
        self.match_combo.pack(
            side="left", fill="both", expand=True, padx=(4, 0)
        )  # 与"工作表格:"旁间距一致

        # 图纸模板（模板库下拉选择，完全沿用共享实现）
        self._add_template_row(src_frame)
        # 在模板行/数据源尺寸变化（含首次布局、窗口缩放）时同步工作表格下拉框
        # 容器尺寸 = 图纸模板下拉框尺寸；src_frame 与模板行大小一致但各自独立
        # 触发 Configure。
        src_frame.bind(
            "<Configure>", lambda _e: src_frame.after_idle(self._sync_sheet_row)
        )
        self.tpl_combo.master.bind(
            "<Configure>", lambda _e: src_frame.after_idle(self._sync_sheet_row)
        )
        # 布局异步完成：先用 after_idle 等当前事件循环排空，再设一个上限
        # 为 3 次的重试兜底（低配机/多屏缩放场景几何稳定可能需要多轮）。
        # _sync_sheet_row 本身幂等（几何稳定后重复调用不产生副作用），
        # 用 _sync_sheet_row_retry 计数控制最多重试次数，避免无限循环。
        self._sync_sheet_row_retry = 0
        src_frame.after_idle(self._sync_sheet_row)

        # 3. 输出区
        self.var_out = tk.StringVar()
        self._add_output_section(self.var_out)

        # 4. 运行区（ODA 路径与输出版本已移至「设置」tab，全局共享）
        self._add_run_section(maximum=100)

        # 5. 日志区
        self._add_log_section()

    def _sync_sheet_row(self, _event=None) -> None:
        """对齐「工作表格」下拉框与「图纸模板」下拉框（同宽同高），且「匹配列」
        下拉框左右边缘对齐图纸模板的「上传/编辑/删除」按钮区。

        不动「图纸模板」行。把「工作表格」下拉框容器(_sheet_box)尺寸同步为图纸
        模板下拉框尺寸（两行同为 src_frame 的子行、左侧标签同宽，故左右边缘
        对齐）；把「匹配列」下拉框容器(_match_box)宽度同步为按钮区宽
        （删除按钮右缘 - 上传按钮左缘）。均同时给高度，避免 pack_propagate(False)
        把下拉框压成一条线。
        布局异步完成：先用 after_idle 等当前事件循环排空，再配合 _sync_sheet_row_retry
        计数器兜底（最多重试 3 次，低配机/多屏缩放场景几何稳定可能需要多轮）。
        调用幂等，几何稳定后重复调用不产生副作用。
        """
        try:
            tpl_h = self.tpl_combo.winfo_height()
            self._sheet_box.configure(
                width=self.tpl_combo.winfo_width(),
                height=tpl_h,
            )
            btns = [
                c
                for c in self.tpl_combo.master.winfo_children()
                if c.winfo_class() == "TButton"
            ]
            if btns:
                btn_left = btns[0].winfo_rootx()
                btn_right = btns[-1].winfo_rootx() + btns[-1].winfo_width()
                self._match_box.configure(
                    width=max(btn_right - btn_left, 0),
                    height=tpl_h,
                )
                # 删除按钮右侧 padx=4，行右端比删除右端多 4px，需要右移匹配列左边缘
                row = self._match_box.master
                row_right = row.winfo_rootx() + row.winfo_width()
                self._match_box.pack_configure(
                    padx=(0, max(row_right - btn_right, 0))
                )
        except Exception:  # noqa: BLE001 - 布局未就绪，重试
            self._sync_sheet_row_retry += 1
            if self._sync_sheet_row_retry <= 3:
                self._root.after_idle(self._sync_sheet_row)
            return
        # 同步成功：重置计数器，不再重试
        self._sync_sheet_row_retry = 0

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
                from cadbatchassistant.core.fill.fill_parse_xlsx import load_sheet_meta

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
        oda, out_version = get_app_runtime_config()

        if not warn_require(
            bool(xlsx) and os.path.isfile(xlsx), "请选择有效的数据表格文件"
        ):
            return None
        if not warn_require(
            bool(tpl_name),
            "请从图纸模板下拉框选择模板（可先「上传」）",
        ):
            return None
        meta = validate_template_meta(
            category="fill",
            tpl_name=tpl_name,
            template_path=template,
            panel_title="填表助手",
            list_key="placeholders",
            extra_checks=((
                ("text", "layer", "x", "y", "height", "style",
                 "halign", "valign", "ref_text", "entity_desc"),
                "占位符缺字段",
            ),),
        )
        if meta is None:
            return None
        if not warn_require(bool(files), "请选择要处理的 DWG/DXF 文件"):
            return None
        if not warn_require(bool(out), "请设置输出目录"):
            return None
        # 模板只读 meta（不再转换模板），仅处理图纸为 DWG 时才需要 ODA
        has_dwg = any(f.lower().endswith(".dwg") for f in files)
        if not warn_require(
            not has_dwg or dc.get_converter().require_for_dwg(has_dwg, oda) is None,
            "缺少 ODA File Converter，请安装或在「设置」页配置其路径",
            title="缺少 ODA File Converter",
        ):
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
