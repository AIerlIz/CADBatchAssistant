"""按模板占位规格 + 数据表.xlsx 填充 修改前 DXF 的标题栏值格。

流程（对每张图）：
1. 加载 before DXF（ACAD2004）
2. 对 specs 中每个字段：
   - 用 value_rule 从 xlsx 值生成显示文本
   - 目标位置已有相同文本实体 → 跳过（避免重复）
   - 否则在规格位置新建 TEXT（图层/坐标/字高/样式/对齐）；
     图纸预置的压力单位 'barg' 不删除，与填入值共存显示
3. 保存为 filled DXF

入口为 fill_all（由 fill_pipeline 调用，多进程并行）。
"""

from __future__ import annotations

import os

from ezdxf import const

from cadbatchassistant.core.common.parallel import TaskFailed, map_files
from cadbatchassistant.core.common.text_replace import read_doc
from cadbatchassistant.core.fill.fill_parse_xlsx import load_xlsx


def make_text(val: str) -> str:
    """数据表值原样填入（不分类加工）。"""
    return val.strip()


def _entity_text(e) -> str:
    """取 TEXT/MTEXT 实体的纯文本内容（用于已有内容比较）。

    - TEXT：内容在 e.dxf.text（ezdxf 的 Text 类无 .text 属性，
      getattr(e, "text", "") 恒为空——旧实现导致 TEXT 检测失效）；
    - MTEXT：e.dxf.text 是含格式码（如 \\P 换行）的原始串，用
      plain_text() 取去格式码后的文本，使同值比较不受格式码影响。
    取不到返回 ""。
    """
    try:
        if e.dxftype() == "MTEXT":
            return e.plain_text()
        return str(getattr(e.dxf, "text", "") or "")
    except Exception:  # noqa: BLE001 - 兜底空文本
        return ""


# 重建占位符实体时排除的文档结构属性（非格式，由 ezdxf 生成）
_DESC_SKIP_KEYS = frozenset(("text", "handle", "owner"))


def entity_to_desc(e) -> dict:
    """占位符实体 → 可 pickle 的轻量描述（供多进程并行任务使用）。

    原实现把 ezdxf 实体对象直接放进任务 item：pickle 时连同其 doc 引用
    序列化整份模板文档（每张图一份文档副本，内存/传输放大）。
    dxfattribs() 是纯数据（坐标/字高/图层/样式/颜色等），不含文档引用；
    源图层/样式定义一并快照，供目标文档缺失时补齐（避免悬空引用）。
    """
    layer = getattr(e.dxf, "layer", "") or ""
    style = getattr(e.dxf, "style", "") or ""
    layer_attribs: dict | None = None
    style_attribs: dict | None = None
    doc = getattr(e, "doc", None)
    if doc is not None:
        if layer and layer in doc.layers:
            layer_attribs = dict(doc.layers.get(layer).dxfattribs())
        if style and style in doc.styles:
            style_attribs = dict(doc.styles.get(style).dxfattribs())
    return {
        "dxftype": e.dxftype(),
        "attribs": dict(e.dxfattribs()),
        "layer_attribs": layer_attribs,
        "style_attribs": style_attribs,
    }


def _find_texts_in(ents, x: float, y: float, tol: float = 0.01):
    """在单图层实体列表（已过滤 TEXT/MTEXT）中按坐标容差查找。"""
    for e in ents:
        ins = e.dxf.insert
        if abs(ins[0] - x) < tol and abs(ins[1] - y) < tol:
            yield e


