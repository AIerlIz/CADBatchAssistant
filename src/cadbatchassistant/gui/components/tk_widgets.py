"""GUI 通用控件构建与 ODA / 模板库弹窗包装。

- 通用控件：build_file_list / popup_list_menu / build_log_panel / build_output_row
- ODA 助手：check_oda / browse_oda / build_oda_row
- 模板库弹窗包装：upload_template_file / delete_template_file / edit_template_file
  （纯文件操作在 core.templates，此处只做对话框与提示）
"""

from __future__ import annotations

import contextlib
import os
import tkinter as tk
from tkinter import ttk
from typing import Literal

from cadbatchassistant.core.common.filetypes import CAD_SUFFIXES
from cadbatchassistant.core.common.templates import (
    TEMPLATE_EDIT_COLUMNS,
    coerce_edit_value,
    editable_rows,
    load_template_json,
    merge_editable_rows,
    remove_template,
    save_template_json,
    templates_dir,
)
from cadbatchassistant.core.dwg_converter import get_converter
from cadbatchassistant.gui.components.tk_util import Tooltip, center_window

# ---------------- ODA 选项助手 ----------------


# ODA 状态枚举：短文案（同行显示）/ 前景色 / tooltip 明细文案
_ODA_STATES = {
    # key -> (short, color, detail)
    "found": (
        "● 已验证",
        "#2e7d32",
        "已检测到 ODAFileConverter，路径有效。",
    ),
    "missing": (
        "● 未检测",
        "#e65100",
        "未检测到 ODAFileConverter（处理 DWG 需要；纯 DXF 无需）。"
        "可点击「浏览」手动选择 ODAFileConverter.exe。",
    ),
    "invalid": (
        "● 路径无效",
        "#c62828",
        "配置的路径不是有效的 ODAFileConverter.exe，请点击「浏览」重新选择。",
    ),
    "chosen": (
        "● 已指定",
        "#1565c0",
        "已手动指定 ODAFileConverter 路径。",
    ),
    "probing": ("● 检测中", "#757575", "正在探测 ODAFileConverter..."),
}


class OdaStatusView:
    """ODA 状态展示（单行内）：短文案 + 颜色 + 悬停明细 tooltip。

    set_state(key) 按 _ODA_STATES 更新可见短文案/颜色与 tooltip 明细；
    状态列固定宽、不伸展，不挤压输入框（严格单行、不换行）。
    """

    def __init__(self, parent, var_out: tk.StringVar, width: int = 12) -> None:
        self._var_out = var_out
        self._label = ttk.Label(
            parent,
            textvariable=var_out,
            width=width,
            anchor="w",
        )
        self._label.grid(row=0, column=3, sticky="e", padx=4)
        self._tip = Tooltip(self._label)

    def set_state(self, key: str) -> None:
        short, color, detail = _ODA_STATES.get(key, ("● ?", "#666", ""))
        self._var_out.set(short)
        self._label.configure(foreground=color)
        self._tip.set_text(detail)


def check_oda(
    var_oda,
    var_info,
    hint: str = "未检测到（处理 DWG 需要；纯 DXF 无需）",
    view: OdaStatusView | None = None,
) -> None:
    """探测 ODAFileConverter 并刷新状态显示。

    var_oda  : 路径输入框的 StringVar
    var_info : 状态 StringVar（保留兼容写入）
    view     : OdaStatusView（推荐）；提供时按多状态更新（颜色/tooltip）
    软件启动（设置页构建）时自动执行：
    - 未配置（空）或配置路径已失效 → 自动填入探测结果
    - 已配置且有效 → 保留用户路径
    - 探测不到 → 保留当前值
    """
    found = get_converter().find()
    current = var_oda.get().strip().strip('"\'')
    if found:
        if not current or not os.path.isfile(current):
            var_oda.set(str(found))
        state, msg = "found", "✓ 已检测到"
    else:
        state, msg = "missing", hint
    var_info.set(msg)
    if view is not None:
        view.set_state(state)


