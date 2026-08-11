"""tkinter GUI：ISO 图纸标题栏填表工具。

选择 数据表.xlsx/.xls 与图纸文件（DWG/DXF 多选），一键执行：
准备 DXF → 推断规格 → 填表 → 输出（DWG 转回 DWG，DXF 保持 DXF）。
后台线程执行，日志与进度经队列回传，界面不卡顿。
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES

from cadbatchassistant.common import (
    APP_CONFIG_FILE,
    AsyncPanel,
    build_file_list,
    build_log_panel,
    build_output_row,
    dedup_paths as _dedup_paths_common,
    load_config as _load_config_common,
    parse_dnd_data,
    save_config as _save_config_common,
)
from cadbatchassistant.core.pipeline import run_pipeline_files

CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "CadFill"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _software_dir() -> Path:
    """软件目录：exe 所在目录（打包运行）或脚本目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(os.path.dirname(os.path.abspath(__file__)))


TEMPLATES_DIR = _software_dir() / "templates"   # 图纸模板库：软件目录下 templates


def _load_config() -> dict:
    """读取「填表助手」面板配置。"""
    return _load_config_common(CONFIG_FILE)


def _save_config(data: dict) -> None:
    """写入「填表助手」面板配置。"""
    _save_config_common(CONFIG_FILE, data)