def fill_one(before_dxf: str, out_dxf: str, spec: dict, row: dict) -> list[str]:
    doc = read_doc(before_dxf)
    msp = doc.modelspace()
    log: list[str] = []
    # 一次遍历收集 TEXT/MTEXT 并按图层分组：避免每个字段都全遍历 modelspace
    # （字段多 + 实体多时从 O(字段×实体) 降为 O(实体 + 字段×单层实体)）
    by_layer: dict[str, list] = {}
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT"):
            by_layer.setdefault(e.dxf.layer, []).append(e)
    # 注意：不删除处理图纸压力格的 'barg' 单位占位——
    # 值原样填入（不含单位），'barg' 作为图纸预置单位与值共存显示。

    # 按规格填值
    for layer, fields in spec.items():
        layer_ents = by_layer.get(layer, [])
        for field, fspec in fields.items():
            val = row.get(field, "")
            # xlsx 值为空：占位符的值也置空（仍克隆占位符）
            text = make_text(val) if val.strip() else ""
            x, y = fspec["x"], fspec["y"]

            if text:
                # 排除空文本与压力格单位 'barg'（均不算已占位内容，允许写入值）
                existing = [
                    e
                    for e in _find_texts_in(layer_ents, x, y)
                    if _entity_text(e).strip() not in ("", "barg")
                ]
                if existing:
                    same = any(
                        "".join(_entity_text(e).split()) == "".join(text.split())
                        for e in existing
                    )
                    if same:
                        log.append(f"跳过 {field}（已存在 {text!r}）")
                    else:
                        cur = _entity_text(existing[0])
                        log.append(f"跳过 {field}（位置已有内容，不覆盖：{cur!r}）")
                    continue

            ent = fspec.get("entity")
            if ent is not None:
                # 统一为轻量实体描述：内存实体对象（串行/直接调用）先转换，
                # 描述 dict（并行任务）直接使用。描述不携带文档引用 →
                # 任务 item 可 pickle，且不随每张图序列化整份模板文档。
                desc = ent if isinstance(ent, dict) else entity_to_desc(ent)
                # 重建占位符实体（保留模板全部 dxfattribs 格式），只替换文字
                attribs = {
                    k: v for k, v in desc["attribs"].items() if k not in _DESC_SKIP_KEYS
                }
                new = (
                    msp.add_mtext("", dxfattribs=attribs)
                    if desc["dxftype"] == "MTEXT"
                    else msp.add_text("", dxfattribs=attribs)
                )
                # 校验目标文档存在同名图层/样式，缺失则补齐（避免悬空引用）
                ent_layer = new.dxf.layer or ""
                if ent_layer and ent_layer not in doc.layers:
                    src_layer = desc.get("layer_attribs")
                    if src_layer:
                        # LayerTable.add 的 color/linetype/lineweight 是关键字
                        # 参数，dxfattribs 里的同名键会被默认值覆盖 → 必须
                        # 经关键字传入，补齐图层才能保留模板的显示属性
                        doc.layers.add(
                            ent_layer,
                            color=src_layer.get("color", const.BYLAYER),
                            linetype=src_layer.get("linetype", "Continuous"),
                            lineweight=src_layer.get(
                                "lineweight", const.LINEWEIGHT_BYLAYER
                            ),
                            dxfattribs={
                                k: v
                                for k, v in src_layer.items()
                                if k in ("true_color", "plot")
                            },
                        )
                    else:
                        doc.layers.add(ent_layer)
                style = getattr(new.dxf, "style", "") or ""
                if style and style not in doc.styles:
                    src_style = desc.get("style_attribs") or {}
                    # ezdxf 的 styles.add 要求 font 为关键字参数（不能经
                    # dxfattribs 传入，否则缺 font 抛 TypeError）
                    doc.styles.add(
                        style,
                        font=src_style.get("font") or "txt",
                        dxfattribs={
                            k: v
                            for k, v in src_style.items()
                            if k in ("height", "width", "oblique")
                        },
                    )
                new.dxf.text = text
                note = "（xlsx 值为空，置空）" if not text else "（替换占位符）"
                log.append(f"填写 {field} = {text!r} {note} [{layer}]")
                continue

            attribs = {
                "layer": layer,
                "insert": (x, y, 0.0),
                "height": fspec["height"],
                "style": fspec["style"],
                "halign": fspec["halign"],
                "valign": fspec["valign"],
            }
            if fspec["valign"] != 0 or fspec["halign"] != 0:
                attribs["align_point"] = (x, y, 0.0)
            msp.add_text(text, dxfattribs=attribs)
            log.append(f"填写 {field} = {text!r} @({x},{y}) [{layer}]")

    doc.saveas(out_dxf)
    log.append(f"保存 {out_dxf}")
    return log


def _fill_one_task(item: tuple) -> tuple:
    """并行 worker：解包任务参数执行 fill_one，返回 (stem, log)。

    item = (stem, before_dxf, out_dxf, spec, row)。顶层函数（Windows spawn 可 pickle）。
    """
    stem, before, out, spec, row = item
    return stem, fill_one(before, out, spec, row)


