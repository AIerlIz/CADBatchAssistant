"""一键流程：xlsx + 图纸（DWG/DXF）→ 从模板推断规格 → 填表 → 输出。

- 支持目录输入（before_dir 内全部图纸）或文件列表输入（run_pipeline_files）。
- DWG 输入：经 ODA 转 DXF 处理，输出转回 DWG；DXF 输入：直接处理，输出保持 DXF。
- 纯 DXF 流程无需 ODAFileConverter。
- DWG 存在时分块「转换→填表→转回」（run_dwg_roundtrip_chunks）：块 k+1 的
  转换与块 k 的填表重叠（ODA 与进程池并行），进度按图纸数推进。
- progress 回调：25%（[1/4] DXF 准备 + DWG 首块转换）→ 50%（规格）→
  50%-75%（填表按图纸推进，DWG 分块转回在其中完成）→ 100%（DXF 输出）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.common.dwg_workflow import (
    CHUNK_SIZE_DEFAULT,
    chunk_stems,
    run_dwg_roundtrip_chunks,
    stage_dxf_batch,
    write_back_dxf_batch,
)
from cadbatchassistant.core.common.input_files import (
    check_duplicate_names,
    stage_inputs,
)
from cadbatchassistant.core.fill.fill_dwg import entity_to_desc, fill_all
from cadbatchassistant.core.fill.fill_parse_xlsx import build_text_lookup, extract_dxf_text


def _classify_by_ext(d: str) -> dict[str, str]:
    """目录内 DWG/DXF 文件名（去扩展名）→ 类型（"dwg"/"dxf"，dwg 优先）。

    同名 .dwg 与 .dxf 共存时显式优先按 DWG 处理（结果确定，不依赖遍历顺序）；
    升序排序下 "*.dwg" < "*.dxf"（'w'<'x'），setdefault 保留先者 → dwg 优先。
    """
    by_ext: dict[str, str] = {}
    for f in sorted(os.listdir(d), key=str.lower):
        low = f.lower()
        stem = os.path.splitext(f)[0]
        if low.endswith(".dwg"):
            by_ext.setdefault(stem, "dwg")
        elif low.endswith(".dxf"):
            by_ext.setdefault(stem, "dxf")
    return by_ext


def _names_from_dir(d: str) -> list[str]:
    """取目录内 DWG/DXF 文件名（去扩展名）并排序。"""
    names = sorted(_classify_by_ext(d))
    if not names:
        raise ValueError(f"目录中没有 DWG/DXF 文件：{d}")
    return names


def _check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise RuntimeError("已取消")


def _report(progress, percent: int) -> None:
    if progress is not None:
        progress(percent)


def run_pipeline(
    xlsx: str,
    before_dir: str,
    out_dir: str,
    oda: str | None = None,
    out_version: str = dc.DEFAULT_OUT_VERSION,
    workdir: str | None = None,
    emit=print,
    cancel=None,
    inputs: list[str] | None = None,
    progress=None,
    template: str | None = None,
    match_col: str | None = None,
    sheet: str | None = None,
    src_files: list[str] | None = None,
) -> dict:
    """执行完整流程，返回摘要 dict。

    xlsx        : 数据表路径
    before_dir  : 输入图纸目录（含 DWG 与/或 DXF）
    out_dir     : 输出目录
    oda         : ODAFileConverter.exe 路径；None 时自动探测（纯 DXF 流程可 None）
    out_version : 输出 DWG 版本（默认与 dwg_converter.DEFAULT_OUT_VERSION 一致）
    workdir     : 临时工作目录；None 时自动创建
    emit        : 日志回调
    cancel      : threading.Event
    inputs      : 仅处理的图纸名列表（去扩展名）；None 表示目录内全部
    progress    : 进度回调 progress(percent)
    template    : 图纸模板文件（单个 .dwg/.dxf，已填好的图框样例），必填；
                  任取一张处理图纸作"修改前"与之 diff 学习规格并广播到全部图纸
    match_col   : 数据表中图纸名列（None 默认第一列）
    sheet       : 数据表中工作表名（None 默认第一个）
    src_files   : 文件模式的总源文件绝对路径列表（run_pipeline_files 传入）：
                  提供时按文件自身扩展名分类（DWG 优先），DXF 直接从原始路径
                  复制进 DXF 批（省一次临时目录复制），不再扫描 before_dir。

    注意：workdir 为 None 时，本函数自建的临时目录在返回前已清理
    （finally 中 rmtree），返回 dict 中的 "workdir"/"specs" 路径已不存在，
    仅供日志/排查看，不可再读文件；调用方如需保留中间产物请传入 workdir。
    """
    if src_files is not None:
        src_map = {
            os.path.splitext(os.path.basename(f))[0]: os.path.abspath(f)
            for f in src_files
        }
        names = sorted(src_map)
        by_ext: dict[str, str] = {}
        for f in src_files:
            low = f.lower()
            stem = os.path.splitext(os.path.basename(f))[0]
            if low.endswith(".dwg"):
                by_ext.setdefault(stem, "dwg")
            elif low.endswith(".dxf"):
                by_ext.setdefault(stem, "dxf")
        if names != sorted(by_ext):
            raise ValueError("输入文件列表与图纸名不一致（存在无法识别的扩展名）")
        emit(f"待处理图纸 {len(names)} 张: {', '.join(names)}")
    else:
        names = sorted(inputs) if inputs else _names_from_dir(before_dir)
        if not names:
            raise ValueError(f"没有可处理的图纸：{before_dir}")
        emit(f"待处理图纸 {len(names)} 张: {', '.join(names)}")

        # 输出目录与输入目录重合时，清理旧输出会误删源文件 → 跳过清理并整体拒绝
        # （输出阶段会把处理结果覆盖到源文件所在位置，同样危险）。
        # 提前校验，避免已创建临时目录/输出目录后再失败。
        same_dir = os.path.normcase(os.path.abspath(out_dir)) == os.path.normcase(
            os.path.abspath(before_dir)
        )
        if same_dir:
            raise ValueError(
                f"输出目录不能与输入图纸目录相同：{out_dir}。"
                "请选择其他输出目录，避免覆盖源文件。"
            )

        # 判断哪些是 DWG（需 ODA 转换），哪些是 DXF（直接处理）——大小写不敏感；
        # 同名 .dwg 与 .dxf 共存时显式优先按 DWG 处理（见 _classify_by_ext）。
        by_ext = _classify_by_ext(before_dir)
        missing = [n for n in names if n not in by_ext]
        if missing:
            raise ValueError(f"以下图纸在目录中找不到文件：{', '.join(missing)}")

    # src_files 模式的输出目录重合防护由 run_pipeline_files 完成（out vs 各源目录）
    dwg_names = [n for n in names if by_ext.get(n) == "dwg"]
    dxf_names = [n for n in names if by_ext.get(n) == "dxf"]
    need_oda = bool(dwg_names)

    converter = dc.get_converter()
    oda_exe = converter.resolve(oda)
    err = converter.require_for_dwg(need_oda, str(oda_exe) if oda_exe else "")
    if err:
        raise FileNotFoundError(err)

    os.makedirs(out_dir, exist_ok=True)
    tmp_created = workdir is None  # 本函数自建临时目录时，结束后清理
    tmp = workdir or tempfile.mkdtemp(prefix="iso_fill_")
    try:
        before_dxf = os.path.join(tmp, "before")
        filled_dxf = os.path.join(tmp, "filled")
        for d in (before_dxf, filled_dxf):
            os.makedirs(d, exist_ok=True)

        # 清理输出目录中本次同名旧文件，避免残留；单个文件被占用/只读时不中断整批
        for n in names:
            for ext in (".DWG", ".dwg", ".dxf"):
                p = os.path.join(out_dir, n + ext)
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError as ex:
                        emit(
                            f"[WARN] 清理旧输出失败（跳过，可能被占用或只读）: "
                            f"{p} ({ex})"
                        )

        # [1/4] 准备 DXF 批：DXF 直接复制（目录模式从 before_dir、文件模式从
        # 原始路径）；DWG 首块在此经 ODA 预转（保持旧流程
        # 「先转换、后解析模板」的顺序，剩余块在分块阶段与填表重叠转换）。
        _check_cancel(cancel)
        emit(
            "[1/4] 准备 DXF（DXF 直接复制"
            + (f"，{len(dwg_names)} 个 DWG 分块经 ODA 转换" if dwg_names else "")
            + "） ..."
        )
        # 分块与 run_dwg_roundtrip_chunks 使用同一块大小（测试可 patch
        # CHUNK_SIZE_DEFAULT 调小块大小验证多块路径，两侧必须一致）
        dwg_chunks = (
            chunk_stems(dwg_names, CHUNK_SIZE_DEFAULT) if dwg_names else []
        )
        chunks_dir = os.path.join(tmp, "dwg_chunks")
        first_before_dir: str | None = None
        if dwg_names:
            os.makedirs(chunks_dir, exist_ok=True)
            first_before_dir = os.path.join(chunks_dir, "c0", "before")
            os.makedirs(first_before_dir, exist_ok=True)
        for n in dxf_names:
            src = (
                os.path.join(before_dir, n + ".dxf")
                if src_files is None
                else src_map[n]
            )
            shutil.copy2(src, os.path.join(before_dxf, n + ".dxf"))
        if dwg_names:
            first_before: str = first_before_dir or os.path.join(
                chunks_dir, "c0", "before"
            )
            stage_dxf_batch(
                converter,
                oda_exe,
                before_dir,
                first_before,
                [n + ".DWG" for n in dwg_chunks[0]],
                [],
                out_version,
            )
        _report(progress, 25)

        # [2/4] 图纸模板占位规格 → 广播到全部图纸（伴生 meta 优先，CLI 兜底现场解析）
        specs_path = os.path.join(tmp, "specs.json")
        _check_cancel(cancel)
        emit("[2/4] 读取模板占位配置 ...")
        from cadbatchassistant.core.common.template_meta import load_template_meta
        from cadbatchassistant.core.fill.fill_learn_spec import (
            scan_all_placeholders,
            value_rule_for,
        )
        from cadbatchassistant.core.fill.fill_parse_xlsx import load_xlsx_with_headers

        # 伴生 meta 优先（GUI 上传只存占位符 JSON，模板库无原文件）；
        # meta 缺失时（CLI / 命令行直接传模板路径）才要求模板文件存在并现场解析
        meta = load_template_meta(template) if template else None
        if meta is not None:
            placeholders = meta.get("placeholders")
            if not isinstance(placeholders, list) or not placeholders:
                raise ValueError("模板占位配置损坏或为空，请删除模板后重新上传")
        else:
            if not template or not os.path.isfile(str(template)):
                raise ValueError("缺少图纸模板文件（值格填 [字段名] 占位的 .dwg/.dxf）")
            # CLI / 命令行等直接传模板路径：现场转换并扫描（历史行为兜底）
            t_dxf = converter.template_to_dxf(
                template, tmp, oda_exe, out_version
            )
            placeholders = scan_all_placeholders(t_dxf)
        # 按本次数据表表头精确匹配（占位符文字去空白后与表头相同），
        # value_rule/sep 运行时按列名重算（与历史 scan_placeholders 行为一致）。
        # 一次读取同时取数据与表头（load_xlsx_with_headers），避免
        # get_headers + 后续 fill_all.load_xlsx 的整表二次解析（大表翻倍）。
        data, headers = load_xlsx_with_headers(xlsx, match_col, sheet)
        header_map = {h.strip(): h for h in headers}
        one_spec: dict = {}
        for ph in placeholders:
            header = header_map.get(ph["text"])
            if header is None:
                continue
            value_rule, sep = value_rule_for(header)
            one_spec.setdefault(ph["layer"], {})[header] = {
                "x": ph["x"],
                "y": ph["y"],
                "height": ph["height"],
                "style": ph["style"],
                "halign": ph["halign"],
                "valign": ph["valign"],
                "ref_text": ph["ref_text"],
                "value_rule": value_rule,
                "sep": sep,
                "entity": ph["entity_desc"],
            }
        n_fields = sum(len(v) for v in one_spec.values())
        if n_fields == 0:
            # 不中断：警告并按无字段处理（输出为原图），便于排查模板
            emit(
                "[WARN] 模板中未找到与数据表表头匹配的占位符，"
                "将按无字段处理（输出为原图）。请检查模板占位符是否与数据表列名一致。"
            )
        emit(f"      模板占位识别到 {n_fields} 个字段，应用到全部图纸")

        def _strip_entity(fields: dict) -> dict:
            return {
                f: {k: v for k, v in fs.items() if k != "entity"}
                for f, fs in fields.items()
            }

        # 浅拷贝广播；entity 转为轻量描述（可 pickle、不含文档引用，
        # 并行任务不会随每张图序列化整份模板文档）；JSON 输出剥离 entity
        def _desc_entity(fields: dict) -> dict:
            return {
                f: (
                    # meta 路径的 entity 已是 desc dict（JSON 化），直接使用
                    {**fs, "entity": fs["entity"]}
                    if isinstance(fs.get("entity"), dict)
                    else (
                        {**fs, "entity": entity_to_desc(fs["entity"])}
                        if fs.get("entity") is not None
                        else dict(fs)
                    )
                )
                for f, fs in fields.items()
            }

        specs = {
            n: {layer: _desc_entity(fields) for layer, fields in one_spec.items()}
            for n in names
        }
        json_specs = {
            n: {layer: _strip_entity(fields) for layer, fields in one_spec.items()}
            for n in names
        }
        with open(specs_path, "w", encoding="utf-8") as fh:
            json.dump(json_specs, fh, ensure_ascii=False, indent=2)

        _report(progress, 50)

        # [3/4] 填表（50%→75% 按图纸推进）：DWG 存在时分块执行，
        # 每块「转换→填表→转回」推进；DXF 名一次填表。
        # 匹配方式：取图纸内所有文字实体，与数据表 match_col 列做精确匹配；
        # 无文字/无匹配 → skipped（不在数据表中）。
        _check_cancel(cancel)
        emit("[3/4] 按 xlsx 填充标题栏值格 ...")

        # 全局图纸进度：每次 fill_all 的 progress 回调计数一次（含被跳过者），
        # 跨分块单调推进，避免并行完成序导致进度条来回跳动
        fill_done = {"v": 0}
        total_all = len(names)

        def _track_fill(_done: int, _total: int) -> None:
            fill_done["v"] += 1
            _report(progress, 50 + int(fill_done["v"] / max(total_all, 1) * 25))

        # 按图纸内文字构建匹配索引（match_col 列值 → (stem, row_data)）；
        # match_col 为 None 时回退到按 stem 匹配（向后兼容）。
        text_lookup = build_text_lookup(data, match_col)
        use_text_match = bool(match_col and text_lookup)

        def _match_by_text(stems: list[str], before_dir: str) -> tuple[dict, list[str], list[str]]:
            """按图纸内文字匹配数据表，返回 (匹配后的 data, failed, skipped)。

            match_col 仅用于匹配：取图纸内所有文字实体，与 match_col 列值精确匹配；
            任一文字命中即取对应行（无自一致性校验，由用户保证选对列）。
            match_col 为空时：回退到按 stem 在 data 中查找（向后兼容）。
            失败：匹配到多行（歧义）；跳过：无匹配、图纸无文字或缺少 before DXF。
            """
            matched_data: dict[str, dict[str, str]] = {}
            failed: list[str] = []
            skipped: list[str] = []
            for stem in stems:
                dxf_path = os.path.join(before_dir, stem + ".dxf")
                if not os.path.isfile(dxf_path):
                    emit(f"[WARN] 缺少 before DXF: {dxf_path}")
                    skipped.append(stem)
                    continue
                if use_text_match:
                    try:
                        texts = extract_dxf_text(dxf_path)
                    except Exception as ex:  # noqa: BLE001 - 单张图失败不中断整批
                        emit(f"[WARN] 读取图纸文字失败 {stem}：{ex}")
                        skipped.append(stem)
                        continue
                    if not texts:
                        emit(f"[WARN] {stem} 图纸内无文字，跳过")
                        skipped.append(stem)
                        continue
                    # 图纸文字与 match_col 列值精确匹配：任一文字命中即取对应行
                    hits: list[tuple[str, dict[str, str]]] = [
                        text_lookup[txt] for txt in texts if txt in text_lookup
                    ]
                    if not hits:
                        emit(
                            f"[WARN] {stem} 未在数据表中找到匹配 "
                            f"（match_col={match_col!r}，图纸文字样本：{sorted(texts)[:5]}）"
                        )
                        skipped.append(stem)
                        continue
                    # 取首次命中（重复列值以首次出现为准，由 build_text_lookup 保证）
                    matched_data[stem] = hits[0][1]
                else:
                    # 向后兼容：按 stem 直接查找
                    row = data.get(stem)
                    if row is None:
                        emit(f"[WARN] {stem} 不在 xlsx 中，跳过")
                        skipped.append(stem)
                        continue
                    matched_data[stem] = row
            return matched_data, failed, skipped

        failed: list[str] = []
        skipped: list[str] = []
        if dxf_names:
            matched_dxf_data, f_dxf, s_dxf = _match_by_text(dxf_names, before_dxf)
            if matched_dxf_data:
                f2, s2 = fill_all(
                    before_dxf,
                    filled_dxf,
                    xlsx,
                    {n: specs[n] for n in matched_dxf_data},
                    emit=emit,
                    progress=_track_fill,
                    sheet=sheet,
                    cancel=cancel,
                    data=matched_dxf_data,
                )
                failed.extend(f2)
                skipped.extend(s2)
            skipped.extend(s_dxf)
            failed.extend(f_dxf)
        if dwg_names:
            # chunks_dir 与首块 before 目录已在 [1/4] 创建/预转

            def _fill_chunk(before_c: str, filled_c: str, stems: list[str]):
                matched_chunk_data, f_chunk, s_chunk = _match_by_text(stems, before_c)
                if matched_chunk_data:
                    f2, s2 = fill_all(
                        before_c,
                        filled_c,
                        xlsx,
                        {s: specs[s] for s in matched_chunk_data},
                        emit=emit,
                        progress=_track_fill,
                        sheet=sheet,
                        cancel=cancel,
                        data=matched_chunk_data,
                    )
                    return f2 + f_chunk, s2 + s_chunk
                return f_chunk, s_chunk

            # DWG 分块「转换→填表→转回」+ 块间转换重叠（ODA 与进程池并行）；
            # 首块已在 [1/4] 预转；写回在块内完成 → 已在输出目录落盘，
            # 进度不再重复上报
            res = run_dwg_roundtrip_chunks(
                converter,
                oda_exe,
                before_dir,
                out_dir,
                dwg_names,
                out_version,
                process_batch=_fill_chunk,
                emit=emit,
                cancel=cancel,
                workdir=chunks_dir,
                chunk_size=CHUNK_SIZE_DEFAULT,
                pre_staged_chunks=1,
            )
            _check_cancel(cancel)
            failed.extend(res["failed"])
            skipped.extend(res["skipped"])

        # [4/4] 输出：DXF 输入直接复制（DWG 转回已在分块阶段完成）
        # 失败的图跳过；skipped（无产物：不在数据表/缺 before DXF）同样不算成功，
        # 否则输出阶段会因产物缺失报错或挂起等待转换。
        _check_cancel(cancel)
        emit(f"[4/4] 输出 DXF → {out_dir} ...")
        if dxf_names:
            write_back_dxf_batch(
                converter,
                oda_exe,
                filled_dxf,
                out_dir,
                [],
                [n + ".dxf" for n in dxf_names],
                out_version,
                skip=set(failed) | set(skipped),
            )
        _report(progress, 100)

        return {
            "workdir": tmp,
            "specs": specs_path,
            "output": out_dir,
            "count": len(names),
            "failed": failed,
            "skipped": skipped,
            "ok": len(names) - len(failed) - len(skipped),
        }
    finally:
        if tmp_created:
            shutil.rmtree(tmp, ignore_errors=True)


def run_pipeline_files(
    xlsx: str,
    files: list[str],
    out_dir: str,
    oda: str | None = None,
    out_version: str = dc.DEFAULT_OUT_VERSION,
    emit=print,
    cancel=None,
    progress=None,
    workdir: str | None = None,
    template: str | None = None,
    match_col: str | None = None,
    sheet: str | None = None,
) -> dict:
    """处理选中的文件列表（DWG/DXF 混合）。

    把选中文件复制到临时输入目录后调用 run_pipeline(inputs=..., template=...)。
    注意：workdir 为 None 时，本函数自建的临时目录（含复制的输入文件）
    在返回前已清理（finally 中 rmtree）；如需保留请传入 workdir。
    """
    if not files:
        raise ValueError("未选择任何图纸文件")
    # 只处理 DWG/DXF（与目录助手一致）；非 CAD 文件早期拒绝（旧实现会
    # 在 stage/目录扫描阶段以「找不到文件」报错，提前报错更清晰）
    from cadbatchassistant.core.common.filetypes import CAD_SUFFIXES

    bad_ext = [f for f in files if not f.lower().endswith(CAD_SUFFIXES)]
    if bad_ext:
        raise ValueError("仅支持 DWG/DXF 图纸文件：" + "、".join(bad_ext))
    # 重名检测（大小写不敏感）：纯 DXF 分支不再走 stage_inputs（那里自带检测），
    # 这里对全部输入先统一检测，防止跨目录同名文件被后续直接复制/处理时互相覆盖
    check_duplicate_names(files)
    # 输出目录与任一源文件所在目录重合时，处理结果会直接覆盖源文件 → 拒绝
    src_dirs = {os.path.normcase(os.path.abspath(os.path.dirname(f))) for f in files}
    if os.path.normcase(os.path.abspath(out_dir)) in src_dirs:
        raise ValueError(
            f"输出目录不能与输入图纸所在目录相同：{out_dir}。"
            "请选择其他输出目录，避免覆盖源文件。"
        )
    # 重名检测（大小写不敏感）+ 复制到临时输入目录（input_files.stage_inputs）。
    # 仅含 DWG 时复制（ODA 转换需要统一目录）；纯 DXF 直接以原始路径处理，
    # 省掉整批复制（复制减半，run_pipeline 的 src_files 分支直读源文件）。
    tmp_created = workdir is None  # 本函数自建临时目录时，结束后清理
    tmp = workdir or tempfile.mkdtemp(prefix="iso_fill_files_")
    try:
        has_dwg = any(f.lower().endswith(".dwg") for f in files)
        if has_dwg:
            before_dir, stems = stage_inputs(files, tmp, prefix="iso_fill_files_")
            return run_pipeline(
                xlsx,
                before_dir,
                out_dir,
                oda=oda,
                out_version=out_version,
                workdir=tmp,
                emit=emit,
                cancel=cancel,
                inputs=stems,
                progress=progress,
                template=template,
                match_col=match_col,
                sheet=sheet,
            )
        return run_pipeline(
            xlsx,
            tmp,
            out_dir,
            oda=oda,
            out_version=out_version,
            workdir=tmp,
            emit=emit,
            cancel=cancel,
            progress=progress,
            template=template,
            match_col=match_col,
            sheet=sheet,
            src_files=[os.path.abspath(f) for f in files],
        )
    finally:
        if tmp_created:
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="ISO 图纸标题栏填表（从模板推断规格）")
    ap.add_argument("--xlsx", required=True, help="数据表 .xlsx/.xls")
    ap.add_argument("--before", required=True, help="输入图纸目录（DWG/DXF）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument(
        "--oda", default=None, help="ODAFileConverter.exe 路径（默认自动探测）"
    )
    ap.add_argument(
        "--template", required=True, help="图纸模板文件（已填好的 .dwg/.dxf 样例）"
    )
    ap.add_argument(
        "--version",
        default=dc.DEFAULT_OUT_VERSION,
        help=f"输出 DWG 版本（默认 {dc.DEFAULT_OUT_VERSION}）",
    )
    ap.add_argument("--match-col", default=None, help="数据表中图纸名列（默认第一列）")
    ap.add_argument("--sheet", default=None, help="数据表中工作表名（默认第一个）")
    args = ap.parse_args()
    summary = run_pipeline(
        args.xlsx,
        args.before,
        args.out,
        oda=args.oda,
        out_version=args.version,
        template=args.template,
        match_col=args.match_col,
        sheet=args.sheet,
    )
    print("\n完成:", summary)


if __name__ == "__main__":
    main()