def browse_oda(
    var_oda, var_info, view: OdaStatusView | None = None
) -> None:
    """弹出文件对话框选择 ODAFileConverter.exe。"""
    from tkinter import filedialog

    f = filedialog.askopenfilename(
        title="选择 ODAFileConverter.exe",
        filetypes=[
            ("ODAFileConverter", "ODAFileConverter.exe"),
            ("可执行文件", "*.exe"),
        ],
    )
    if f:
        var_oda.set(f)
        var_info.set("已指定")
        if view is not None:
            view.set_state("chosen")


def build_oda_row(
    parent,
    label: str = "ODA File Converter:",
    browse_text: str = "浏览",
    initial: str = "",
) -> tuple[tk.StringVar, tk.StringVar]:
    """在 parent 的 row=0 构建 ODA 路径选择行，返回 (var_oda, var_info)。

    布局（严格单行）：Label | Entry(列1伸展、铺满空白) | 浏览(列2) | 状态(列3,最右)。
    状态为短文案 + 颜色 + 悬停明细 tooltip；浏览紧贴输入框，状态靠最右，
    输入框占满「浏览」之前的全部剩余宽度。
    """
    var_oda = tk.StringVar(value=initial)
    var_info = tk.StringVar()
    view = OdaStatusView(parent, var_info)
    # 记录 view 供调用方 state 更新复用（attach 到返回的 var_info）
    var_info._oda_view = view  # type: ignore[attr-defined]
    ttk.Label(parent, text=label).grid(row=0, column=0, sticky="w")
    ttk.Entry(parent, textvariable=var_oda).grid(
        row=0, column=1, sticky="ew", padx=4
    )
    ttk.Button(
        parent,
        text=browse_text,
        command=lambda: browse_oda(var_oda, var_info, view),
    ).grid(row=0, column=2, padx=4)  # 紧贴输入框
    parent.columnconfigure(1, weight=1)  # 输入框铺满空白；浏览/状态列不伸展
    view.set_state("probing")  # 初始为探测中，等待 check_oda 更新
    return var_oda, var_info


# ---------------- 模板库弹窗包装 ----------------


def upload_template_file(
    category: str,
    src: str | None = None,
    title: str = "上传图纸模板（解析占位符存入模板库）",
) -> tuple[str, str] | None:
    """选择 dwg/dxf 模板，返回 (模板文件名, 源文件完整路径)。

    不把原文件复制进模板库——模板库只保存解析出的占位符配置 JSON，
    由调用方从返回的源路径解析占位符写入 meta。文件非法 / 用户取消 /
    覆盖被拒时返回 None（提示弹窗在此统一处理）。
    """
    from tkinter import filedialog, messagebox

    if src is None:
        src = filedialog.askopenfilename(
            title=title,
            filetypes=[
                ("CAD 文件", "*.dwg *.dxf"),
                ("DWG 文件", "*.dwg"),
                ("DXF 文件", "*.dxf"),
                ("所有文件", "*.*"),
            ],
        )
        if not src:
            return None
    if not (src.lower().endswith(CAD_SUFFIXES) and os.path.isfile(src)):
        messagebox.showwarning("提示", "仅支持上传 .dwg/.dxf 文件")
        return None
    name = os.path.basename(src)
    d = templates_dir(category)
    target = d / (name + ".json")
    if target.exists() and not messagebox.askyesno(
        "覆盖", f"模板库已存在 {name}，是否覆盖？"
    ):
        return None
    return name, src


