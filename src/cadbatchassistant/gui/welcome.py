"""「首次使用引导」多步向导窗口。

首次启动（或用户在设置页手动打开）时展示 5 步引导向导：
欢迎 → 改字助手 → 填表助手 → 目录助手 → 设置与 ODA。
每步可「上一步 / 下一步」翻页，最后一步「完成使用」收尾；
任意路径关闭（按钮或窗口 X）统一走 _close() 写入 welcome_seen
标记，避免每次启动重复弹出。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cadbatchassistant import __version__
from cadbatchassistant.common import (
    WELCOME_SEEN_KEY,
    center_window,
    mark_welcome_seen,
    resource_path,
    save_app_config,
)

# 向导页面数据：(标题, [(行类型, 文本), ...])
# 行类型：head=小节标题（欢迎页首行为大标题）, body=正文/步骤, hint=灰色提示
_PAGES: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "欢迎",
        [
            ("head", f"欢迎使用 CAD批处理助手 v{__version__}"),
            ("body", "一个窗口完成图纸批处理：批量改字、数据表填图、图纸目录生成。"),
            ("body", "接下来分 4 步带您了解三个功能页的操作流程与必要设置，"
                     "点击「下一步」继续，随时可关闭本引导。"),
        ],
    ),
    (
        "改字助手",
        [
            ("head", "改字助手 —— 批量修改 DWG/DXF 图纸文字"),
            ("body", "1. 选择 DWG/DXF 图纸（可拖放追加）"),
            ("body", "2. 编辑查找/替换规则：双击单元格编辑，「＋」添加，Delete/右键删除"),
            ("body", "3. 设置输出目录"),
            ("body", "4. 点击「开始处理」"),
            ("hint", "默认「普通文本」按字面匹配；勾选「正则模式」后查找按正则解释，"
                     "替换支持 \\1 反向引用。"),
        ],
    ),
    (
        "填表助手",
        [
            ("head", "填表助手 —— 把数据表填入图纸标题栏"),
            ("body", "1. 选择数据表格（.xlsx/.xls），可选「工作表格」与「匹配列」"
                     "（图纸名列，默认第一列）"),
            ("body", "2. 选择图纸模板：未填图框 + 值格填「[列名]」占位，"
                     "占位列名与数据表表头精确匹配"),
            ("body", "3. 选择图纸（可拖放）"),
            ("body", "4. 设置输出目录，点击「开始处理」"),
            ("hint", "每个占位符「[列名]」只从数据表对应列取值；"
                     "列缺失或值为空时该字段置空。"),
        ],
    ),
    (
        "目录助手",
        [
            ("head", "目录助手 —— 从图纸取值生成目录 Excel"),
            ("body", "1. 选择图纸模板：在取值位置放「[字段名]」文字（如 [图号]），"
                     "可放多个同名候选位"),
            ("body", "2. 选择表格模板（必填）：Excel 表头列名 = 模板字段名 +「页码」"),
            ("body", "3. 选择图纸（可拖放）"),
            ("body", "4. 设置输出目录，点击「开始处理」"),
            ("hint", "每图纸一个条目，无值字段填 NA；页码每文件一页；"
                     "图号只从图纸提取，取不到填 NA。"),
        ],
    ),
    (
        "设置与 ODA",
        [
            ("head", "设置与 ODA 转换"),
            ("body", "处理 DWG 图纸需在「设置」页配置 ODA File Converter 路径；"
                     "纯 DXF 场景无需安装。"),
            ("body", "软件更新：打包版启动后自动检查 GitHub Release，"
                     "有新版本可在「设置」页应用内更新。"),
            ("hint", "三个功能页位于主窗口顶部 tab，可随时切换；本引导也可在"
                     "「设置」页点击「重新显示使用引导」随时回看。"),
        ],
    ),
]


class WelcomeDialog:
    """模态 5 步引导向导：展示欢迎与三大功能操作流程，关闭后标记已见。

    导航：首步仅「下一步」+「跳过引导」；中间步「上一步 / 下一步」；
    末步「完成使用」。任何路径关闭均写入 welcome_seen 标记。
    """

    def __init__(self, parent: tk.Widget) -> None:
        self._parent = parent
        self._root = parent.winfo_toplevel()
        self._win = tk.Toplevel(parent)
        self._win.title(f"欢迎使用 CAD批处理助手 v{__version__}")
        self._win.transient(self._root)
        self._win.resizable(False, False)
        self._win.geometry("520x450")  # 固定尺寸容纳所有页面
        self._win.protocol("WM_DELETE_WINDOW", self._close)
        self._page = 0
        self._build_ui()
        center_window(self._win, self._root)  # 相对主窗口居中
        self._win.grab_set()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        main = ttk.Frame(self._win, padding=16)
        main.pack(fill="both", expand=True)

        self._step_label = ttk.Label(main, text="", font=("", 10))
        self._step_label.pack(anchor="w", pady=(0, 8))

        self._content = ttk.Frame(main)
        self._content.pack(fill="both", expand=True)

        btn_row = ttk.Frame(main)
        btn_row.pack(fill="x", pady=(10, 0))
        self._btn_next = ttk.Button(btn_row, text="下一步", command=self._next)
        self._btn_next.pack(side="right")
        self._btn_prev = ttk.Button(btn_row, text="上一步", command=self._prev)
        self._btn_prev.pack(side="right", padx=(0, 8))
        self._btn_skip = ttk.Button(btn_row, text="跳过引导", command=self._close)
        self._btn_skip.pack(side="left")

        self._render_page()

    def _nav_state(self) -> dict:
        """当前页的导航状态（UI 刷新与测试共用）。"""
        is_last = self._page == len(_PAGES) - 1
        return {
            "page": self._page,
            "total": len(_PAGES),
            "prev_enabled": self._page > 0,
            "next_text": "完成使用" if is_last else "下一步",
            "next_closes": is_last,
            "skip_visible": self._page == 0,
        }

    def _render_page(self) -> None:
        """按当前页渲染内容区并刷新步骤指示与导航按钮。"""
        title, lines = _PAGES[self._page]
        self._step_label.config(
            text=f"第 {self._page + 1} / {len(_PAGES)} 步：{title}")

        for child in self._content.winfo_children():
            child.destroy()

        if self._page == 0:
            try:
                logo = tk.PhotoImage(file=resource_path("assets/logo.png"))
                self._logo = logo.subsample(2)  # 256px → 128px；保持引用防 GC
                ttk.Label(self._content, image=self._logo).pack(pady=(0, 6))
            except tk.TclError:
                pass

        for kind, text in lines:
            kwargs: dict = {"wraplength": 440, "justify": "left", "anchor": "w"}
            if kind == "head":
                if self._page == 0:
                    kwargs.update(font=("", 16, "bold"), justify="center",
                                  anchor="center")
                else:
                    kwargs.update(font=("", 11, "bold"), pady=(2, 4))
                ttk.Label(self._content, text=text, **kwargs).pack(fill="x")
            elif kind == "hint":
                ttk.Label(self._content, text=text, foreground="#555555",
                          **kwargs).pack(fill="x", pady=(4, 0))
            else:  # body
                ttk.Label(self._content, text=text, **kwargs).pack(fill="x", pady=1)

        nav = self._nav_state()
        self._btn_prev.state(
            ["disabled"] if not nav["prev_enabled"] else ["!disabled"])
        self._btn_next.config(
            text=nav["next_text"],
            command=self._close if nav["next_closes"] else self._next)
        if nav["skip_visible"]:
            self._btn_skip.pack(side="left")
        else:
            self._btn_skip.pack_forget()

    # ---------------- 导航 ----------------
    def _next(self) -> None:
        if self._page < len(_PAGES) - 1:
            self._page += 1
            self._render_page()
        else:
            self._close()

    def _prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render_page()

    # ---------------- 关闭 ----------------
    def _close(self) -> None:
        """写入已见标记并关闭窗口（按钮与窗口 X 统一走此路径）。

        对已销毁窗口重复调用时 destroy 会抛 TclError，
        用 winfo_exists 守卫保证幂等。
        """
        mark_welcome_seen()
        if self._win.winfo_exists():
            self._win.destroy()


def reopen_welcome(parent: tk.Widget) -> None:
    """清空已见标记并重新弹出引导向导（设置页「重新显示使用引导」用）。

    清标记仅影响「是否需要自动弹出」的判定；用户关闭引导时
    又会写回 True，因此不会导致下次启动重复自动弹出。
    """
    save_app_config({WELCOME_SEEN_KEY: False})
    WelcomeDialog(parent)
