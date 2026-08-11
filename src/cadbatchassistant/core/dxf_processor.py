"""DXF 文字查找替换核心模块。

遍历 DXF 文档中的 TEXT / MTEXT / ATTRIB / ATTDEF 实体（含块定义、INSERT 附属属性），
按规则查找替换文字，保留图层、坐标、样式与 MTEXT 格式码；
处理 R12 的 \\U+XXXX Unicode 转义（读时解码，写时由 ezdxf 自动转义）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf

# 直接携带文字的实体类型
TEXT_TYPES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")

# R12 / 代码页无法表示时 AutoCAD 使用 \\U+XXXX 转义
_U_ESCAPE_RE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def decode_text(s: str) -> str:
    """把 \\U+XXXX 转义序列解码为真实 Unicode 字符。"""
    if "\\U+" not in s:
        return s
    return _U_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


@dataclass
class ReplaceRule:
    """一条查找替换规则。"""

    find: str
    replace: str = ""
    case_sensitive: bool = True


@dataclass
class FileResult:
    """单个文件的处理结果。"""

    src: str
    dst: str | None
    status: str = "ok"  # ok | skipped | error
    error: str = ""
    replaced_total: int = 0
    per_type: dict[str, int] = field(default_factory=dict)


def apply_rules(text: str, rules: list[ReplaceRule]) -> tuple[str, int]:
    """对单个文本依次应用所有正则替换规则，返回 (新文本, 替换次数)。

    - rule.find 按正则表达式解释
    - rule.replace 按正则替换语义（支持 \\1 反向引用）
    - 非法正则跳过该规则（GUI 已提前拦截，此处兜底）
    """
    new_text = text
    total = 0
    for rule in rules:
        if not rule.find:
            continue  # 空模式会匹配任意位置，必须排除
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(rule.find, flags)
        except re.error:
            continue  # 非法正则：跳过该规则，不中断批次
        try:
            new_text, count = pattern.subn(rule.replace, new_text)
        except re.error:
            continue  # 替换模板引用无效组（如 \1 无对应捕获组）：跳过该规则
        total += count
    return new_text, total


def _get_text(e) -> str:
    if e.dxftype() == "MTEXT":
        return e.text  # 含格式码的原始文本，替换纯文字部分不破坏格式
    return e.dxf.text


def _set_text(e, s: str) -> None:
    if e.dxftype() == "MTEXT":
        e.text = s
    else:
        e.dxf.text = s


def iter_text_entities(doc):
    """遍历文档中所有可替换的文字实体（模型空间/图纸空间/块定义/INSERT 属性）。"""
    for block in doc.blocks:
        for e in block:
            t = e.dxftype()
            if t in TEXT_TYPES:
                yield e
            elif t == "INSERT":
                for attrib in e.attribs:
                    yield attrib


def read_doc(src: str | Path) -> ezdxf.document.Drawing:
    """读取 DXF，编码自动检测失败时回退尝试常见中文编码。"""
    src = str(src)
    try:
        return ezdxf.readfile(src)
    except ezdxf.DXFStructureError:
        raise
    except Exception as first_err:
        for enc in ("gbk", "gb18030", "utf-8"):
            try:
                return ezdxf.readfile(src, encoding=enc)
            except Exception:
                continue
        raise first_err


def process_dxf_file(
    src: str | Path,
    dst: str | Path | None,
    rules: list[ReplaceRule],
    dry_run: bool = False,
) -> FileResult:
    """处理单个 DXF 文件：查找替换文字并保存（dry_run 时只统计不保存）。"""
    src = Path(src)
    result = FileResult(src=str(src), dst=str(dst) if dst else None)

    try:
        doc = read_doc(src)
    except Exception as ex:  # noqa: BLE001 - 逐个文件容错，错误进结果不中断批次
        result.status = "error"
        result.error = f"读取失败: {ex}"
        return result

    for e in iter_text_entities(doc):
        t = e.dxftype()
        raw = _get_text(e)
        decoded = decode_text(raw)
        new_text, count = apply_rules(decoded, rules)
        if count:
            result.replaced_total += count
            result.per_type[t] = result.per_type.get(t, 0) + count
            if not dry_run:
                _set_text(e, new_text)

    if dry_run:
        return result

    if dst is None:
        result.status = "error"
        result.error = "未指定输出路径"
        return result

    dst = Path(dst)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        doc.saveas(dst)
        result.dst = str(dst)
    except Exception as ex:  # noqa: BLE001
        result.status = "error"
        result.error = f"保存失败: {ex}"
    return result
