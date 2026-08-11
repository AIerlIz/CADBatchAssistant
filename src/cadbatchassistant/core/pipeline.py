# -*- coding: utf-8 -*-
"""一键流程：xlsx + 图纸（DWG/DXF）→ 从模板推断规格 → 填表 → 输出。

- 支持目录输入（before_dir 内全部图纸）或文件列表输入（run_pipeline_files）。
- DWG 输入：经 ODA 转 DXF 处理，输出转回 DWG；DXF 输入：直接处理，输出保持 DXF。
- 纯 DXF 流程无需 ODAFileConverter。
- progress 回调：转换完成 25%、规格完成 50%、填表阶段 50%→75%（按图纸推进）、
  转回完成 100%。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.fill_dwg import fill_all


def _names_from_dir(d: str) -> list[str]:
    """取目录内 DWG/DXF 文件名（去扩展名）并排序。"""
    out = set()
    for f in os.listdir(d):
        if f.lower().endswith((".dwg", ".dxf")):
            out.add(os.path.splitext(f)[0])
    names = sorted(out)
    if not names:
        raise ValueError(f"目录中没有 DWG/DXF 文件：{d}")
    return names


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")


def _report(progress, percent: int) -> None:
    if progress is not None:
        progress(percent)


def _template_to_dxf(template: str, oda_exe, tmp: str, out_version: str,
                     emit=print) -> str:
    """把图纸模板（.dwg/.dxf）转为 DXF，返回模板 DXF 路径。"""
    tname = os.path.basename(template)
    t_stem = os.path.splitext(tname)[0]
    if tname.lower().endswith(".dwg"):
        t_dir = os.path.join(tmp, "tmpl")
        t_out = os.path.join(tmp, "tmpl_dxf")
        os.makedirs(t_dir, exist_ok=True)
        os.makedirs(t_out, exist_ok=True)
        shutil.copy2(template, os.path.join(t_dir, tname))
        dc.convert_dwg_batch_to_dxf(oda_exe, t_dir, t_out, [tname], out_version)
        return os.path.join(t_out, t_stem + ".dxf")
    # .dxf 直接复制
    t_dxf = os.path.join(tmp, "tmpl.dxf")
    shutil.copy2(template, t_dxf)
    return t_dxf


def run_pipeline(xlsx: str, before_dir: str, out_dir: str,
                 oda: str | None = None, out_version: str = "ACAD2004",
                 workdir: str | None = None, emit=print,
                 cancel=None, inputs: list[str] | None = None,
                 progress=None, template: str | None = None,
                 match_col: str | None = None,
                 sheet: str | None = None) -> dict:
    """执行完整流程，返回摘要 dict。

    xlsx        : 数据表路径
    before_dir  : 输入图纸目录（含 DWG 与/或 DXF）
    out_dir     : 输出目录
    oda         : ODAFileConverter.exe 路径；None 时自动探测（纯 DXF 流程可 None）
    out_version : 输出 DWG 版本（默认 ACAD2004）
    workdir     : 临时工作目录；None 时自动创建
    emit        : 日志回调
    cancel      : threading.Event
    inputs      : 仅处理的图纸名列表（去扩展名）；None 表示目录内全部
    progress    : 进度回调 progress(percent)
    template    : 图纸模板文件（单个 .dwg/.dxf，已填好的图框样例），必填；
                  任取一张处理图纸作"修改前"与之 diff 学习规格并广播到全部图纸
    match_col   : 数据表中图纸名列（None 默认第一列）
    sheet       : 数据表中工作表名（None 默认第一个）
    """
    names = sorted(inputs) if inputs else _names_from_dir(before_dir)
    if not names:
        raise ValueError(f"没有可处理的图纸：{before_dir}")
    emit(f"待处理图纸 {len(names)} 张: {', '.join(names)}")

    # 判断哪些是 DWG（需 ODA 转换），哪些是 DXF（直接处理）——大小写不敏感
    by_ext: dict[str, str] = {}
    for f in os.listdir(before_dir):
        low = f.lower()
        stem = os.path.splitext(f)[0]
        if low.endswith(".dwg"):
            by_ext.setdefault(stem, "dwg")
        elif low.endswith(".dxf"):
            by_ext.setdefault(stem, "dxf")
    dwg_names = [n for n in names if by_ext.get(n) == "dwg"]
    dxf_names = [n for n in names if by_ext.get(n) == "dxf"]
    missing = [n for n in names if n not in by_ext]
    if missing:
        raise ValueError(f"以下图纸在目录中找不到文件：{', '.join(missing)}")
    need_oda = bool(dwg_names)

    oda_exe = oda or dc.find_oda_converter()
    if need_oda and (not oda_exe or not os.path.isfile(str(oda_exe))):
        raise FileNotFoundError("未找到 ODAFileConverter.exe，请在选项里指定路径")

    os.makedirs(out_dir, exist_ok=True)
    tmp = workdir or tempfile.mkdtemp(prefix="iso_fill_")
    before_dxf = os.path.join(tmp, "before")
    filled_dxf = os.path.join(tmp, "filled")
    for d in (before_dxf, filled_dxf):
        os.makedirs(d, exist_ok=True)

    # 清理输出目录中本次同名旧文件，避免残留
    for n in names:
        for ext in (".DWG", ".dwg", ".dxf"):
            p = os.path.join(out_dir, n + ext)
            if os.path.isfile(p):
                os.remove(p)

    # [1/4] DWG → DXF；DXF 直接复制
    _check_cancel(cancel)
    emit("[1/4] 准备 DXF（DWG 经 ODA 转换，DXF 直接复制） ...")
    if dwg_names:
        dc.convert_dwg_batch_to_dxf(oda_exe, before_dir, before_dxf,
                                    [n + ".DWG" for n in dwg_names], out_version)
    for n in dxf_names:
        shutil.copy2(os.path.join(before_dir, n + ".dxf"),
                     os.path.join(before_dxf, n + ".dxf"))
    _report(progress, 25)

    # [2/4] 图纸模板占位扫描规格 → 广播到全部图纸
    specs_path = os.path.join(tmp, "specs.json")
    _check_cancel(cancel)
    emit("[2/4] 扫描图纸模板占位文字 ...")
    if not template or not os.path.isfile(str(template)):
        raise ValueError("缺少图纸模板文件（值格填 [字段名] 占位的 .dwg/.dxf）")
    t_dxf = _template_to_dxf(template, oda_exe, tmp, out_version, emit)
    from cadbatchassistant.core.learn_spec import scan_placeholders

    one_spec = scan_placeholders(t_dxf, xlsx, sheet)
    n_fields = sum(len(v) for v in one_spec.values())
    if n_fields == 0:
        # 不中断：警告并按无字段处理（输出为原图），便于排查模板
        emit("[WARN] 模板中未找到与数据表表头匹配的占位符，"
             "将按无字段处理（输出为原图）。请检查模板占位符是否与数据表列名一致。")
    emit(f"      模板占位识别到 {n_fields} 个字段，应用到全部图纸")

    def _strip_entity(fields: dict) -> dict:
        return {f: {k: v for k, v in fs.items() if k != "entity"}
                for f, fs in fields.items()}

    # 浅拷贝广播（共享占位符实体引用，只读）；JSON 输出剥离 entity
    specs = {n: {layer: dict(fields) for layer, fields in one_spec.items()}
             for n in names}
    json_specs = {n: {layer: _strip_entity(fields)
                      for layer, fields in one_spec.items()} for n in names}
    with open(specs_path, "w", encoding="utf-8") as fh:
        json.dump(json_specs, fh, ensure_ascii=False, indent=2)

    # [2b]（无超限检查：不再推断单元格边界）

    _report(progress, 50)

    # [3/4] 填表（50%→75% 按图纸推进）
    _check_cancel(cancel)
    emit("[3/4] 按 xlsx 填充标题栏值格 ...")

    def _fill_progress(done: int, total: int) -> None:
        _report(progress, 50 + int(done / max(total, 1) * 25))

    failed = fill_all(before_dxf, filled_dxf, xlsx, specs, emit=emit,
                      progress=_fill_progress, match_col=match_col,
                      sheet=sheet)
    _report(progress, 75)

    # [4/4] 输出：DWG 输入转回 DWG；DXF 输入直接复制（失败的图跳过）
    ok_names = [n for n in names if n not in failed]
    _check_cancel(cancel)
    emit(f"[4/4] 输出 → {out_dir} ...")
    ok_dwg = [n for n in dwg_names if n in ok_names]
    if ok_dwg:
        dc.convert_dxf_batch_to_dwg(oda_exe, filled_dxf, out_dir,
                                    [n + ".dxf" for n in ok_dwg], out_version)
    for n in dxf_names:
        if n not in ok_names:
            continue
        shutil.copy2(os.path.join(filled_dxf, n + ".dxf"),
                     os.path.join(out_dir, n + ".dxf"))
    _report(progress, 100)

    return {
        "workdir": tmp,
        "specs": specs_path,
        "output": out_dir,
        "count": len(names),
        "failed": failed,
        "ok": len(names) - len(failed),
    }


def run_pipeline_files(xlsx: str, files: list[str], out_dir: str,
                       oda: str | None = None, out_version: str = "ACAD2004",
                       emit=print, cancel=None, progress=None,
                       workdir: str | None = None,
                       template: str | None = None,
                       match_col: str | None = None,
                       sheet: str | None = None) -> dict:
    """处理选中的文件列表（DWG/DXF 混合）。

    把选中文件复制到临时输入目录后调用 run_pipeline(inputs=..., template=...)。
    """
    if not files:
        raise ValueError("未选择任何图纸文件")
    tmp = workdir or tempfile.mkdtemp(prefix="iso_fill_files_")
    before_dir = os.path.join(tmp, "inputs")
    os.makedirs(before_dir, exist_ok=True)
    stems: list[str] = []
    for f in files:
        name = os.path.basename(f)
        shutil.copy2(f, os.path.join(before_dir, name))
        stems.append(os.path.splitext(name)[0])
    return run_pipeline(xlsx, before_dir, out_dir, oda=oda,
                        out_version=out_version, workdir=tmp,
                        emit=emit, cancel=cancel, inputs=stems,
                        progress=progress, template=template,
                        match_col=match_col, sheet=sheet)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="ISO 图纸标题栏填表（从模板推断规格）")
    ap.add_argument("--xlsx", required=True, help="数据表 .xlsx/.xls")
    ap.add_argument("--before", required=True, help="输入图纸目录（DWG/DXF）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--oda", default=None, help="ODAFileConverter.exe 路径（默认自动探测）")
    ap.add_argument("--template", required=True, help="图纸模板文件（已填好的 .dwg/.dxf 样例）")
    ap.add_argument("--version", default="ACAD2004", help="输出 DWG 版本（默认 ACAD2004）")
    ap.add_argument("--match-col", default=None,
                    help="数据表中图纸名列（默认第一列）")
    ap.add_argument("--sheet", default=None,
                    help="数据表中工作表名（默认第一个）")
    args = ap.parse_args()
    summary = run_pipeline(args.xlsx, args.before, args.out,
                           oda=args.oda, out_version=args.version,
                           template=args.template,
                           match_col=args.match_col, sheet=args.sheet)
    print("\n完成:", summary)


if __name__ == "__main__":
    main()
