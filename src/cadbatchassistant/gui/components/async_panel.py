"""后台任务面板通用骨架：后台线程 + 消息队列 + after 轮询。"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk

from cadbatchassistant.gui.components.tk_util import apply_vista_theme


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
    def _emit(self, msg: str | None = None, progress: int | None = None) -> None:
        self.msg_queue.put((msg, progress))

    def _start_worker(self, args: tuple) -> None:
        """在后台线程运行 self._run(*args)；分配新任务代次。"""
        self._run_seq += 1
        seq = self._run_seq
        self.worker = threading.Thread(target=self._run, args=(seq, *args), daemon=True)
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
        except Exception as ex:
            # 堆栈落日志文件（GUI 只显示一行，完整现场供排查）
            logging.getLogger("cadbatchassistant.gui.components.async_panel").exception(
                "后台任务处理中断"
            )
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
        """恢复按钮状态，并触发 _finish_notify 完成提示钩子。

        按钮复位与提示分离：_finish_notify 由子类覆盖为弹窗（默认无操作），
        避免子类为加弹窗而覆盖整个 _on_finish 或依赖 super() 的 MRO 链。
        """
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._finish_notify(success)

    def _finish_notify(self, success: bool) -> None:
        """完成提示钩子（默认无操作）；子类覆盖为弹窗/日志汇总。"""

    # ---- 关闭钩子（由统一入口调用，不销毁窗口） ----
    def _on_close(self) -> None:
        """统一关闭钩子：置停止标志通知后台线程；不销毁窗口。"""
        self.running = False
        self._cancel_event.set()
