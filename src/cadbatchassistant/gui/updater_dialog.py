"""更新下载对话框：展示下载进度，完成后替换当前 exe 并自动重启。

入口 start_update_download(parent, latest, mirror)：
- 后台线程分块下载到临时目录（进度经队列回主线程刷新进度条）
- 下载完成 → 用户确认 → run_replace 启动 PowerShell 替换进程 → 关闭主窗口
- 失败 / 取消：清理临时文件并提示
"""

from __future__ import annotations

import hashlib
import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from cadbatchassistant import __version__
from cadbatchassistant.common import center_window
from cadbatchassistant.core import updater

_DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "CADBatchAssistant_update"
_NEW_EXE = "CADBatchAssistant_new.exe"


def ask_update_choice(parent: tk.Widget, tag: str) -> str:
    """发现新版本时的三选对话框。

    返回 'update'（立即更新）/ 'ignore'（忽略此版本）/ 'cancel'（取消）。
    """
    root = parent.winfo_toplevel()
    result = {"choice": "cancel"}
    top = tk.Toplevel(root)
    top.title("发现新版本")
    top.resizable(False, False)
    top.transient(root)
    top.grab_set()

    ttk.Label(
        top,
        text=f"发现新版本 {tag}（当前 v{__version__}）\n\n是否立即下载并更新？",
        justify="left",
    ).pack(padx=16, pady=(14, 10))
    btns = ttk.Frame(top)
    btns.pack(pady=(0, 12))

    def _pick(choice: str) -> None:
        result["choice"] = choice
        top.destroy()

    ttk.Button(btns, text="立即更新",
               command=lambda: _pick("update")).pack(side="left", padx=6)
    ttk.Button(btns, text="忽略此版本",
               command=lambda: _pick("ignore")).pack(side="left", padx=6)
    ttk.Button(btns, text="取消",
               command=lambda: _pick("cancel")).pack(side="left", padx=6)
    top.protocol("WM_DELETE_WINDOW", lambda: _pick("cancel"))
    center_window(top, root)  # 相对主窗口居中
    top.wait_window()
    return result["choice"]


def start_update_download(parent: tk.Widget, latest: dict, mirror: str) -> None:
    """打开更新下载对话框（模态阻塞至流程结束）。"""
    dialog = UpdateDialog(parent, latest, mirror)
    dialog.run()


