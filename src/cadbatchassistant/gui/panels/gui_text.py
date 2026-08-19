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

from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.common.dwg_workflow import run_dwg_roundtrip_chunks
from cadbatchassistant.core.common.input_files import check_duplicate_names
from cadbatchassistant.core.common.parallel import TaskFailed, map_files
from cadbatchassistant.core.common.text_replace import ReplaceRule, process_dxf_file
from cadbatchassistant.gui.components.async_panel import AsyncPanel
from cadbatchassistant.gui.mixins.gui_shared import (
    FilesPanelMixin,
    PanelLayoutMixin,
    RunStartMixin,
    get_app_runtime_config,
    warn_require,
)


def _text_task(item: tuple):
    """并行 worker：解包 (src, dst, rules, dry_run) 执行 process_dxf_file。

    顶层函数（Windows spawn 可 pickle）；单文件异常由 map_files 包装为
    TaskFailed，调用方统一容错。
    """
    src, dst, rules, dry_run = item
    return process_dxf_file(src, dst, rules, dry_run=dry_run)


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
        self._edit_iid: str = ""
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


class CadTextApp(FilesPanelMixin, PanelLayoutMixin, RunStartMixin, AsyncPanel):
    def __init__(self, parent: tk.Widget) -> None:
        """构建「改字助手」面板；parent 为嵌入容器（如 Notebook 的 tab 页）。"""
        super().__init__(parent)
        self.scanned_files: list[str] = []
        self.rules_data: list[tuple[str, str]] = []

        self._build_ui()
        self._refresh_rule_list()  # 渲染表格与底部「添加规则」行

    # ---------------- UI 构建 ----------------
    def _build_ui(self) -> None:
        self._main = self._build_root()
        self._add_input_section()

        # 2. 规则区（表格内直接增删改）
        rule_frame = ttk.LabelFrame(
            self._main, text="替换规则（按顺序执行）", padding=8
        )
        rule_frame.pack(fill="x", **self._pad)

        rule_tree_frame = ttk.Frame(rule_frame)
        rule_tree_frame.pack(fill="x")
        self.rule_tree = EditableTreeview(
            rule_tree_frame,
            self,
            columns=("find", "replace"),
            show="headings",
            height=5,
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
        opt_frame = ttk.LabelFrame(self._main, text="选项", padding=8)
        opt_frame.pack(fill="x", **self._pad)

        self.var_case = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="大小写敏感", variable=self.var_case).grid(
            row=0, column=0, sticky="w"
        )
        self.var_regex = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt_frame, text="正则模式", variable=self.var_regex).grid(
            row=0, column=1, sticky="w", padx=8
        )
        self.var_dry = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opt_frame, text="dry-run（只统计不写文件）", variable=self.var_dry
        ).grid(row=0, column=2, sticky="w", padx=8)
        opt_frame.columnconfigure(2, weight=1)

        # 4. 输出区
        self.var_out = tk.StringVar()
        self._add_output_section(self.var_out)

        # 5. 运行区
        self._add_run_section()

        # 6. 日志区
        self._add_log_section()

    # ---------------- 规则管理（表格内联增删改） ----------------
    def _refresh_rule_list(self) -> None:
        self.rule_tree.delete(*self.rule_tree.get_children())
        for i, (find, replace) in enumerate(self.rules_data):
            self.rule_tree.insert("", "end", iid=str(i), values=(find, replace))
        self.rule_tree.insert("", "end", iid="__add__", values=("＋ 点击添加规则", ""))

    def _rules(self) -> list[ReplaceRule]:
        return [
            ReplaceRule(
                find=find,
                replace=replace,
                case_sensitive=self.var_case.get(),
                regex=self.var_regex.get(),
            )
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
    def _run_maximum(self) -> int | None:
        """进度条上限 = 待处理文件数（改字助手按文件推进）。"""
        return len(self.scanned_files)

    def _prepare_run(self) -> tuple | None:
        """校验输入并准备 worker 参数；校验/复制失败弹窗提示并返回 None。

        返回 (inp, out, rules, dry_run, oda, out_version, work_in, snapshot)，
        与 _work 签名一致。复制输入文件到临时目录在此完成（begin_run 之前），
        失败时面板未进入运行态，无需复位。
        """
        rules = self._rules()
        if not warn_require(bool(rules), "请至少添加一条替换规则"):
            return None
        if not warn_require(bool(self.scanned_files), "请选择要处理的 DWG/DXF 文件"):
            return None
        out = self.var_out.get().strip()
        if not out:
            self._default_output()
            out = self.var_out.get().strip()
        if not warn_require(bool(out), "请设置输出目录"):
            return None
        oda, out_version = get_app_runtime_config()
        has_dwg = any(p.lower().endswith(".dwg") for p in self.scanned_files)
        if has_dwg and not self.var_dry.get() and not warn_require(
            dc.get_converter().require_for_dwg(True, oda) is None,
            "缺少 ODA File Converter，请安装或在「设置」页配置其路径",
            title="缺少 ODA File Converter",
        ):
            return None

        # 快照文件列表：后台线程只读快照，避免运行期间主线程
        # 增删文件（_delete_selected_files 等）导致迭代竞态/IndexError
        snapshot = list(self.scanned_files)

        # 重名检测：跨目录同名文件复制到临时输入目录会互相覆盖（丢图），
        # 输出阶段也会写同一路径；与 fill/catalog 流水线一致，直接拒绝。
        try:
            check_duplicate_names(snapshot)
        except ValueError as ex:
            messagebox.showerror("输入文件重名", str(ex))
            return None

        # 文件模式下：仅 DWG 需要把所选文件复制到临时输入目录（ODA 转换要求
        # 输入为目录）；纯 DXF 批直接读原路径处理（_run_batch 的 dxf_tasks 用
        # 原始路径），跳过复制避免整批白拷贝。复制在 begin_run 之前执行
        # （失败即不启动，无需复位运行态）。
        work_in = None
        inp = ""
        need_stage = (not self.var_dry.get()) and any(
            p.lower().endswith(".dwg") for p in snapshot
        )
        try:
            if need_stage:
                work_in = tempfile.mkdtemp(prefix="cad_text_input_")
                # 仅复制 DWG：DXF 直接读原路径处理（_run_batch 的 dxf_tasks
                # 用原始路径；DWG 分块经 run_dwg_roundtrip_chunks 转换）
                for p in snapshot:
                    if p.lower().endswith(".dwg"):
                        shutil.copy2(str(p), os.path.join(work_in, os.path.basename(p)))
                inp = work_in
        except Exception as ex:  # noqa: BLE001 - 复制失败（如文件被占用）不启动
            if work_in:
                shutil.rmtree(work_in, ignore_errors=True)
            self._emit(f"复制输入文件失败：{ex}")
            self._emit("处理未开始（输入文件复制失败），请检查文件是否被占用后重试。")
            return None

        return (
            inp,
            out,
            rules,
            self.var_dry.get(),
            oda,
            out_version,
            work_in,
            snapshot,
        )

    # ---------------- 批处理（后台线程） ----------------
    def _work(
        self,
        inp: str,
        out: str,
        rules: list[ReplaceRule],
        dry_run: bool,
        oda: str,
        out_version: str,
        work_in: str | None = None,
        files_snapshot: list[str] | None = None,
    ) -> bool:
        try:
            self._run_batch(inp, out, rules, dry_run, oda, out_version, files_snapshot)
            success = not self._is_cancelled()  # 中途被停止时不算完成
            if success:
                total = len(files_snapshot or self.scanned_files)
                self._emit(f"==== 全部完成：{total} 个文件，输出见 {out} ====")
            return success
        finally:
            if work_in:
                shutil.rmtree(work_in, ignore_errors=True)

    def _run_batch(
        self,
        inp: str,
        out: str,
        rules: list[ReplaceRule],
        dry_run: bool,
        oda: str,
        out_version: str,
        files_snapshot: list[str] | None = None,
    ) -> None:
        out_dir = Path(out)
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)

        # 后台线程只读快照，不读 self.scanned_files（主线程可能并发增删）
        files = list(
            files_snapshot if files_snapshot is not None else self.scanned_files
        )
        dxf_files = [Path(p) for p in files if p.lower().endswith(".dxf")]
        dwg_files = [Path(p) for p in files if p.lower().endswith(".dwg")]
        done = 0
        total_ok, total_fail, total_replaced, total_skipped = 0, 0, 0, 0

        # ---- DXF 直接处理（多进程并行） ----
        dxf_tasks = [(src, out_dir / src.name, rules, dry_run) for src in dxf_files]

        def _on_dxf_done(result, _idx, item) -> None:
            nonlocal done, total_ok, total_fail, total_replaced
            src = item[0]
            done += 1
            if isinstance(result, TaskFailed):
                total_fail += 1
                self._emit(f"[DXF] {src.name}: 错误 - {result.error}")
            else:
                total_ok += 1
                total_replaced += result.replaced_total
                verb = "预览命中" if dry_run else "处理完成"
                self._emit(
                    f"[DXF] {src.name}: {verb}，替换 {result.replaced_total} 处 "
                    f"({', '.join(f'{k}:{v}' for k, v in result.per_type.items())})"
                )
            self._progress(done)

        map_files(
            _text_task,
            dxf_tasks,
            is_cancelled=self._is_cancelled,
            on_done=_on_dxf_done,
        )
        if self._is_cancelled():
            self._emit("已停止。")
            return

        # ---- DWG 经 ODA 转换处理 ----
        if dwg_files and not self._is_cancelled():
            if dry_run:
                # dry-run 不执行 ODA 转换：DWG 既不算成功也不算失败，计入跳过
                # （此前计为失败且提前 return 丢失汇总输出，误导用户）
                total_skipped += len(dwg_files)
                for src in dwg_files:
                    done += 1
                    self._progress(done)
                    self._emit(
                        f"[DWG] {src.name}: dry-run 跳过"
                        "（DWG 需 ODA 转换，dry-run 不执行）"
                    )
            else:
                converter = dc.get_converter()
                work = tempfile.mkdtemp(prefix="cad_text_tool_")
                try:
                    dwg_stems = [p.stem for p in dwg_files]
                    chunks_dir = Path(work) / "chunks"

                    def _process_dwg_chunk(
                        before_dir, filled_dir, stems
                    ) -> tuple[list[str], list[str]]:
                        """处理一个分块（并行替换），返回 (failed, skipped)。

                        failed 含「无转换产物」与「替换失败」的图纸：helper
                        据此跳过写回（否则 ODA 转回会因缺产物挂起等待）。
                        """
                        nonlocal done, total_ok, total_fail, total_replaced
                        tasks: list[tuple] = []
                        failed_here: list[str] = []
                        for s in stems:
                            src = Path(before_dir) / f"{s}.dxf"
                            if not src.is_file():
                                total_fail += 1
                                failed_here.append(s)
                                self._emit(f"[DWG] {s}.dwg: 转换失败（无产物）")
                                continue
                            tasks.append(
                                (
                                    str(src),
                                    str(Path(filled_dir) / f"{s}.dxf"),
                                    rules,
                                    False,
                                )
                            )

                        def _on_dwg_done(result, _idx, item) -> None:
                            nonlocal done, total_ok, total_fail, total_replaced
                            f = Path(item[0])
                            done += 1
                            if isinstance(result, TaskFailed):
                                total_fail += 1
                                failed_here.append(f.stem)  # 替换失败：不写回
                                self._emit(f"[DWG] {f.stem}.dwg: 错误 - {result.error}")
                            else:
                                total_ok += 1
                                total_replaced += result.replaced_total
                                per_type = ", ".join(
                                    f"{k}:{v}" for k, v in result.per_type.items()
                                )
                                self._emit(
                                    f"[DWG] {f.stem}.dwg: 转换+替换完成，"
                                    f"替换 {result.replaced_total} 处 ({per_type})"
                                )
                            self._progress(done)

                        map_files(
                            _text_task,
                            tasks,
                            is_cancelled=self._is_cancelled,
                            on_done=_on_dwg_done,
                        )
                        return failed_here, []

                    # DWG 分块「转换→替换→转回」+ 块间转换重叠（ODA 与进程池并行）
                    run_dwg_roundtrip_chunks(
                        converter,
                        oda,
                        inp,
                        out_dir,
                        dwg_stems,
                        out_version,
                        process_batch=_process_dwg_chunk,
                        emit=self._emit,
                        cancel=self._cancel_event,
                        workdir=chunks_dir,
                    )
                    if self._is_cancelled():
                        self._emit("已停止。（已写回的分块保留在输出目录）")
                        return
                except dc.ODAError as ex:
                    total_fail += len(dwg_files)
                    self._emit(f"[ODA] 转换失败：{ex}")
                except Exception as ex:  # noqa: BLE001
                    total_fail += len(dwg_files)
                    self._emit(f"[DWG] 处理失败：{ex}")
                finally:
                    shutil.rmtree(work, ignore_errors=True)

        summary = f"---- 汇总：成功 {total_ok}，失败 {total_fail}，"
        if total_skipped:
            summary += f"跳过 {total_skipped}，"
        summary += f"替换 {total_replaced} 处 ----"
        self._emit(summary)
        if dry_run:
            self._emit("（dry-run 模式：未写入任何文件）")

    def _progress(self, done: int) -> None:
        self._emit("", done)
