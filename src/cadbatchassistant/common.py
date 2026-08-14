"""两个功能面板（改字助手 / 填表助手）共享的公共组件。

- OUT_VERSION_CHOICES : DWG 输出版本下拉选项
- load_config / save_config : JSON 配置读写（按配置目录/文件隔离）
- default_font_family : Windows 中文字体选择
- dedup_paths : 路径去重（Windows 路径大小写不敏感）
- apply_vista_theme : 设置 ttk 主题
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from cadbatchassistant.core.dwg_converter import (
    DEFAULT_OUT_VERSION,
    find_oda_converter,
)

# DWG 输出版本下拉选项（两个面板共用）；默认值取自 dwg_converter.DEFAULT_OUT_VERSION，
# 与转换层默认保持一致，避免多处常量发散
_OUT_VERSION_CHOICES = [
    "ACAD2013", "ACAD2010", "ACAD2007", "ACAD2004", "ACAD2000",
]
OUT_VERSION_CHOICES = [DEFAULT_OUT_VERSION] + _OUT_VERSION_CHOICES

# 全局设置（ODA 路径、DWG 输出版本）存放于统一配置目录，「设置」页与两个面板共享
APP_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "CADBatchAssistant"
APP_CONFIG_FILE = APP_CONFIG_DIR / "config.json"


def load_config(config_file: str | Path) -> dict:
    """读取 JSON 配置文件；不存在或损坏时返回空 dict。"""
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置损坏/不存在时返回空
        return {}


def save_config(config_file: str | Path, data: dict) -> None:
    """写入 JSON 配置文件；写失败不抛出（不阻塞使用）。"""
    try:
        Path(config_file).parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001 - 写配置失败不阻塞使用
        pass


# ---------------- 全局配置访问（ODA / DWG 输出版本 / 更新镜像） ----------------


def load_app_config() -> dict:
    """读取全局配置（ODA 路径、DWG 输出版本、更新镜像等），不存在时返回空 dict。"""
    return load_config(APP_CONFIG_FILE)


def save_app_config(updates: dict) -> dict:
    """合并更新全局配置并保存（保留 update_ignore 等其他键），返回新配置。"""
    cfg = load_config(APP_CONFIG_FILE)
    cfg.update(updates)
    save_config(APP_CONFIG_FILE, cfg)
    return cfg


def get_oda() -> str:
    """全局配置中的 ODAFileConverter 路径（未配置时为空串）。"""
    return str(load_app_config().get("oda", "")).strip()


def get_out_version() -> str:
    """全局配置中的 DWG 输出版本（默认 ACAD2018，与 dwg_converter 默认一致）。"""
    return str(load_app_config().get("version", "ACAD2018")).strip() or "ACAD2018"


# ---------------- 软件目录 / 模板库 / 目录助手规则 ----------------

# 目录助手（catalog）规则默认值：软件目录 config.json 的 rules 段可覆盖
DEFAULT_CATALOG_RULES = {
    "point_tolerance": 5,
    "figure_field": "图号",
    "data_rows_per_page": 50,
    "cover_pages": 1,
}


def software_dir() -> Path:
    """软件目录：exe 所在目录（打包运行）或项目根（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def resource_path(name: str) -> str:
    """返回打包进 exe 的资源文件路径（如 "assets/logo.ico"）。

    打包运行时从 PyInstaller 解压目录 sys._MEIPASS 取；源码运行时取项目根。
    供窗口图标等读取随包分发的资源。
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return str(base / name)


def rules_file() -> Path:
    """目录助手规则配置文件：软件目录下的 config.json（可手动编辑）。"""
    return software_dir() / "config.json"


def load_catalog_rules() -> dict:
    """读取目录助手规则（软件目录 config.json 的 rules 段），缺省返回内置默认。"""
    cfg = load_config(rules_file())
    rules = dict(DEFAULT_CATALOG_RULES)
    user_rules = cfg.get("rules")
    if isinstance(user_rules, dict):
        rules.update({k: v for k, v in user_rules.items() if v not in (None, "")})
    return rules


def templates_dir(category: str) -> Path:
    """模板库目录：软件目录/templates/<category>（如 fill / catalog）。"""
    return software_dir() / "templates" / category


def list_templates(category: str) -> list[str]:
    """返回模板库（category 子目录）中的 .dwg/.dxf 模板文件名（排序）。"""
    d = templates_dir(category)
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir()
                  if f.is_file() and f.suffix.lower() in (".dwg", ".dxf"))


def upload_template_file(category: str, src: str | None = None,
                         title: str = "上传图纸模板（复制到模板库）") -> str | None:
    """把 dwg/dxf 复制进模板库（category 子目录），返回模板文件名。

    未传 src 时弹出文件选择框；文件非法 / 用户取消 / 覆盖被拒时返回 None
    （提示弹窗在此统一处理）。
    """
    import shutil
    from tkinter import filedialog, messagebox

    if src is None:
        src = filedialog.askopenfilename(
            title=title,
            filetypes=[("CAD 文件", "*.dwg *.dxf"), ("DWG 文件", "*.dwg"),
                       ("DXF 文件", "*.dxf"), ("所有文件", "*.*")],
        )
        if not src:
            return None
    if not (src.lower().endswith((".dwg", ".dxf")) and os.path.isfile(src)):
        messagebox.showwarning("提示", "仅支持上传 .dwg/.dxf 文件")
        return None
    d = templates_dir(category)
    d.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(src)
    target = d / name
    if target.exists() and os.path.normcase(str(target)) != os.path.normcase(src):
        if not messagebox.askyesno("覆盖", f"模板库已存在 {name}，是否覆盖？"):
            return None
    shutil.copy2(src, target)
    return name


def delete_template_file(category: str, name: str) -> bool:
    """确认后删除模板库（category 子目录）中的模板；成功返回 True。

    name 为空 / 用户取消 / 删除失败时返回 False（提示弹窗在此统一处理）。
    """
    from tkinter import messagebox

    if not name:
        messagebox.showwarning("提示", "请先选择要删除的模板")
        return False
    if not messagebox.askyesno("确认删除", f"确定删除模板「{name}」吗？"):
        return False
    try:
        (templates_dir(category) / name).unlink()
    except OSError as ex:
        messagebox.showerror("删除失败", str(ex))
        return False
    return True


def default_font_family() -> str:
    """Windows 上优先使用微软雅黑，保证中文显示清晰。"""
    try:
        from tkinter import font as tkfont

        installed = set(tkfont.families())
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "SimSun"):
            if name in installed:
                return name
    except Exception:  # noqa: BLE001
        pass
    return "TkDefaultFont"


def dedup_paths(paths) -> list:
    """路径去重（Windows 大小写不敏感），保持原顺序。"""
    seen: set[str] = set()
    out = []
    for p in paths:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def apply_vista_theme(style: tk.ttk.Style | None = None) -> None:
    """尝试使用 vista 主题；不可用时静默回退默认主题。"""
    if style is None:
        style = ttk.Style()
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass


def center_window(win, parent: tk.Widget | None = None) -> None:
    """让窗口相对 parent 居中；parent 为空时相对屏幕居中。

    需在窗口内容布局完成后调用（内部 update_idletasks 取实际尺寸），
    供主窗口（屏幕居中）与各 Toplevel 弹窗（相对主窗口居中）复用。
    """
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    if parent is not None:
        root = parent.winfo_toplevel()
        base_x, base_y = root.winfo_rootx(), root.winfo_rooty()
        base_w, base_h = root.winfo_width(), root.winfo_height()
    else:
        base_x = base_y = 0
        base_w, base_h = win.winfo_screenwidth(), win.winfo_screenheight()
    x = base_x + max(0, (base_w - w) // 2)
    y = base_y + max(0, (base_h - h) // 2)
    win.geometry(f"+{x}+{y}")


# ---------------- ODA 选项助手 ----------------


def check_oda(var_oda, var_info, hint: str = "未检测到（处理 DWG 需要；纯 DXF 无需）") -> None:
    """探测 ODAFileConverter 并刷新选项行显示。

    var_oda  : 路径输入框的 StringVar
    var_info : 状态提示的 StringVar
    hint     : 未检测到时的提示文案（两面板文案不同）
    探测到但输入框已有值（用户手动指定/已保存配置）时不覆盖，仅刷新提示。
    """
    found = find_oda_converter()
    if found:
        if not var_oda.get().strip():
            var_oda.set(str(found))
        var_info.set("✓ 已检测到")
    else:
        var_info.set(hint)


def browse_oda(var_oda, var_info) -> None:
    """弹出文件对话框选择 ODAFileConverter.exe。"""
    from tkinter import filedialog

    f = filedialog.askopenfilename(
        title="选择 ODAFileConverter.exe",
        filetypes=[("ODAFileConverter", "ODAFileConverter.exe"), ("可执行文件", "*.exe")],
    )
    if f:
        var_oda.set(f)
        var_info.set("已指定")


def build_oda_row(parent, label: str = "ODA File Converter:",
                  browse_text: str = "浏览",
                  initial: str = "") -> tuple[tk.StringVar, tk.StringVar]:
    """在 parent 的 row=0 构建 ODA 路径选择行，返回 (var_oda, var_info)。

    布局：Label | Entry(伸展) | 浏览按钮 | 状态提示；浏览与 _check_oda
    由调用方绑定（命令复用 common.browse_oda / common.check_oda）。
    """
    var_oda = tk.StringVar(value=initial)
    var_info = tk.StringVar()
    ttk.Label(parent, text=label).grid(row=0, column=0, sticky="w")
    ttk.Entry(parent, textvariable=var_oda).grid(
        row=0, column=1, sticky="ew", padx=4)
    ttk.Button(parent, text=browse_text,
               command=lambda: browse_oda(var_oda, var_info)).grid(
        row=0, column=2, padx=4)
    ttk.Label(parent, textvariable=var_info).grid(
        row=0, column=3, sticky="w", padx=4)
    return var_oda, var_info


# ---------------- 通用控件构建 ----------------


def build_file_list(parent, height: int = 6, on_delete=None,
                    delete_text: str = "删除选中文件",
                    fill: str = "both",
                    width: int | None = None) -> tuple[tk.Listbox, tk.Menu]:
    """构建多选文件列表（Listbox + 滚动条 + 右键删除菜单），返回 (listbox, menu)。

    右键菜单由 popup_list_menu 统一处理；调用方如需 Delete 键/拖放支持，
    对返回的 listbox 另行绑定。
    """
    listbox = tk.Listbox(parent, height=height, selectmode="extended",
                         width=width if width is not None else 20)
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


def parse_dnd_data(data: str) -> list[str]:
    """解析 tkdnd 拖拽数据为路径列表（优先用 tkdnd 标准 splitlist）。"""
    try:
        r = tk.Tcl()
        return [p for p in r.splitlist(data) if p.strip()]
    except Exception:  # noqa: BLE001 - 回退手写解析
        out: list[str] = []
        i = 0
        while i < len(data):
            if data[i] == "{":
                j = data.find("}", i)
                if j == -1:
                    break
                out.append(data[i + 1:j])
                i = j + 1
            else:
                j = data.find(" ", i)
                if j == -1:
                    out.append(data[i:])
                    break
                out.append(data[i:j])
                i = j + 1
        return [p for p in out if p.strip()]


def build_log_panel(parent, height: int = 8,
                    title: str = "日志") -> tuple[ttk.LabelFrame, tk.Text]:
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


def build_output_row(parent, var, on_browse, on_default,
                     browse_text: str = "浏览",
                     entry_hook=None) -> tk.Entry:
    """构建输出目录行（Label + Entry + 浏览 + 默认），返回 entry。

    entry_hook : 可选回调 entry_hook(entry)，用于附加拖放等扩展绑定。
    """
    ttk.Label(parent, text="输出目录:").grid(row=0, column=0, sticky="w")
    entry = ttk.Entry(parent, textvariable=var)
    entry.grid(row=0, column=1, sticky="ew", padx=4)
    if entry_hook is not None:
        entry_hook(entry)
    ttk.Button(parent, text=browse_text, command=on_browse).grid(
        row=0, column=2, padx=4)
    ttk.Button(parent, text="默认", command=on_default).grid(
        row=0, column=3, padx=4)
    parent.columnconfigure(1, weight=1)
    return entry


# ---------------- 后台任务面板骨架 ----------------


class AsyncPanel:
    """后台任务面板通用骨架：后台线程 + 消息队列 + after 轮询。

    基类在 __init__ 中创建 self._root / self._parent / self.msg_queue /
    self.worker / self.running / self._cancel_event，并启动 100ms 队列轮询
    与 vista 主题。子类职责：
    - 在 _build_ui 中创建 self.log_text（tk.Text）与 self.progress（ttk.Progressbar）
    - 实现 _work(*args) -> bool 后台任务体（工作线程中执行，用 self._emit
      回报；返回 True 表示成功，错误捕获与 sentinel 由基类 _run 统一处理）
    - 启动任务：置 self.running = True、self._cancel_event.clear()、复位按钮，
      然后 self._start_worker(args)
    - 可选覆盖 _on_finish(success) 做完成收尾（默认恢复按钮状态）
    - 统一关闭钩子 _on_close 已由基类提供（置停止标志，不销毁窗口）
    - 任务体内用 self._is_cancelled() 轮询停止请求

    停止语义：self.running（布尔，任务体轮询检查）与 self._cancel_event
    （threading.Event，任务体可 wait 检查），_stop 时同时置位。
    """

    def __init__(self, parent: tk.Widget) -> None:
        self._root = parent.winfo_toplevel()
        self._parent = parent
        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.running = False
        self._cancel_event = threading.Event()
        self._run_seq = 0  # 任务代次：__DONE__ 只响应当前代次，防旧任务复位新任务状态
        apply_vista_theme(ttk.Style())
        self._root.after(100, self._poll_queue)

    # ---- 线程安全的任务汇报 ----
    def _emit(self, msg: str, progress: int | None = None) -> None:
        self.msg_queue.put((msg, progress))

    def _stop_event(self) -> threading.Event:
        return self._cancel_event

    def _start_worker(self, args: tuple) -> None:
        """在后台线程运行 self._run(*args)；分配新任务代次。"""
        self._run_seq += 1
        seq = self._run_seq
        self.worker = threading.Thread(
            target=self._run, args=(seq, *args), daemon=True)
        self.worker.start()

    def _run(self, seq: int, *args) -> None:
        """后台线程模板：统一 try/except/finally 与 __DONE__ sentinel 收尾。

        子类只需实现 _work(*args) -> bool（True 表示成功），错误捕获、
        日志提示与 sentinel 上报由本方法统一处理，避免各面板重复样板。
        seq 为任务代次，__DONE__ 消息携带它；主线程只响应当前代次，
        旧任务（停止后残留）的 __DONE__ 不会复位新任务的 UI 状态。
        """
        success = False
        try:
            success = bool(self._work(*args))
        except Exception as ex:  # noqa: BLE001 - 意外异常统一按失败处理
            self._emit(f"处理中断：{ex}")
        finally:
            self.msg_queue.put(("__DONE__", success, seq))

    def _is_cancelled(self) -> bool:
        """是否已请求停止（供任务体内轮询检查）。"""
        return self._cancel_event.is_set()

    # ---- 主线程轮询（每 100ms 冲刷队列） ----
    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.msg_queue.get_nowait()
                if item[0] == "__DONE__":
                    # 只响应当前代次的完成：停止后残留的旧任务 __DONE__
                    # 不复位新任务状态（否则旧收尾会覆盖新任务的按钮/进度）
                    if len(item) >= 3 and item[2] == self._run_seq:
                        self._on_finish(item[1])
                    break  # 处理完本轮，仍会走到末尾重调度，支持多轮批处理
                msg, progress = item
                if msg:
                    self.log_text.insert("end", msg + "\n")
                    self.log_text.see("end")
                if progress is not None:
                    self.progress.config(value=progress)
        except queue.Empty:
            pass
        self._root.after(100, self._poll_queue)

    # ---- 停止 ----
    def _stop(self) -> None:
        """请求停止：置停止标志，当前文件处理完后退出循环。

        同时禁用「开始处理」按钮，直到本任务真正结束（__DONE__ 到达、
        _on_finish 恢复）——否则停止后立即重开会与仍在收尾的旧任务
        双线程并发（旧取消信号被 begin_run 清掉，两个线程同时写队列/
        输出目录）。
        """
        self.running = False
        self._cancel_event.set()
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="disabled")
        self._emit("收到停止请求，将在当前文件处理完后停止...")

    # ---- 完成（主线程，由 __DONE__ sentinel 触发） ----
    def _on_finish(self, success: bool) -> None:
        """默认恢复按钮状态；子类可覆盖（如弹窗提示）。"""
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    # ---- 关闭钩子（由统一入口调用，不销毁窗口） ----
    def _on_close(self) -> None:
        """统一关闭钩子：置停止标志通知后台线程；不销毁窗口。"""
        self.running = False
        self._cancel_event.set()
