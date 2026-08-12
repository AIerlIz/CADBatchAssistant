"""tkinter GUI 界面模块。

主窗口包含：输入/输出目录选择、文件列表预览、规则管理、
选项（大小写敏感、dry-run）、进度条与实时日志面板。
（ODA 路径与输出版本位于「设置」tab，全局共享。）
处理逻辑在后台线程执行，通过队列回传日志与进度，避免界面卡顿。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from tkinterdnd2 import DND_FILES

from cadbatchassistant.common import (
    AsyncPanel,
    build_file_list,
    build_log_panel,
    build_output_row,
    get_oda,
    get_out_version,
)
from cadbatchassistant.core.text_replace import ReplaceRule, process_dxf_file
from cadbatchassistant.core.dwg_converter import (
    ODAError,
    convert_dwg_batch_to_dxf,
    convert_dxf_batch_to_dwg,
    require_oda_for_dwg,
)
from cadbatchassistant.gui.gui_shared import (
    FilesPanelMixin,
    begin_run,
    finish_popup,
)


class EditableTreeview(ttk.Treeview):
    """支持单元格内联编辑、底部「添加规则」行、Delete/右键删除的表格。

    交互约定：
    - 双击单元格：出现输入框编辑，Enter/失焦提交，Esc 取消，Tab 跳下一格
    - 单击底部「＋ 点击添加规则」行：新增一行并进入编辑
    - 选中行按 Delete 或右键「删除选中行」：删除
    """

    def __init__(self, master, app, **kw) -> None:
        super().__init__(master, **kw)
        self._app = app
        self._editor: tk.Entry | None = None
        self._edit_iid: str | None = None
        self._edit_col: int = 0

        self.bind("<Double-1>", self._on_double)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Delete>", self._on_delete)
        self.bind("<Button-3>", self._on_right_click)
        self._menu = tk.Menu(master, tearoff=0)
        self._menu.add_command(label="删除选中行", command=self._menu_delete)

    # ---------- 事件 ----------
    def _on_click(self, event) -> str | None:
        iid = self.identify_row(event.y)
        if iid == "__add__":
            self._app.add_rule_row()
            return "break"
        return None

    def _on_double(self, event) -> None:
        if self.identify_region(event.x, event.y) != "cell":
            return
        iid = self.identify_row(event.y)
        col = self.identify_column(event.x)  # "#1" / "#2"
        if not iid or iid == "__add__" or col not in ("#1", "#2"):
            return
        self._start_edit(iid, col)

    def _on_delete(self, _event=None) -> str:
        self._app.delete_selected_rules()
        return "break"

    def _on_right_click(self, event) -> str:
        iid = self.identify_row(event.y)
        if iid and iid != "__add__":
            if iid not in self.selection():
                self.selection_set(iid)
            self._menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _menu_delete(self) -> None:
        self._app.delete_selected_rules()

    # ---------- 内联编辑 ----------
    def _start_edit(self, iid: str, col: str) -> None:
        self._cancel_edit()
        try:
            idx = int(iid)
        except ValueError:
            return
        if not (0 <= idx < len(self._app.rules_data)):
            return
        col_idx = 0 if col == "#1" else 1
        box = self.bbox(iid, col)
        if not box:  # 窗口未显示/行未布局时跳过编辑
            return
        x, y, w, h = box
        var = tk.StringVar(value=self._app.rules_data[idx][col_idx])
        ed = tk.Entry(self, textvariable=var, borderwidth=1, relief="solid")
        ed.place(x=x, y=y, width=w, height=h)
        self._editor, self._edit_iid, self._edit_col = ed, iid, col_idx
        ed.focus_set()
        ed.select_range(0, "end")
        ed.bind("<Return>", lambda _e: self._commit())
        ed.bind("<Escape>", lambda _e: self._cancel_edit())
        ed.bind("<FocusOut>", lambda _e: self._commit())
        ed.bind("<Tab>", lambda _e: self._next_cell())

    def _commit(self) -> None:
        if self._editor is None:
            return
        ed, iid, col = self._editor, self._edit_iid, self._edit_col
        value = ed.get()
        self._cancel_edit()
        self._app.commit_edit(int(iid), col, value)

    def _cancel_edit(self) -> None:
        if self._editor is not None:
            self._editor.destroy()
            self._editor = None

    def _next_cell(self) -> str:
        if self._editor is None:
            return "break"
        ed, iid, col = self._editor, self._edit_iid, self._edit_col
        value = ed.get()
        self._cancel_edit()
        self._app.commit_edit(int(iid), col, value)

        children = self.get_children()
        if col == 0:
            new_iid, new_col = iid, "#2"
        else:
            pos = children.index(iid) if iid in children else -1
            if 0 <= pos + 1 < len(children) and children[pos + 1] != "__add__":
                new_iid, new_col = children[pos + 1], "#1"
            else:
                self._app.add_rule_row()  # 追加到末尾，并已打开首格编辑
                return "break"
        self._start_edit(new_iid, new_col)
        return "break"


class CadTextApp(AsyncPanel, FilesPanelMixin):
    def __init__(self, parent: tk.Widget) -> None:
        """构建「改字助手」面板；parent 为嵌入容器（如 Notebook 的 tab 页）。"""
        super().__init__(parent)
        self.scanned_files: list[str] = []
        self.rules_data: list[tuple[str, str]] = []

        self._build_ui()
        self._refresh_rule_list()  # 渲染表格与底部「添加规则」行

    # ---------------- UI 构建 ----------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(self._parent, padding=8)
        main.pack(fill="both", expand=True)

        # 1. 输入区
        file_frame = ttk.LabelFrame(main, text="待处理", padding=8)
        file_frame.pack(fill="x", **pad)

        top = ttk.Frame(file_frame)
        top.pack(fill="x", pady=(0, 4))
        ttk.Button(top, text="选择文件", command=self._browse_input_files).pack(side="left")
        self.var_scan_info = tk.StringVar(value="尚未选择文件")
        ttk.Label(top, textvariable=self.var_scan_info).pack(side="left", padx=10)

        self.file_list, self._file_menu = build_file_list(
            file_frame, height=6, on_delete=self._delete_selected_files)
        # 拖拽 DWG/DXF 到列表追加
        self.file_list.drop_target_register(DND_FILES)
        self.file_list.dnd_bind("<<Drop>>", self._on_drop_files)

        # 2. 规则区（表格内直接增删改）
        rule_frame = ttk.LabelFrame(main, text="替换规则（按顺序执行）", padding=8)
        rule_frame.pack(fill="x", **pad)

        rule_tree_frame = ttk.Frame(rule_frame)
        rule_tree_frame.pack(fill="x")
        self.rule_tree = EditableTreeview(
            rule_tree_frame, self, columns=("find", "replace"), show="headings", height=5
        )
        self.rule_tree.heading("find", text="查找")
        self.rule_tree.heading("replace", text="替换为")
        self.rule_tree.column("find", width=320)
        self.rule_tree.column("replace", width=320)
        tree_scroll = ttk.Scrollbar(
            rule_tree_frame, orient="vertical", command=self.rule_tree.yview
        )
        self.rule_tree.config(yscrollcommand=tree_scroll.set)
        self.rule_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        rule_tree_frame.columnconfigure(0, weight=1)

        # 3. 选项区（ODA 路径与输出版本已移至「设置」tab，全局共享）
        opt_frame = ttk.LabelFrame(main, text="选项", padding=8)
        opt_frame.pack(fill="x", **pad)

        self.var_case = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="大小写敏感", variable=self.var_case).grid(
            row=0, column=0, sticky="w"
        )
        self.var_regex = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="正则模式", variable=self.var_regex).grid(
            row=0, column=1, sticky="w", padx=8
        )
        self.var_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="dry-run（只统计不写文件）", variable=self.var_dry).grid(
            row=0, column=2, sticky="w", padx=8
        )
        opt_frame.columnconfigure(2, weight=1)

        # 4. 输出区
        out_frame = ttk.LabelFrame(main, text="输出", padding=8)
        out_frame.pack(fill="x", **pad)
        self.var_output = tk.StringVar()
        build_output_row(
            out_frame, self.var_output,
            on_browse=lambda: self._browse_dir(self.var_output),
            on_default=self._default_output,
            entry_hook=lambda e: (e.drop_target_register(DND_FILES),
                                  e.dnd_bind("<<Drop>>", self._on_drop_out_dir)))

        # 5. 运行区
        run_frame = ttk.Frame(main)
        run_frame.pack(fill="x", **pad)
        self.btn_start = ttk.Button(run_frame, text="开始处理", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(run_frame, text="停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(run_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=8)

        # 6. 日志区
        log_frame, self.log_text = build_log_panel(main, height=8)
        log_frame.pack(fill="both", expand=True, **pad)

    # ---------------- 规则管理（表格内联增删改） ----------------
    def _refresh_rule_list(self) -> None:
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, (find, replace) in enumerate(self.rules_data):
            self.rule_tree.insert("", "end", iid=str(i), values=(find, replace))
        self.rule_tree.insert("", "end", iid="__add__", values=("＋ 点击添加规则", ""))

    def _rules(self) -> list[ReplaceRule]:
        return [
            ReplaceRule(find=find, replace=replace,
                        case_sensitive=self.var_case.get(),
                        regex=self.var_regex.get())
            for find, replace in self.rules_data
            if find
        ]

    def add_rule_row(self) -> None:
        """表格底部添加行：新增一条空规则并进入编辑。"""
        self.rules_data.append(("", ""))
        self._refresh_rule_list()
        children = self.rule_tree.get_children()
        new_iid = children[-2] if len(children) >= 2 else None
        if new_iid:
            self.rule_tree.selection_set(new_iid)
            self.rule_tree._start_edit(new_iid, "#1")

    def commit_edit(self, idx: int, col: int, value: str) -> None:
        """内联编辑提交：col 0=查找，1=替换为；两个都为空则删除该行。

        正则模式下查找按正则校验合法性，非法弹窗拒绝写入；
        普通文本模式自动转义，无需校验。
        """
        if not (0 <= idx < len(self.rules_data)):
            return
        find, replace = self.rules_data[idx]
        if col == 0:
            find = value
        else:
            replace = value
        if not find and not replace:
            del self.rules_data[idx]
            self._refresh_rule_list()
            return
        if find and self.var_regex.get():
            try:
                re.compile(find)
            except re.error as ex:
                messagebox.showwarning(
                    "正则无效", f"「{find}」不是有效的正则表达式：\n{ex}"
                )
                self._refresh_rule_list()
                return
            if replace:
                try:
                    re.compile(find).subn(replace, "")
                except re.error as ex:
                    messagebox.showwarning(
                        "替换文本无效",
                        f"「{replace}」中的反斜杠转义或反向引用无效：\n{ex}\n"
                        "（路径等反斜杠需写 \\\\，反向引用如 \\1 需有对应捕获组）",
                    )
                    self._refresh_rule_list()
                    return
        self.rules_data[idx] = (find, replace)
        self._refresh_rule_list()

    def delete_selected_rules(self) -> None:
        """删除表格中选中的规则行。"""
        idxs = sorted((int(i) for i in self.rule_tree.selection()), reverse=True)
        for idx in idxs:
            if 0 <= idx < len(self.rules_data):
                del self.rules_data[idx]
        self._refresh_rule_list()

    # ---------------- 运行 ----------------
    def _start(self) -> None:
        if self.running:
            return
        rules = self._rules()
        if not rules:
            messagebox.showwarning("提示", "请至少添加一条替换规则")
            return
        inp = ""
        if not self.scanned_files:
            messagebox.showwarning("提示", "请选择要处理的 DWG/DXF 文件")
            return
        out = self.var_output.get().strip()
        if not out:
            self._default_output()
            out = self.var_output.get().strip()
        if not out:
            messagebox.showwarning("提示", "请设置输出目录")
            return
        oda = get_oda()
        out_version = get_out_version()
        has_dwg = any(p.lower().endswith(".dwg") for p in self.scanned_files)
        if has_dwg and not self.var_dry.get():
            err = require_oda_for_dwg(True, oda)
            if err:
                messagebox.showerror("缺少 ODA File Converter", err)
                return

        begin_run(self, maximum=len(self.scanned_files))

        # 文件模式下：把所选文件复制到临时输入目录（DWG 转换需要目录），处理完清理
        work_in = None
        if not self.var_dry.get():
            work_in = tempfile.mkdtemp(prefix="cad_text_input_")
            for p in list(self.scanned_files):
                shutil.copy2(str(p), os.path.join(work_in, os.path.basename(p)))
            inp = work_in

        self._start_worker((inp, out, rules, self.var_dry.get(),
                            oda, out_version, work_in))

    # ---------------- 批处理（后台线程） ----------------
    def _work(self, inp: str, out: str, rules: list[ReplaceRule],
              dry_run: bool, oda: str, out_version: str,
              work_in: str | None = None) -> bool:
        try:
            self._run_batch(inp, out, rules, dry_run, oda, out_version)
            success = not self._is_cancelled()  # 中途被停止时不算完成
            if success:
                total = len(self.scanned_files)
                self._emit(f"==== 全部完成：{total} 个文件，输出见 {out} ====")
            return success
        finally:
            if work_in:
                shutil.rmtree(work_in, ignore_errors=True)

    def _run_batch(self, inp: str, out: str, rules: list[ReplaceRule],
                   dry_run: bool, oda: str, out_version: str) -> None:
        out_dir = Path(out)
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)

        dxf_files = [Path(p) for p in self.scanned_files if p.lower().endswith(".dxf")]
        dwg_files = [Path(p) for p in self.scanned_files if p.lower().endswith(".dwg")]
        done = 0
        total_ok, total_fail, total_replaced = 0, 0, 0

        # ---- DXF 直接处理 ----
        for src in dxf_files:
            if self._is_cancelled():
                self._emit("已停止。")
                return
            dst = out_dir / src.name
            res = process_dxf_file(src, dst, rules, dry_run=dry_run)
            done += 1
            self._progress(done, total_ok, total_fail, total_replaced)
            if res.status == "ok":
                total_ok += 1
                total_replaced += res.replaced_total
                verb = "预览命中" if dry_run else "处理完成"
                self._emit(f"[DXF] {src.name}: {verb}，替换 {res.replaced_total} 处 "
                           f"({', '.join(f'{k}:{v}' for k, v in res.per_type.items())})")
            else:
                total_fail += 1
                self._emit(f"[DXF] {src.name}: 错误 - {res.error}")

        # ---- DWG 经 ODA 转换处理 ----
        if dwg_files and not self._is_cancelled():
            if dry_run:
                for src in dwg_files:
                    done += 1
                    self._progress(done, total_ok, total_fail, total_replaced)
                    self._emit(f"[DWG] {src.name}: dry-run 跳过（DWG 需 ODA 转换，dry-run 不执行）")
                total_fail += len(dwg_files)
                return
            work = tempfile.mkdtemp(prefix="cad_text_tool_")
            try:
                mid1 = Path(work) / "dxf_from_dwg"
                mid2 = Path(work) / "dxf_processed"
                mid1.mkdir()
                mid2.mkdir()
                dwg_names = [p.name for p in dwg_files]
                self._emit(f"正在用 ODA 转换 {len(dwg_files)} 个 DWG → DXF ...")
                convert_dwg_batch_to_dxf(oda, inp, mid1, dwg_names, out_version)
                for f in sorted(mid1.glob("*.dxf")):
                    if self._is_cancelled():
                        self._emit("已停止。")
                        return
                    res = process_dxf_file(f, mid2 / f.name, rules)
                    done += 1
                    total_replaced += res.replaced_total
                    self._progress(done, total_ok, total_fail, total_replaced)
                    if res.status == "ok":
                        total_ok += 1
                        self._emit(f"[DWG] {f.stem}.dwg: 转换+替换完成，替换 {res.replaced_total} 处 "
                                   f"({', '.join(f'{k}:{v}' for k, v in res.per_type.items())})")
                    else:
                        total_fail += 1
                        self._emit(f"[DWG] {f.stem}.dwg: 错误 - {res.error}")
                if not self._is_cancelled() and total_fail == 0:
                    self._emit("正在用 ODA 转换处理后的 DXF → DWG ...")
                    convert_dxf_batch_to_dwg(oda, mid2, out_dir, [f.name for f in mid1.glob("*.dxf")], out_version)
            except ODAError as ex:
                total_fail += len(dwg_files)
                self._emit(f"[ODA] 转换失败：{ex}")
            except Exception as ex:  # noqa: BLE001
                total_fail += len(dwg_files)
                self._emit(f"[DWG] 处理失败：{ex}")
            finally:
                shutil.rmtree(work, ignore_errors=True)

        self._emit(f"---- 汇总：成功 {total_ok}，失败 {total_fail}，替换 {total_replaced} 处 ----")
        if dry_run:
            self._emit("（dry-run 模式：未写入任何文件）")

    def _progress(self, done: int, ok: int, fail: int, replaced: int) -> None:
        self._emit("", done)

    def _on_finish(self, success: bool) -> None:
        """完成收尾：恢复按钮并弹窗汇总。"""
        super()._on_finish(success)
        finish_popup(success)



