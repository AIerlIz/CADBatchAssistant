"""GUI 通用控件构建与 ODA / 模板库弹窗包装。

- 通用控件：build_file_list / popup_list_menu / build_log_panel / build_output_row
- ODA 助手：check_oda / browse_oda / build_oda_row
- 模板库弹窗包装：upload_template_file / delete_template_file
  （纯文件操作在 core.templates，此处只做对话框与提示）
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Literal

from cadbatchassistant.core.common.filetypes import CAD_SUFFIXES
from cadbatchassistant.core.common.templates import (
    remove_template,
    templates_dir,
)
from cadbatchassistant.core.dwg_converter import get_converter
from cadbatchassistant.gui.components.tk_util import Tooltip

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