def delete_template_file(category: str, name: str) -> bool:
    """确认后删除模板库（category 子目录）中的模板；成功返回 True。

    name 为空 / 用户取消 / 删除失败时返回 False（提示弹窗在此统一处理）。
    纯文件删除委托 core.templates.remove_template。
    """
    from tkinter import messagebox

    if not name:
        messagebox.showwarning("提示", "请先选择要删除的模板")
        return False
    if not messagebox.askyesno("确认删除", f"确定删除模板「{name}」吗？"):
        return False
    try:
        remove_template(category, name)
    except OSError as ex:
        messagebox.showerror("删除失败", str(ex))
        return False
    return True


class _TemplateEditDialog:
    """模板占位符编辑对话框（Toplevel）：表格内联编辑 + 添加/删除行。

    columns : list[tuple[key, header, kind]]（见 core.templates.TEMPLATE_EDIT_COLUMNS）
    编辑结果行经 rows() 返回；「保存」在本类内校验并落盘（失败弹错保持
    对话框打开，便于修正）；「取消」丢弃。saved 标记是否成功保存。
    """

    def __init__(self, parent, title: str, columns, rows: list[dict]) -> None:
        self._parent = parent
        self._columns = columns
        self._rows = [dict(r) for r in rows]
        self._editor: tk.Entry | None = None
        self.saved = False

        self._win = tk.Toplevel(parent)
        self._win.title(title)
        self._win.transient(parent.winfo_toplevel())
        self._win.resizable(True, True)

        body = ttk.Frame(self._win, padding=8)
        body.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(
            body,
            columns=[str(i) for i in range(len(columns))],
            show="headings",
            height=10,
        )
        for i, (_key, header, _kind) in enumerate(columns):
            self._tree.heading(str(i), text=header, anchor="w")
            self._tree.column(str(i), width=110, anchor="w")
        scroll = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        self._tree.config(yscrollcommand=scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._tree.bind("<Double-1>", self._on_double)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._menu = tk.Menu(body, tearoff=0)
        self._menu.add_command(label="删除选中行", command=self._delete_selected)

        desc = ttk.Label(
            self._win,
            text="双击单元格编辑；「是/否」列填 是/否/1/0/true/false。",
            padding=(8, 0, 8, 4),
        )
        desc.pack(fill="x")

        bar = ttk.Frame(self._win, padding=(8, 0, 8, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="添加行", command=self._add_row).pack(side="left")
        ttk.Button(bar, text="删除选中行", command=self._delete_selected).pack(
            side="left", padx=6
        )
        ttk.Button(bar, text="保存", command=self._save).pack(side="right")
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right", padx=6)

        self._refresh()
        center_window(self._win, parent.winfo_toplevel())

    # ---------------- 行读写 ----------------
    def rows(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def _refresh(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for idx, row in enumerate(self._rows):
            values = tuple(
                self._display(kind, row.get(key, ""))
                for key, _h, kind in self._columns
            )
            self._tree.insert("", "end", iid=str(idx), values=values)

    @staticmethod
    def _display(kind: str, value) -> str:
        if kind == "bool":
            return "是" if value else "否"
        return "" if value is None else str(value)

    def _parse(self, kind: str, text: str):
        """把编辑框文本按类型解析为待保存值（委托 core 单一实现，避免两处漂移）。

        非法数值/枚举抛 ValueError（由保存路径统一弹错）。
        """
        return coerce_edit_value(kind, text)

    # ---------------- 内联编辑 ----------------
    def _on_double(self, event) -> None:
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self._tree.identify_row(event.y)
        col_comp = self._tree.identify_column(event.x)
        if not iid or not col_comp.startswith("#"):
            return
        try:
            idx = int(iid)
            col = int(col_comp[1:]) - 1
        except ValueError:
            return
        if not (0 <= col < len(self._columns)):
            return
        self._start_edit(idx, col)

    def _start_edit(self, idx: int, col: int) -> None:
        self._cancel_edit()
        iid = str(idx)
        box = self._tree.bbox(iid, str(col))
        if not box:
            return
        x, y, w, h = box
        _key, _h, kind = self._columns[col]
        var = tk.StringVar(value=self._display(kind, self._rows[idx].get(_key, "")))
        ed = tk.Entry(self._tree, textvariable=var, borderwidth=1, relief="solid")
        ed.place(x=x, y=y, width=w, height=h)
        self._editor, self._edit_idx, self._edit_col = ed, idx, col
        ed.focus_set()
        ed.select_range(0, "end")
        ed.bind("<Return>", lambda _e: self._commit())
        ed.bind("<Escape>", lambda _e: self._cancel_edit())
        ed.bind("<FocusOut>", lambda _e: self._commit())

    def _commit(self) -> None:
        if self._editor is None:
            return
        ed, idx, col = self._editor, self._edit_idx, self._edit_col
        text = ed.get()
        self._cancel_edit()
        _key, _h, kind = self._columns[col]
        # 非法数值保留原值，保存时统一校验
        with contextlib.suppress(ValueError):
            self._rows[idx][_key] = self._parse(kind, text)
        self._refresh()

    def _cancel_edit(self) -> None:
        if self._editor is not None:
            self._editor.destroy()
            self._editor = None

    # ---------------- 行增删 ----------------
    def _add_row(self) -> None:
        self._rows.append(
            {
                key: (
                    False
                    if kind == "bool"
                    else 0
                    if kind in ("int", "float")
                    else ""
                )
                for key, _h, kind in self._columns
            }
        )
        self._refresh()

    def _delete_selected(self) -> None:
        for iid in sorted(self._tree.selection(), reverse=True):
            try:
                idx = int(iid)
            except ValueError:
                continue
            if 0 <= idx < len(self._rows):
                del self._rows[idx]
        self._refresh()

    def _on_right_click(self, event) -> str:
        iid = self._tree.identify_row(event.y)
        if iid:
            if iid not in self._tree.selection():
                self._tree.selection_set(iid)
            self._menu.tk_popup(event.x_root, event.y_root)
        return "break"

    # ---------------- 保存 / 取消 ----------------
    def _save(self) -> None:
        # 先做一次类型校验（把编辑框仍为字符串的列解析）
        from tkinter import messagebox

        try:
            for _col, (key, _h, kind) in enumerate(self._columns):
                for row in self._rows:
                    if kind in ("float", "int") and isinstance(row.get(key), str):
                        row[key] = self._parse(kind, row[key])
            payload_cb = getattr(self, "_on_save", None)
            if payload_cb is not None:
                payload_cb(self.rows())
        except ValueError as ex:
            messagebox.showwarning("编辑模板", str(ex))
            return
        self.saved = True
        self._win.destroy()

    def _cancel(self) -> None:
        self._win.destroy()

    def run(self) -> bool:
        self._parent.winfo_toplevel().wait_window(self._win)
        return self.saved


def edit_template_file(
    category: str, name: str, parent=None, on_save=None
) -> bool:
    """打开模板占位符编辑对话框；保存成功返回 True，取消/失败返回 False。

    category : 模板库分类（"fill" / "catalog"）
    name     : 模板名（下拉选中项）
    parent   : 宿主窗口（None 用默认根）；用于弹窗相对居中
    on_save  : 可选回调 on_save(payload)，在落盘前对合并后的 payload 做联动
        修正（fields 已由 core.merge_editable_rows 自动重建，一般不必提供）。

    编辑损坏 → 弹错并返回 False；保存时的类型错误弹错（对话框保持打开）。
    """
    from tkinter import messagebox

    if not name:
        messagebox.showwarning("提示", "请先选择要编辑的模板")
        return False
    data = load_template_json(category, name)
    if data is None:
        messagebox.showerror(
            "编辑模板",
            f"模板「{name}」配置缺失或损坏，无法编辑（请删除后重新上传）",
        )
        return False
    columns = TEMPLATE_EDIT_COLUMNS.get(category, [])
    if not columns:
        messagebox.showerror("编辑模板", f"不支持的模板分类：{category}")
        return False
    rows = editable_rows(category, data)
    parent = parent or tk._default_root  # type: ignore[attr-defined]
    if parent is None:
        messagebox.showerror("编辑模板", "无可用窗口")
        return False

    dlg = _TemplateEditDialog(parent, f"编辑模板「{name}」", columns, rows)

    def _do_save(edited_rows: list[dict]) -> None:
        payload = merge_editable_rows(category, data, edited_rows)
        if on_save is not None:
            on_save(payload)
        save_template_json(category, name, payload)

    dlg._on_save = _do_save  # type: ignore[attr-defined]
    try:
        return dlg.run()
    except tk.TclError:
        return False


# ---------------- 通用控件构建 ----------------


def build_file_list(
    parent,
    height: int = 6,
    on_delete=None,
    delete_text: str = "删除选中文件",
    fill: Literal["none", "x", "y", "both"] = "both",
    width: int | None = None,
) -> tuple[tk.Listbox, tk.Menu]:
    """构建多选文件列表（Listbox + 滚动条 + 右键删除菜单），返回 (listbox, menu)。

    右键菜单由 popup_list_menu 统一处理；调用方如需 Delete 键/拖放支持，
    对返回的 listbox 另行绑定。
    """
    listbox = tk.Listbox(
        parent,
        height=height,
        selectmode="extended",
        width=width if width is not None else 20,
    )
    listbox.pack(side="left", fill=fill, expand=True)
    scroll = ttk.Scrollbar(parent, orient="vertical", command=listbox.yview)
    scroll.pack(side="right", fill="y")
    listbox.config(yscrollcommand=scroll.set)
    menu = tk.Menu(parent, tearoff=0)
    if on_delete is not None:
        menu.add_command(label=delete_text, command=on_delete)
    listbox.bind("<Button-3>", lambda e: popup_list_menu(e, listbox, menu))
    return listbox, menu


def popup_list_menu(event, listbox: tk.Listbox, menu: tk.Menu) -> str:
    """右键点击列表：选中点击行并弹出菜单。"""
    idx = listbox.nearest(event.y)
    if idx >= 0:
        if idx not in listbox.curselection():
            listbox.selection_clear(0, "end")
            listbox.selection_set(idx)
        menu.tk_popup(event.x_root, event.y_root)
    return "break"


def build_log_panel(
    parent, height: int = 8, title: str = "日志"
) -> tuple[ttk.LabelFrame, tk.Text]:
    """构建日志面板（LabelFrame + Text + 滚动条），返回 (frame, log_text)。

    frame 由调用方 pack（如 fill="both", expand=True）。
    """
    frame = ttk.LabelFrame(parent, text=title, padding=8)
    log_text = tk.Text(frame, height=height, wrap="word")
    log_scroll = ttk.Scrollbar(frame, orient="vertical", command=log_text.yview)
    log_text.config(yscrollcommand=log_scroll.set)
    log_text.pack(side="left", fill="both", expand=True)
    log_scroll.pack(side="right", fill="y")
    return frame, log_text


def build_output_row(
    parent, var, on_browse, on_default, browse_text: str = "浏览", entry_hook=None
) -> tk.Entry:
    """构建输出目录行（Label + Entry + 浏览 + 默认），返回 entry。

    entry_hook : 可选回调 entry_hook(entry)，用于附加拖放等扩展绑定。
    """
    ttk.Label(parent, text="输出目录:").grid(row=0, column=0, sticky="w")
    entry = ttk.Entry(parent, textvariable=var)
    entry.grid(row=0, column=1, sticky="ew", padx=4)
    if entry_hook is not None:
        entry_hook(entry)
    ttk.Button(parent, text=browse_text, command=on_browse).grid(
        row=0, column=2, padx=4
    )
    ttk.Button(parent, text="默认", command=on_default).grid(row=0, column=3, padx=4)
    parent.columnconfigure(1, weight=1)
    return entry
