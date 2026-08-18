"""DWG 批处理工作流：统一「DWG→DXF 批（供处理）→ 处理后写回」编排。

「改字」「填表」两条流程都需要把一批图纸统一成 DXF 处理，再按源类型写回
（DWG 源转回 DWG，DXF 源保持 DXF）；「目录」流程只用到前一半（单向转换）。
本模块把这套编排抽成两个函数供复用，转换细节委托给 Converter 实现
（默认 ODA，经 get_converter 获取，可切换）。

分块流水线 run_dwg_roundtrip_chunks：把 DWG 批按块「转换→处理→转回」，
块 k+1 的转换在后台线程进行、与块 k 的 ezdxf 处理重叠——ODA（外部进程）
干活的同时进程池不再闲置。ODA 始终单实例串行（同一时刻至多一个转换进程）。
「填表」「改字」的 DWG 分支共用该辅助。

文件名约定：dwg_files / dxf_files 均为含扩展名的文件名（如 "a.DWG" / "b.dxf"），
与 dwg_converter 的转换接口一致；调用方需自行保证扩展名与文件实际类型匹配。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

from cadbatchassistant.core.dwg_converter import (
    DEFAULT_OUT_VERSION,
    Converter,
)


def stage_dxf_batch(
    converter: Converter,
    oda_exe: str | Path,
    in_dir: str | Path,
    dxf_out: str | Path,
    dwg_files: list[str],
    dxf_files: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
) -> None:
    """把 DWG（经转换）与 DXF（直接复制）统一成 DXF 批到 dxf_out。

    in_dir   : 输入目录（含源图纸）
    dxf_out  : DXF 批输出目录（DWG 转换产物 + DXF 副本，供后续处理）
    dwg_files: 需要转换的 DWG 文件名列表（含扩展名）
    dxf_files: 需要复制的 DXF 文件名列表（含扩展名）
    """
    Path(dxf_out).mkdir(parents=True, exist_ok=True)
    if dwg_files:
        converter.dwg_to_dxf(oda_exe, in_dir, dxf_out, dwg_files, out_version)
    for name in dxf_files:
        shutil.copy2(Path(in_dir) / name, Path(dxf_out) / name)


def write_back_dxf_batch(
    converter: Converter,
    oda_exe: str | Path,
    processed_dir: str | Path,
    out_dir: str | Path,
    dwg_files: list[str],
    dxf_files: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
    skip: set[str] | frozenset[str] | None = None,
) -> None:
    """把处理后的 DXF 写回输出目录：DWG 源转回 DWG，DXF 源直接复制。

    processed_dir: 处理后的 DXF 批目录（与 stage_dxf_batch 的 dxf_out 对应）
    out_dir       : 输出目录（自动创建）
    skip          : 不写回的图纸名集合（无扩展名 stem，如 "A1"；转换失败/
                    跳过的图纸）。dwg_files/dxf_files 含扩展名，这里按
                    Path(name).stem 比较，与 fill_all 返回的 failed/skipped
                    （stem）一致。
    """
    skip = set(skip or ())
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    ok_dwg = [n for n in dwg_files if Path(n).stem not in skip]
    if ok_dwg:
        converter.dxf_to_dwg(
            oda_exe,
            processed_dir,
            out_dir,
            [Path(n).stem + ".dxf" for n in ok_dwg],
            out_version,
        )
    for name in dxf_files:
        if Path(name).stem in skip:
            continue
        shutil.copy2(Path(processed_dir) / name, Path(out_dir) / name)


# 分块流水线的默认块大小（张）：块间转换与处理重叠时，块内文件数过大
# 会拉长单个 ODA 调用、过小则增加 ODA 启动次数；8 是实测折中。
CHUNK_SIZE_DEFAULT = 8


def chunk_stems(
    stems: list[str], chunk_size: int = CHUNK_SIZE_DEFAULT
) -> list[list[str]]:
    """把图纸名列表按 chunk_size 切块（供调用方与 run_dwg_roundtrip_chunks
    使用同一分块：分块目录命名依赖块序，两侧必须一致）。"""
    return [stems[i : i + chunk_size] for i in range(0, len(stems), chunk_size)]


def run_dwg_roundtrip_chunks(
    converter: Converter,
    oda_exe: str | Path,
    in_dir: str | Path,
    out_dir: str | Path,
    dwg_stems: list[str],
    out_version: str = DEFAULT_OUT_VERSION,
    process_batch=None,
    emit=print,
    cancel=None,
    chunk_size: int = CHUNK_SIZE_DEFAULT,
    workdir: str | Path | None = None,
    progress_writeback=None,
    pre_staged_chunks: int = 0,
) -> dict:
    """DWG「转换→处理→转回」分块流水线。

    in_dir   : 源 DWG 目录（含 *.DWG）
    out_dir  : 输出目录（转回 DWG 落点，自动创建）
    dwg_stems: 去扩展名 DWG 名列表（顺序即处理顺序）
    process_batch(before_dir, filled_dir, stems) -> (failed, skipped)
        before_dir 为该块转换出的 DXF 目录；filled_dir 为该块处理产物输出目录；
        stems 为该块图纸名。返回 (failed, skipped)（failed/skipped 的图纸
        不会写回）。调用方负责并行执行与日志/进度回调。
    workdir  : 分块临时目录（调用方创建并在流程结束后清理）；每块用独立
        子目录 workdir/c{k}（避免 ODA 整目录重复转换）。必须提供。
    cancel   : threading.Event；置位时停止——运行中的块处理完即停，写回阶段
        不再进行（已写回的早前分块保留在输出目录）；后台转换线程先 join
        等待其 ODA 结束（与旧实现转换阶段取消需等 ODA 完成一致）。
    progress_writeback(done_chunks, total_chunks)：每块写回完成回调。
    pre_staged_chunks : 已由调用方完成首 N 块转换（其 before 目录按
        workdir/c{k}/before 建立）；用于保持「先转首块再解析模板」的旧顺序，
        分块须与 chunk_stems(dwg_stems, chunk_size) 完全一致。
    返回 {"failed": [...], "skipped": [...], "ok": int}。

    说明：块 k+1 的转换（后台线程 + ODA）与块 k 的 ezdxf 处理（进程池）重叠，
    但同一时刻至多一个 ODA 子进程（块 k+1 转换线程在块 k 写回前已 join）。
    """
    if workdir is None:
        raise ValueError("run_dwg_roundtrip_chunks 需要 workdir（分块临时目录）")
    workdir = Path(workdir)
    n = len(dwg_stems)
    chunks = chunk_stems(dwg_stems, chunk_size)
    m = len(chunks)
    dirs = [
        (workdir / f"c{k}" / "before", workdir / f"c{k}" / "filled")
        for k in range(m)
    ]
    for before_d, filled_d in dirs:
        before_d.mkdir(parents=True, exist_ok=True)
        filled_d.mkdir(parents=True, exist_ok=True)

    def _is_cancel() -> bool:
        return cancel is not None and cancel.is_set()

    def _stage(k: int) -> None:
        before_d = dirs[k][0]
        chunk = chunks[k]
        emit(f"转换分块 {k + 1}/{m}（{len(chunk)} 张 DWG → DXF）...")
        stage_dxf_batch(
            converter,
            oda_exe,
            in_dir,
            before_d,
            [s + ".DWG" for s in chunk],
            [],
            out_version,
        )

    failed_all: list[str] = []
    skipped_all: list[str] = []
    stage_future: threading.Thread | None = None
    try:
        if pre_staged_chunks <= 0:
            _stage(0)
        for k in range(m):
            if _is_cancel():
                break
            before_d, filled_d = dirs[k]
            # 预启动下一块转换（与当前块的处理重叠）
            if k + 1 < m and not _is_cancel():
                stage_future = threading.Thread(
                    target=_stage, args=(k + 1,), daemon=True
                )
                stage_future.start()
            failed, skipped = process_batch(before_d, filled_d, chunks[k])
            failed_all.extend(failed)
            skipped_all.extend(skipped)
            if stage_future is not None:
                stage_future.join()
                stage_future = None
            if _is_cancel():
                emit("[WARN] 收到取消请求，停止 DWG 分块写回（已写回的保留在输出目录）")
                break
            # 写回本块（失败/跳过的图纸不写回）
            ok_stems = [
                s
                for s in chunks[k]
                if s not in failed_all and s not in skipped_all
            ]
            if ok_stems:
                emit(f"写回分块 {k + 1}/{m}（{len(ok_stems)} 张 DXF → DWG）...")
                write_back_dxf_batch(
                    converter,
                    oda_exe,
                    filled_d,
                    out_dir,
                    [s + ".DWG" for s in ok_stems],
                    [],
                    out_version,
                )
            if progress_writeback is not None:
                progress_writeback(k + 1, m)
    finally:
        if stage_future is not None:
            stage_future.join()
    return {
        "failed": failed_all,
        "skipped": skipped_all,
        "ok": n - len(failed_all) - len(skipped_all),
    }
