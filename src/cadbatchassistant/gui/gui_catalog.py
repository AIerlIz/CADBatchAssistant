"""「目录助手」面板：从 DWG/DXF 图纸按标记模板取值，生成图纸目录 Excel。

- 待处理区：图纸文件多选列表（选择/追加/右键删除/Delete/拖放）
- 数据源区：标记模板库下拉（上传/删除到 templates/catalog）+ 表格模板（Excel）
- 输出区：输出目录（默认 = 第一个图纸文件所在目录 output）
- 运行区：开始/停止 + 进度条；日志区

后台线程执行 catalog_pipeline（模板解析 → 取值 → 文件粒度目录），
日志与进度经队列回传。模板库与填表助手分离（templates/catalog vs templates/fill）。
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES

from openpyxl import load_workbook

from cadbatchassistant.common import (
    AsyncPanel,
    build_file_list,
    build_log_panel,
    build_output_row,
    dedup_paths as _dedup_paths_common,
    delete_template_file,
    get_oda,
    get_out_version,
    list_templates,
    load_catalog_rules,
    load_config,
    parse_dnd_data,
    save_config,
    templates_dir,
    upload_template_file,
)
from cadbatchassistant.core.catalog_excel_writer import detect_sheet_candidates
from cadbatchassistant.core.catalog_pipeline import (
    PipelineResult,
    parse_template_fields,
    run_pipeline,
)

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "CadFill"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_panel_config() -> dict:
    """读取「目录助手」面板配置（模板/表格模板/输出记忆，catalog_ 前缀分组）。"""
    return load_config(CONFIG_FILE)


def _save_panel_config(data: dict) -> None:
    """写入「目录助手」面板配置（与填表助手同文件，catalog_ 前缀键互不干扰）。"""
    save_config(CONFIG_FILE, data)


class CatalogPanel(AsyncPanel):
    """目录生成面板（模板标记取值）。"""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.scanned_files: list[str] = []
        self._last_result: PipelineResult | None = None
        self._build_ui()
        self._load()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)

        # 1. 待处理区：图纸文件多选
        in_frame = ttk.LabelFrame(main, text="待处理", padding=8)
        in_frame.pack(fill="x", **pad)
        top = ttk.Frame(in_frame)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="选择文件", command=self._browse_input_files).pack(side="left")
        self.var_scan_info = tk.StringVar(value="尚未选择文件")
        ttk.Label(top, textvariable=self.var_scan_info).pack(side="left", padx=10)

        self.file_list, self._file_menu = build_file_list(
            in_frame, height=6, on_delete=self._delete_selected_files,
            fill="both", width=60)
        self.file_list.bind("<Delete>", lambda _e: self._delete_selected_files())
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop_files)

        # 2. 数据源区：标记模板库 + 表格模板
        src_frame = ttk.LabelFrame(main, text="数据源", padding=8)
        src_frame.pack(fill="x", **pad)

        row_tpl = ttk.Frame(src_frame)
        row_tpl.pack(fill="x")
        ttk.Label(row_tpl, text="标记模板:").pack(side="left")
        self.var_template = tk.StringVar()
        self.tpl_combo = ttk.Combobox(row_tpl, textvariable=self.var_template,
                                      state="readonly", width=24)
        self.tpl_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.tpl_combo.drop_target_register(DND_FILES)
        self.tpl_combo.dnd_bind("<<Drop>>", self._on_drop_upload_template)
        ttk.Button(row_tpl, text="上传", command=self._upload_template).pack(
            side="left", padx=4)
        ttk.Button(row_tpl, text="删除", command=self._delete_template).pack(
            side="left", padx=4)

        row_xlsx = ttk.Frame(src_frame)
        row_xlsx.pack(fill="x", pady=(6, 0))
        ttk.Label(row_xlsx, text="表格模板:").pack(side="left")
        self.var_xlsx = tk.StringVar()
        e_xlsx = ttk.Entry(row_xlsx, textvariable=self.var_xlsx)
        e_xlsx.pack(side="left", fill="x", expand=True, padx=4)
        e_xlsx.drop_target_register(DND_FILES)
        e_xlsx.dnd_bind("<<Drop>>",
                        lambda e: self._on_drop_single(e, self.var_xlsx, (".xlsx",)))
        ttk.Button(row_xlsx, text="浏览", command=self._browse_xlsx).pack(
            side="left", padx=4)

        # 3. 输出区
        out_frame = ttk.LabelFrame(main, text="输出", padding=8)
        out_frame.pack(fill="x", **pad)
        self.var_out = tk.StringVar()
        build_output_row(
            out_frame, self.var_out,
            on_browse=lambda: self._browse_dir(self.var_out),
            on_default=self._default_output,
            entry_hook=lambda e: (e.drop_target_register(DND_FILES),
                                  e.dnd_bind("<<Drop>>", self._on_drop_out_dir)))

        # 4. 运行区（ODA 路径与输出版本在「设置」tab，全局共享）
        run_frame = ttk.Frame(main)
        run_frame.pack(fill="x", **pad)
        self.btn_start = ttk.Button(run_frame, text="开始处理", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(run_frame, text="停止", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(run_frame, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

        # 5. 日志区
        log_frame, self.log_text = build_log_panel(main, height=8)
        log_frame.pack(fill="both", expand=True, **pad)

    # ---------------- 图纸文件 ----------------
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
        self.scanned_files = self._dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if not self.var_out.get().strip():
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
        self.scanned_files = self._dedup_paths(self.scanned_files)
        self._refresh_file_list()
        if not self.var_out.get().strip():
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

    @staticmethod
    def _dedup_paths(paths: list[str]) -> list[str]:
        return _dedup_paths_common(paths)

    # ---------------- 表格模板（Excel） ----------------
    def _browse_xlsx(self) -> None:
        f = filedialog.askopenfilename(
            title="选择表格模板",
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if f:
            self.var_xlsx.set(f)
            _save_panel_config({"catalog_xlsx": f})

    def _on_drop_single(self, event, var: tk.StringVar, exts: tuple) -> None:
        hit = next((p for p in parse_dnd_data(event.data)
                    if p.lower().endswith(exts)), None)
        if hit is not None:
            var.set(hit)
            _save_panel_config({"catalog_xlsx": hit})
        elif parse_dnd_data(event.data):
            messagebox.showwarning("提示", f"仅支持 {', '.join(exts)} 文件")

    # ---------------- 标记模板库（templates/catalog） ----------------
    def _refresh_templates(self) -> None:
        """刷新下拉框并恢复上次选择（config.json 存模板文件名）。"""
        names = list_templates("catalog")
        self.tpl_combo["values"] = names
        last = _load_panel_config().get("catalog_template", "")
        if last in names:
            self.var_template.set(last)
        elif names and not self.var_template.get():
            self.var_template.set(names[0])
        else:
            self.var_template.set("")

    def _upload_template(self, path: str | None = None) -> None:
        """把 dwg/dxf 复制进标记模板库（templates/catalog）并选中。"""
        name = upload_template_file(
            "catalog", path, title="上传标记模板（[字段名] 取值位置）")
        if name:
            self._refresh_templates()
            self.var_template.set(name)
            _save_panel_config({"catalog_template": name})

    def _delete_template(self) -> None:
        name = self.var_template.get().strip()
        if delete_template_file("catalog", name):
            _save_panel_config({"catalog_template": ""})
            self._refresh_templates()

    def _on_drop_upload_template(self, event) -> None:
        hit = next((p for p in parse_dnd_data(event.data)
                    if p.lower().endswith((".dwg", ".dxf")) and os.path.isfile(p)), None)
        if hit is None:
            messagebox.showwarning("提示", "仅支持拖入 .dwg/.dxf 标记模板（将上传到模板库）")
            return
        self._upload_template(hit)

    # ---------------- 输出目录 ----------------
    def _default_output(self) -> None:
        if self.scanned_files:
            out = str(Path(self.scanned_files[0]).parent / "output")
            self.var_out.set(out)
            _save_panel_config({"catalog_out": out})

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(title="选择目录")
        if d:
            var.set(d)
            _save_panel_config({"catalog_out": d})

    def _on_drop_out_dir(self, event) -> None:
        d = next((p for p in parse_dnd_data(event.data) if os.path.isdir(p)), None)
        if d is not None:
            self.var_out.set(d)
            _save_panel_config({"catalog_out": d})
        elif parse_dnd_data(event.data):
            messagebox.showwarning("提示", "输出目录请拖入文件夹")

    # ---------------- 配置记忆 ----------------
    def _load(self) -> None:
        self._refresh_templates()  # 恢复上次选择的标记模板
        cfg = _load_panel_config()
        last_xlsx = cfg.get("catalog_xlsx", "")
        if last_xlsx and os.path.isfile(last_xlsx):
            self.var_xlsx.set(last_xlsx)
        last_out = cfg.get("catalog_out", "")
        if last_out and os.path.isdir(last_out):
            self.var_out.set(last_out)

    # ---------------- 运行 ----------------
    def _start(self) -> None:
        if self.running:
            return
        tpl_name = self.var_template.get().strip()
        template = str(templates_dir("catalog") / tpl_name) if tpl_name else ""
        xlsx = self.var_xlsx.get().strip()
        files = list(self.scanned_files)
        out = self.var_out.get().strip()

        if not tpl_name or not os.path.isfile(template):
            messagebox.showwarning("提示", "请从标记模板下拉框选择模板（可先「上传」）")
            return
        if not xlsx or not os.path.isfile(xlsx):
            messagebox.showwarning("提示", "请选择有效的表格模板")
            return
        if not files:
            messagebox.showwarning("提示", "请选择要处理的 DWG/DXF 文件")
            return
        if not out:
            self._default_output()
            out = self.var_out.get().strip()
        if not out:
            messagebox.showwarning("提示", "请设置输出目录")
            return

        # 表格模板 sheet 预检（主线程）：解析模板字段名并反推 sheet，
        # 无匹配提前弹错；多个 sheet 并列最高时让用户选择。
        oda = get_oda()
        try:
            fields = parse_template_fields(template, oda)
        except Exception as ex:  # noqa: BLE001 - 模板解析失败提前提示
            messagebox.showerror("图纸目录助手", f"模板解析失败：{ex}")
            return
        sheet_name: str | None = None
        try:
            wb = load_workbook(xlsx)
            cands = detect_sheet_candidates(wb, fields)
            wb.close()
        except Exception as ex:  # noqa: BLE001 - 表格模板读取失败提前提示
            messagebox.showerror("图纸目录助手", f"读取表格模板失败：{ex}")
            return
        if not cands:
            messagebox.showerror(
                "图纸目录助手",
                "表格模板中未找到与字段匹配的表头（sheet 与表头行）（字段："
                + "、".join(fields)
                + "）。表头列名应包含与标记模板 [字段名] 占位符一致的字段名。")
            return
        tied = [c for c in cands if c[0] == cands[0][0]]
        if len(tied) > 1:
            sheet_name = self._ask_sheet(tied, fields)
            if sheet_name is None:
                return
        self.log_text.delete("1.0", "end")
        self._emit(f"表格模板 sheet：{sheet_name or '自动定位'}")
        self.running = True
        self._cancel_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.progress.config(value=0)
        self._start_worker((
            template, xlsx, files, out,
            oda, get_out_version(), load_catalog_rules(), sheet_name,
        ))

    def _ask_sheet(self, candidates, fields: list[str]) -> str | None:
        """多个 sheet 并列最高时弹窗选择，返回所选 sheet 名；取消/关闭返回 None。"""
        names = [c[1].title for c in candidates]
        pick: dict[str, str | None] = {"name": None}
        win = tk.Toplevel(self._root)
        win.title("选择表格模板 sheet")
        win.transient(self._root)
        win.resizable(False, False)
        ttk.Label(
            win,
            text="表格模板中有多个 sheet 与字段匹配（字段："
                 + "、".join(fields)
                 + "），请选择本次使用的 sheet：",
            wraplength=420,
        ).pack(padx=12, pady=(12, 6))
        lb = tk.Listbox(win, height=min(len(names), 8), width=40,
                        selectmode="single")
        for n in names:
            lb.insert("end", n)
        lb.selection_set(0)
        lb.pack(padx=12, pady=4)

        def _ok() -> None:
            sel = lb.curselection()
            if sel:
                pick["name"] = names[sel[0]]
            win.destroy()

        def _cancel() -> None:
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="确定", command=_ok).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=_cancel).pack(side="left", padx=6)
        win.protocol("WM_DELETE_WINDOW", _cancel)
        win.grab_set()
        self._root.wait_window(win)
        return pick["name"]

    def _emit_log(self, msg: str) -> None:
        self._emit(msg)

    def _work(self, template, xlsx, files, out, oda, version, rules,
              sheet_name) -> bool:
        res = run_pipeline(
            template, xlsx, files, out, oda, version, rules,
            sheet_name=sheet_name,
            log=self._emit_log, progress=lambda p: self._emit(None, int(p)),
            is_cancelled=self._is_cancelled,
        )
        self._last_result = res
        if res.error:
            self._emit(f"处理失败：{res.error}")
        return res.ok

    def _on_finish(self, success: bool) -> None:
        super()._on_finish(success)
        res = self._last_result
        if res is None:
            return
        if success and res.out_path:
            msg = (f"目录生成完成：{res.out_path}\n\n"
                   f"图纸：{res.total_files} 个文件\n"
                   f"无管段(NA)：{res.na_rows} 张\n"
                   f"总页数：{res.total_pages}\n"
                   f"字段：{' / '.join(res.fields)}\n")
            if res.failed_files:
                msg += f"\n转换失败：{len(res.failed_files)} 个\n" + "\n".join(res.failed_files[:10])
            messagebox.showinfo("图纸目录助手", msg)
        elif not success:
            messagebox.showerror("图纸目录助手", f"处理失败：\n{res.error or '未知错误'}")