class IsoFillApp(AsyncPanel):
    def __init__(self, parent: tk.Widget) -> None:
        """构建「填表助手」面板；parent 为嵌入容器（如 Notebook 的 tab 页）。"""
        super().__init__(parent)
        self.scanned_files: list[str] = []
        self._build_ui()
        self._load_paths()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)

        # 1. 输入区（与「改字助手」一致：仅选择文件 + 文件列表）
        in_frame = ttk.LabelFrame(main, text="输入", padding=8)
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
        # 拖拽 DWG/DXF 到列表追加
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop_files)

        # 2. 数据源区（数据表 + 图纸模板，置于输入区正下方）
        src_frame = ttk.LabelFrame(main, text="数据源", padding=8)
        src_frame.pack(fill="x", **pad)

        # 数据表（xlsx/xls）
        row_xlsx = ttk.Frame(src_frame)
        row_xlsx.pack(fill="x")
        ttk.Label(row_xlsx, text="数据表:").pack(side="left")
        self.var_xlsx = tk.StringVar()
        e_xlsx = ttk.Entry(row_xlsx, textvariable=self.var_xlsx)
        e_xlsx.pack(side="left", fill="x", expand=True, padx=4)
        e_xlsx.drop_target_register(DND_FILES)
        e_xlsx.dnd_bind("<<Drop>>",
                        lambda e: self._on_drop_single(e, self.var_xlsx,
                                                       (".xlsx", ".xls")))
        ttk.Button(row_xlsx, text="浏览", command=self._browse_xlsx).pack(
            side="left", padx=4)

        # 图纸模板（模板库下拉选择）
        row_tpl = ttk.Frame(src_frame)
        row_tpl.pack(fill="x", pady=(6, 0))
        ttk.Label(row_tpl, text="图纸模板:").pack(side="left")
        self.var_template = tk.StringVar()
        self.tpl_combo = ttk.Combobox(row_tpl, textvariable=self.var_template,
                                      state="readonly", width=16)
        self.tpl_combo.pack(side="left", fill="x", expand=True, padx=4)
        self.tpl_combo.drop_target_register(DND_FILES)
        self.tpl_combo.dnd_bind("<<Drop>>", self._on_drop_upload_template)
        ttk.Button(row_tpl, text="上传", command=self._upload_template).pack(
            side="left", padx=4)
        ttk.Button(row_tpl, text="删除", command=self._delete_template).pack(
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

        # 4. 运行区（ODA 路径与输出版本已移至「设置」tab，全局共享）
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

    # ---------------- 输入 ----------------
    def _browse_xlsx(self) -> None:
        f = filedialog.askopenfilename(
            title="选择数据表", filetypes=[("Excel 数据表", "*.xlsx *.xls"), ("所有文件", "*.*")]
        )
        if f:
            self.var_xlsx.set(f)

    # ---------------- 拖拽文件 ----------------
    @staticmethod
    def _parse_dnd_data(data: str) -> list[str]:
        """解析拖拽数据为路径列表（复用 common.parse_dnd_data）。"""
        return parse_dnd_data(data)

    def _on_drop_single(self, event, var: tk.StringVar, exts: tuple) -> None:
        paths = self._parse_dnd_data(event.data)
        hit = next((p for p in paths
                    if p.lower().endswith(exts)), None)
        if hit is not None:
            var.set(hit)
        elif paths:
            messagebox.showwarning("提示", f"仅支持 {', '.join(exts)} 文件")

    def _on_drop_files(self, event) -> None:
        added = False
        for p in self._parse_dnd_data(event.data):
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

    def _on_drop_out_dir(self, event) -> None:
        paths = self._parse_dnd_data(event.data)
        d = next((p for p in paths if os.path.isdir(p)), None)
        if d is not None:
            self.var_out.set(d)
        elif paths:
            messagebox.showwarning("提示", "输出目录请拖入文件夹")

    def _browse_template(self) -> None:
        """（已改为模板库上传，此方法保留兼容）"""
        self._upload_template()

    # ---------------- 图纸模板库 ----------------
    def _list_templates(self) -> list[str]:
        """返回模板库中的模板文件名（.dwg/.dxf，排序）。"""
        if not TEMPLATES_DIR.is_dir():
            return []
        return sorted(f.name for f in TEMPLATES_DIR.iterdir()
                      if f.is_file() and f.suffix.lower() in (".dwg", ".dxf"))

    def _refresh_templates(self) -> None:
        """刷新下拉框并恢复上次选择（config.json 存模板文件名）。"""
        names = self._list_templates()
        self.tpl_combo["values"] = names
        last = _load_config().get("template", "")
        if last in names:
            self.var_template.set(last)
        elif names and not self.var_template.get():
            self.var_template.set(names[0])
        else:
            self.var_template.set("")

    def _upload_template(self, path: str | None = None) -> None:
        """把 dwg/dxf 复制进模板库并选中。"""
        if path is None:
            path = filedialog.askopenfilename(
                title="上传图纸模板（复制到模板库）",
                filetypes=[("CAD 文件", "*.dwg *.dxf"), ("DWG 文件", "*.dwg"),
                           ("DXF 文件", "*.dxf"), ("所有文件", "*.*")],
            )
            if not path:
                return
        if path.lower().endswith((".dwg", ".dxf")) and os.path.isfile(path):
            TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
            name = os.path.basename(path)
            target = TEMPLATES_DIR / name
            if target.exists() and os.path.normcase(str(target)) != os.path.normcase(path):
                if not messagebox.askyesno("覆盖", f"模板库已存在 {name}，是否覆盖？"):
                    return
            import shutil

            shutil.copy2(path, target)
            self._refresh_templates()
            self.var_template.set(name)
            _save_config({"template": name})
        else:
            messagebox.showwarning("提示", "仅支持上传 .dwg/.dxf 文件")

    def _delete_template(self) -> None:
        name = self.var_template.get().strip()
        if not name:
            messagebox.showwarning("提示", "请先选择要删除的模板")
            return
        if not messagebox.askyesno("确认删除", f"确定删除模板「{name}」吗？"):
            return
        target = TEMPLATES_DIR / name
        try:
            target.unlink()
        except OSError as ex:  # noqa: BLE001
            messagebox.showerror("删除失败", str(ex))
            return
        self._refresh_templates()
        _save_config({"template": self.var_template.get()})

    def _on_drop_upload_template(self, event) -> None:
        paths = self._parse_dnd_data(event.data)
        hit = next((p for p in paths
                    if p.lower().endswith((".dwg", ".dxf")) and os.path.isfile(p)), None)
        if hit is None:
            messagebox.showwarning("提示", "仅支持拖入 .dwg/.dxf 图纸模板（将上传到模板库）")
            return
        self._upload_template(hit)

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

    @staticmethod
    def _dedup_paths(paths: list[str]) -> list[str]:
        return _dedup_paths_common(paths)

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

    def _default_output(self) -> None:
        if self.scanned_files:
            self.var_out.set(str(Path(self.scanned_files[0]).parent / "output"))

    def _browse_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(title="选择目录")
        if d:
            var.set(d)

    def _load_paths(self) -> None:
        # 仅恢复上次选择的图纸模板；输入输出路径不设默认值、不记忆恢复
        # （ODA 路径与输出版本为全局设置，见「设置」tab）
        self._refresh_templates()

    # ---------------- 运行 ----------------
    def _start(self) -> None:
        if self.running:
            return
        xlsx = self.var_xlsx.get().strip()
        tpl_name = self.var_template.get().strip()
        template = os.path.join(TEMPLATES_DIR, tpl_name) if tpl_name else ""
        files = list(self.scanned_files)
        out = self.var_out.get().strip()
        app_cfg = _load_config_common(APP_CONFIG_FILE)
        oda = app_cfg.get("oda", "").strip()
        out_version = app_cfg.get("version", "ACAD2018")

        if not xlsx or not os.path.isfile(xlsx):
            messagebox.showwarning("提示", "请选择有效的数据表文件")
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
        if any(f.lower().endswith(".dwg") for f in files) or template.lower().endswith(".dwg"):
            if not oda or not os.path.isfile(oda):
                messagebox.showerror(
                    "缺少 ODA File Converter",
                    "输入包含 DWG 文件，未找到 ODAFileConverter.exe，"
                    "请安装或在「设置」页配置其路径。（仅 DXF 文件无需 ODA）",
                )
                return

        self.running = True
        self._cancel_event.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.progress.config(value=0)
        self.log_text.delete("1.0", "end")

        self._start_worker((xlsx, template, files, out, oda,
                            out_version, self._cancel_event))

    def _run_worker(self, xlsx: str, template: str, files: list[str], out: str,
                    oda: str, version: str, cancel) -> None:
        success = False
        try:
            summary = run_pipeline_files(
                xlsx, files, out, oda=oda or None, out_version=version,
                emit=self._emit, cancel=cancel, template=template,
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
            success = not failed
        except Exception as ex:  # noqa: BLE001
            self._emit(f"处理中断：{ex}")
        finally:
            self.msg_queue.put(("__DONE__", success))

    def _on_finish(self, success: bool) -> None:
        """完成收尾：恢复按钮并弹窗汇总（与「改字助手」一致）。"""
        super()._on_finish(success)
        if success:
            messagebox.showinfo("完成", "处理完成，请查看日志。")
        else:
            messagebox.showwarning("完成", "处理中断，详见日志。")