class UpdateDialog:
    """模态对话框：进度条 + 取消按钮；下载/替换流程结束即关闭。"""

    def __init__(self, parent: tk.Widget, latest: dict, mirror: str) -> None:
        self._root = parent.winfo_toplevel()
        self._latest = latest
        self._mirror = mirror
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._closed = False

        self._top = tk.Toplevel(self._root)
        self._top.title("软件更新")
        self._top.resizable(False, False)
        self._top.transient(self._root)
        self._top.grab_set()

        frame = ttk.Frame(self._top, padding=12)
        frame.pack(fill="both", expand=True)
        self._status = tk.StringVar(value=f"正在下载 {latest['tag']} ...")
        ttk.Label(frame, textvariable=self._status).pack(anchor="w")
        self._progress = ttk.Progressbar(frame, mode="determinate")
        self._progress.pack(fill="x", pady=8)
        self._detail = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._detail).pack(anchor="w")
        ttk.Button(frame, text="取消", command=self._on_cancel).pack(
            anchor="e", pady=(8, 0))

        self._top.protocol("WM_DELETE_WINDOW", self._on_cancel)
        center_window(self._top, self._root)  # 相对主窗口居中
        self._top.after(100, self._poll)
        threading.Thread(target=self._work, daemon=True).start()

    def run(self) -> None:
        """阻塞直到对话框关闭（调用方在主线程）。"""
        self._top.wait_window()

    # ---------------- 后台下载 ----------------
    def _work(self) -> None:
        dest = _DOWNLOAD_DIR / _NEW_EXE

        def progress(done: int, total: int) -> None:
            if self._cancel.is_set():
                raise updater.UpdateError(updater.CANCEL_MSG)
            self._queue.put(("progress", done, total))

        try:
            updater.download_asset(
                self._latest["url"], dest, self._mirror, progress,
                size=self._latest.get("size"),
                abort_event=self._cancel,
                sha256_url=self._latest.get("sha256_url"))
            self._queue.put(("done", str(dest)))
        except updater.UpdateError as e:
            self._queue.put(("error", str(e)))
        except Exception as e:  # noqa: BLE001 - 意外异常也要回主线程提示，
            # 否则对话框永远停在"正在下载"（无任何消息）无法关闭
            self._queue.put(("error", f"下载过程中发生意外错误：{e}"))

    # ---------------- 主线程轮询 ----------------
    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, done, total = item
                    if total:
                        self._progress.config(maximum=total, value=done)
                        self._detail.set(f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
                    else:
                        self._progress.config(mode="indeterminate")
                        self._progress.start()
                elif kind == "done":
                    self._on_downloaded(item[1])
                    return
                else:  # error
                    if self._cancel.is_set():
                        # 用户已取消下载：静默关闭，不再询问重试
                        self._top.destroy()
                        return
                    self._finish_error(item[1])
                    return
        except queue.Empty:
            pass
        try:
            self._top.after(100, self._poll)
        except tk.TclError:
            pass  # 窗口已销毁（取消/关闭）

    # ---------------- 结束分支 ----------------
    def _on_cancel(self) -> None:
        """用户取消：置标志停止下载，关闭对话框并清理临时文件。"""
        self._cancel.set()
        self._closed = True
        try:
            self._top.destroy()
        except tk.TclError:
            pass
        try:
            (_DOWNLOAD_DIR / _NEW_EXE).unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _sha256_of(dest: str) -> str:
        """分块计算文件 SHA-256（hex 小写）。"""
        hasher = hashlib.sha256()
        with open(dest, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _on_downloaded(self, dest: str) -> None:
        self._status.set("下载完成")
        self._progress.stop()
        if not messagebox.askyesno(
            "更新就绪",
            "下载完成。\n\n关闭程序后将自动替换并重新启动，是否继续？",
            parent=self._root,
        ):
            self._top.destroy()
            return
        # 计算下载 exe 的 SHA-256，交给替换脚本在校验后重启：
        # 防止复制环节产生损坏文件导致"升级后启动即崩"（如 Failed to load
        # Python DLL），校验失败保留旧 exe 并写日志。
        sha256 = self._sha256_of(dest)
        # 启动 PowerShell 替换进程（等待本进程退出 → 覆盖 exe → 校验 → 重启）
        updater.run_replace(dest, updater.current_exe_path(), restart=True,
                            expected_sha256=sha256)
        self._top.destroy()
        # 先让各面板停止后台线程，再销毁主窗口（与 main.py 的关闭钩子一致）
        handler = getattr(self._root, "on_close", None)
        if callable(handler):
            try:
                handler()
            except Exception:  # noqa: BLE001 - 清理钩子失败不阻断更新
                pass
        try:
            self._root.destroy()
        except tk.TclError:
            pass

    def _finish_error(self, msg: str) -> None:
        """下载失败：提示原因并询问是否重试；重试复用本对话框，否则关闭。

        msg 已含 updater 层给出的建议（检查网络 / 更换镜像等）。
        """
        self._progress.stop()
        self._progress.config(mode="determinate", maximum=100, value=0)
        self._detail.set("")
        if messagebox.askyesno(
            "更新失败", f"{msg}\n\n是否重试下载？", parent=self._root,
        ):
            self._status.set(f"正在重试下载 {self._latest['tag']} ...")
            self._cancel.clear()
            threading.Thread(target=self._work, daemon=True).start()
            self._top.after(100, self._poll)
            return
        self._top.destroy()