def fill_all(
    before_dxf_dir: str,
    out_dxf_dir: str,
    xlsx: str,
    specs: dict,
    emit=print,
    progress=None,
    match_col: str | None = None,
    sheet: str | None = None,
    cancel=None,
    data: dict | None = None,
) -> tuple[list[str], list[str]]:
    """批量填充：对 specs 中每张图执行 fill_one。

    文件相互独立（readfile → 填值 → saveas），经 map_files 多进程并行处理
    （文件数少时自动回退串行）；单张图失败不中断，记录后继续处理其余图纸。
    cancel   : 可选 threading.Event；置位时停止（当前处理中的图完成后停止，
               未开始的图不再处理）。
    progress : 可选回调 progress(index, total)，每处理一张图（成败均）调用一次，
               index 为图纸在全部图纸中的顺序号（含被跳过者），与串行实现一致。
    match_col: 数据表中图纸名列（None 默认第一列）。
    sheet    : 数据表中工作表名（None 默认第一个）。
    data     : 已读取的数据 {图纸名: {列名: 值}}（调用方已 load 一次时传入，
               避免流水线整表二次解析）；None 时按 xlsx 现场读取。
    返回 (failed, skipped)：failed 为处理失败的图纸名；
    skipped 为"没有产出"的图纸名（不在数据表中 / 缺少 before DXF），
    调用方不得把它们当作成功（否则输出阶段会因产物缺失报错或挂起等待）。
    """
    if data is None:
        data = load_xlsx(xlsx, match_col, sheet)
    stems = sorted(specs)
    failed: list[str] = []
    skipped: list[str] = []
    # 预筛：不在数据表 / 缺 before DXF → skipped（不参与并行处理）
    tasks: list[tuple] = []
    order: dict[str, int] = {}
    # 进度按提交序单调推进：并行完成序与提交序不同，直接按完成序回调
    # 会让进度条来回跳动；这里标记完成位 + 游标推进，保证 progress 的
    # index 严格递增，且被跳过（skipped）的图纸同样计入（与串行一致）。
    done_flags = [False] * (len(stems) + 1)  # 1-based 提交位
    cursor = 1

    def _advance_progress() -> None:
        nonlocal cursor
        while cursor <= len(stems) and done_flags[cursor]:
            if progress:
                progress(cursor, len(stems))
            cursor += 1

    for i, stem in enumerate(stems, 1):
        order[stem] = i
        if stem not in data:
            emit(f"[WARN] {stem} 不在 xlsx 中，跳过")
            skipped.append(stem)
            done_flags[i] = True
            _advance_progress()
            continue
        before = os.path.join(before_dxf_dir, stem + ".dxf")
        out = os.path.join(out_dxf_dir, stem + ".dxf")
        if not os.path.isfile(before):
            emit(f"[WARN] 缺少 before DXF: {before}")
            skipped.append(stem)
            done_flags[i] = True
            _advance_progress()
            continue
        tasks.append((stem, before, out, specs[stem], data[stem]))

    cancelled_reported = {"v": False}

    def _is_cancelled() -> bool:
        c = cancel is not None and cancel.is_set()
        if c and not cancelled_reported["v"]:
            cancelled_reported["v"] = True
            emit("[WARN] 收到取消请求，停止填表")
        return c

    def _on_done(result, _index, item) -> None:
        stem = item[0]
        if isinstance(result, TaskFailed):
            emit(f"[ERROR] {stem} 处理失败：{result.error}")
            failed.append(stem)
        else:
            emit(f"===== {stem}")
            for line in result[1]:
                emit("  " + line)
        done_flags[order[stem]] = True
        _advance_progress()

    map_files(
        _fill_one_task,
        tasks,
        is_cancelled=_is_cancelled,
        on_done=_on_done,
        reuse_pool=True,  # 跨块/跨阶段复用共享进程池（省 spawn 开销）
    )
    ok = len(stems) - len(failed) - len(skipped)
    emit(
        f"      完成 {ok}/{len(stems)} 张，"
        + (f"失败 {len(failed)} 张：{', '.join(failed)}；" if failed else "")
        + (f"跳过 {len(skipped)} 张：{', '.join(skipped)}" if skipped else "")
        + ("全部成功" if not failed and not skipped else "")
    )
    return failed, skipped
