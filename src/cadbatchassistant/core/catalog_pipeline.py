"""一键流程（目录助手）：图纸模板 DWG + 图纸文件列表 → 目录 Excel。

流程：解析模板锚点 → 图纸/模板统一转 DXF → 按锚点提取每字段值
（图号取不到用文件名兜底）→ 文件粒度目录 → 输出 Excel。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from cadbatchassistant.core import catalog_excel_writer
from cadbatchassistant.core import dwg_converter as dc
from cadbatchassistant.core.catalog_builder import (
    FileEntry,
    build_file_catalog,
)
from cadbatchassistant.core.catalog_reader import extract_by_anchors
from cadbatchassistant.core.catalog_template_reader import (
    Anchor,
    collect_fields,
    parse_template,
)
from cadbatchassistant.core.input_files import check_duplicate_names
from cadbatchassistant.core.parallel import TaskFailed, map_files

# 回调：log(msg)，progress(0-100 整数)
LogFn = Callable[[str], None]
ProgressFn = Callable[[int], None]


def _point_tolerance(rules: dict | None) -> float:
    """安全解析 point_tolerance 配置；缺失/非法值时回退默认 5.0。

    配置来自用户可编辑的 config.json，非法值不应让整个流程中断。
    """
    try:
        return float((rules or {}).get("point_tolerance", 5))
    except (TypeError, ValueError):
        return 5.0


class PipelineResult:
    """一次流程的结果。"""

    def __init__(self) -> None:
        self.ok = False
        self.error = ""
        self.out_path: Path | None = None
        self.total_files = 0
        self.failed_files: list[str] = []
        self.na_rows = 0
        self.total_pages = 0
        self.fields: list[str] = []


def parse_template_anchors(template_dwg: str | Path,
                           oda: str = "") -> list[Anchor]:
    """解析图纸模板，返回锚点列表（含字段名与取值区域）。

    模板为 DWG 时先复制到临时目录并转 DXF（oda 为空时自动探测
    ODAFileConverter）；无占位符/转换失败时抛异常，由调用方决定处理
    （GUI 弹错、selftest 记录失败）。
    """
    template = Path(str(template_dwg))
    with tempfile.TemporaryDirectory(prefix="cad_fields_") as td:
        tmp = Path(td)
        t_dxf = dc.get_converter().template_to_dxf(template, tmp, oda)
        return parse_template(t_dxf)


def parse_template_fields(template_dwg: str | Path, oda: str = "") -> list[str]:
    """解析图纸模板，返回按出现顺序去重后的字段名列表。"""
    return collect_fields(parse_template_anchors(template_dwg, oda))


def _extract_task(item: tuple) -> dict:
    """并行 worker：解包 (dxf_path, anchors, point_tolerance, fig_fields) 取值。

    顶层函数（Windows spawn 可 pickle）；单文件异常由 map_files 包装为
    TaskFailed，调用方统一容错。
    """
    f, anchors, point_tol, fig_fields = item
    return extract_by_anchors(
        f, anchors, point_tolerance=point_tol, fig_fields=fig_fields)


def run_pipeline(
    template_dwg: str | Path,
    xlsx_template: str | Path,
    dwg_files: list[str | Path],
    out_path: str | Path,
    oda: str = "",
    out_version: str = "ACAD2018",
    rules: dict | None = None,
    sheet_name: str | None = None,
    log: LogFn = lambda m: None,
    progress: ProgressFn = lambda p: None,
    is_cancelled: Callable[[], bool] = lambda: False,
    template_anchors: list | None = None,
) -> PipelineResult:
    """执行完整流程（模板标记取值）。失败时返回 ok=False 的结果。

    template_dwg : 图纸模板 DWG（[字段名] 占位符 + 矩形区域）
    xlsx_template : 用户提供的表格模板（sheet 自动定位、表头行由占位符
                    字段名反推，列名 = 字段名/页码，必填）
    sheet_name : 指定表格模板使用的 sheet 名（可空；为空时自动定位
                 匹配字段数最多的 sheet）
    dwg_files : 要处理的图纸 DWG/DXF 文件列表
    template_anchors : 已解析的模板锚点（parse_template_anchors 的结果）。
                       提供时跳过模板转换与解析（GUI 预检已解析过，省一次
                       模板 DXF 转换）；None 时在本流程内解析。
    """
    result = PipelineResult()
    rules = rules or {}

    # 1. 校验输入
    template = Path(str(template_dwg))
    if not template.is_file():
        result.error = f"图纸模板 DWG 不存在: {template}"
        return result
    xlsx = Path(str(xlsx_template)) if str(xlsx_template).strip() else None
    if xlsx is None or not xlsx.is_file():
        result.error = "必须提供表格模板（输出目录样式）"
        return result
    files = [Path(str(p)) for p in dwg_files if str(p).strip()]
    files = [p for p in files if p.suffix.lower() in (".dwg", ".dxf")]
    if not files:
        result.error = "请选择要处理的 DWG/DXF 图纸文件"
        return result
    result.total_files = len(files)
    out_path = Path(out_path)
    # 输出路径兼容两种语义：显式 .xlsx 文件名，或目录（自动按表格模板名命名）
    if out_path.suffix.lower() not in (".xlsx", ".xls"):
        tpl_stem = Path(str(xlsx)).stem if str(xlsx).strip() else ""
        out_path = out_path / (f"{tpl_stem}.xlsx" if tpl_stem else "目录.xlsx")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 2. ODA 校验（有 DWG 时需要）
    converter = dc.get_converter()
    oda = converter.resolve(oda)
    has_dwg = template.suffix.lower() == ".dwg" or any(
        p.suffix.lower() == ".dwg" for p in files)
    err = converter.require_for_dwg(has_dwg, oda)
    if err:
        result.error = err
        return result

    # 3. 统一复制到临时目录并转换 DXF（跨目录图纸 + 模板）
    #    先检测重名：同名文件（含大小写不敏感）会互相覆盖/漏处理，直接报错终止
    all_inputs = [template] + files
    try:
        check_duplicate_names(all_inputs)
    except ValueError as ex:
        result.error = str(ex)
        return result
    with tempfile.TemporaryDirectory(prefix="cad_catalog_") as tmp:
        tmp_dir = Path(tmp)
        try:
            for src in all_inputs:
                shutil.copy2(src, tmp_dir / src.name)
        except OSError as ex:
            result.error = f"复制输入文件失败: {ex}"
            return result

        # ODA 要求输出目录与输入目录不同，用独立子目录承接 DXF 产物
        dxf_out = tmp_dir / "_dxf_out"
        dxf_out.mkdir(parents=True, exist_ok=True)

        # 4. 解析模板锚点（模板若为 DWG 先转 DXF）；已传入 template_anchors 时复用
        progress(10)
        if template_anchors is not None:
            anchors = list(template_anchors)
            if not anchors:
                result.error = "模板锚点为空"
                return result
        else:
            try:
                template_dxf = converter.template_to_dxf(
                    template, tmp_dir, oda, subdir="_dxf_out")
            except dc.ODAError as ex:
                result.error = f"模板 DWG 转换失败: {ex}"
                return result
            try:
                anchors = parse_template(template_dxf)
            except Exception as ex:  # noqa: BLE001
                result.error = f"解析模板失败: {ex}"
                return result
        result.fields = collect_fields(anchors)
        fields = result.fields
        log(f"模板解析完成：锚点 {len(anchors)} 个，字段：{'、'.join(fields)}")

        # 5. 图纸转换（DWG → DXF）
        dwg_names = [p.name for p in files if p.suffix.lower() == ".dwg"]
        if dwg_names:
            progress(20)
            log(f"开始转换 {len(dwg_names)} 个 DWG → DXF ...")
            try:
                converter.dwg_to_dxf(oda, tmp_dir, dxf_out, dwg_names)
            except dc.ODAError as ex:
                result.error = f"DWG 转换失败: {ex}"
                return result
        # 图纸产物：DWG 转换到 dxf_out，DXF 源文件直接复制在 tmp_dir
        dxf_files: list[Path] = []
        failed_files: list[str] = []
        for p in files:
            if p.suffix.lower() == ".dwg":
                cand = dxf_out / (p.stem + ".dxf")
            else:
                cand = tmp_dir / (p.stem + ".dxf")
            if cand.is_file():
                dxf_files.append(cand)
            else:
                failed_files.append(p.name)
        result.failed_files = failed_files
        if result.failed_files:
            log(f"警告：{len(result.failed_files)} 个文件转换失败（已跳过）："
                + "、".join(result.failed_files))
        if not dxf_files:
            result.error = "没有任何 DXF 产物，无法继续"
            return result

        # 6. 逐文件按锚点取值（图纸号只从图纸中提取，不做文件名兜底）
        point_tol = _point_tolerance(rules)
        figure_field = str(rules.get("figure_field", "图号"))
        # 图号字段识别：精确匹配优先，其次宽松匹配（配置 "图号" 可命中 "图纸号" 列）；
        # 图号类字段的单点锚点只取距离最近 1 个文字（一个图号位一个值）
        fig_fields = frozenset(
            f for f in fields
            if figure_field and (f == figure_field
                                 or all(ch in f for ch in figure_field)))
        total = len(dxf_files)
        tasks = [(f, anchors, point_tol, fig_fields) for f in dxf_files]
        results: list[dict | None] = [None] * total
        cancelled = {"v": False}

        def _is_cancelled() -> bool:
            if is_cancelled():
                cancelled["v"] = True
            return cancelled["v"]

        done_count = {"v": 0}

        def _on_done(result, idx, item) -> None:
            f = item[0]
            # 并行完成序与提交序不同：进度/日志按「完成序号」编号并单调推进
            # （results 仍按提交序 idx 落位，entries 顺序不受影响）
            done_count["v"] += 1
            progress(20 + int(60 * done_count["v"] / total))
            log(f"  处理 [{done_count['v']}/{total}] {f.name}")
            if isinstance(result, TaskFailed):
                log(f"     取值失败：{result.error}")
                results[idx] = {}
            else:
                results[idx] = result

        map_files(_extract_task, tasks, is_cancelled=_is_cancelled,
                  on_done=_on_done)
        if cancelled["v"]:
            result.error = "已取消"
            return result
        # 保持用户选择的文件顺序构建 entries（map_files 结果按提交顺序返回）
        entries = [FileEntry(filename=dxf_files[i].stem, values=results[i])
                   for i in range(total) if results[i] is not None]

        # 7. 构建目录并输出
        progress(80)
        try:
            cat = build_file_catalog(
                entries, fields,
                data_rows_per_page=int(rules.get("data_rows_per_page", 50)),
                cover_pages=int(rules.get("cover_pages", 1)),
            )
            result.na_rows = cat.na_rows
            result.total_pages = cat.total_pages
            catalog_excel_writer.write_catalog_from_template(
                cat, xlsx, out_path, sheet_name=sheet_name)
        except Exception as ex:  # noqa: BLE001 - 输出阶段（表头反推/写入）失败按流程错误返回
            result.error = f"生成目录失败: {ex}"
            return result
        result.out_path = out_path
        log(f"目录已生成：{out_path}")
        log(f"完成：{len(entries)} 张图纸，"
            f"{cat.na_rows} 张无值(NA)，共 {cat.total_pages} 页")
        progress(100)

    result.ok = True
    return result
