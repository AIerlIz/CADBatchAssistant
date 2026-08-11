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

# Python surrogateescape 残留：R2000+ DXF 固定按 UTF-8 解码，ANSI/非 UTF-8
# 内容解码失败时不抛异常，而是把每个字节映射到 U+DC80-U+DCFF
_SURROGATE_MIN = 0xDC80
_SURROGATE_MAX = 0xDCFF

# 兜底编码（声明代码页无法解析时按常见中文编码尝试）
_FALLBACK_ENCODINGS = ("gbk", "gb18030", "utf-8")

# 文件头 $DWGCODEPAGE（如 ANSI_936 / ANSI_950 / ANSI_1252）
_CODEPAGE_RE = re.compile(rb"\$DWGCODEPAGE\r\n\s*3\r\nANSI_(\d+)")


def _declared_encoding(src: str) -> str | None:
    """读取 DXF 头部声明的代码页对应的 Python 编码；无声明/不可映射返回 None。"""
    try:
        with open(src, "rb") as f:
            head = f.read(4096)
    except OSError:
        return None
    m = _CODEPAGE_RE.search(head)
    if not m:
        return None
    try:
        from ezdxf.tools.codepage import codepage_to_encoding
    except ImportError:
        return None
    return codepage_to_encoding.get(m.group(1).decode())


def _candidate_encodings(src: str) -> list[str]:
    """重读候选编码：声明代码页优先，再按常见中文编码兜底。"""
    encodings: list[str] = []
    declared = _declared_encoding(src)
    if declared:
        encodings.append(declared)
    for enc in _FALLBACK_ENCODINGS:
        if enc not in encodings:
            encodings.append(enc)
    return encodings


def decode_text(s: str) -> str:
    """把 \\U+XXXX 转义序列解码为真实 Unicode 字符。"""
    if "\\U+" not in s:
        return s
    return _U_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)


@dataclass
class ReplaceRule:
    """一条查找替换规则。

    regex=False（默认）时 find/replace 均按普通文本字面匹配（元字符自动转义）；
    regex=True 时 find 按正则表达式、replace 支持 \\1 反向引用。
    """

    find: str
    replace: str = ""
    case_sensitive: bool = True
    regex: bool = False


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
    """对单个文本依次应用所有替换规则，返回 (新文本, 替换次数)。

    - rule.regex=False（默认）：find/replace 按普通文本字面处理（自动转义
      正则元字符，如半角括号、点、星号；替换文本中的 \\1、反斜杠原样输出）
    - rule.regex=True：find 按正则表达式解释，replace 支持 \\1 反向引用
    - 非法正则跳过该规则（GUI 已提前拦截，此处兜底）
    """
    new_text = text
    total = 0
    for rule in rules:
        if not rule.find:
            continue  # 空模式会匹配任意位置，必须排除
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        try:
            if rule.regex:
                pattern = re.compile(rule.find, flags)
            else:
                pattern = re.compile(re.escape(rule.find), flags)
        except re.error:
            continue  # 非法正则：跳过该规则，不中断批次
        try:
            if rule.regex:
                new_text, count = pattern.subn(rule.replace, new_text)
            else:
                # 普通文本：替换内容按字面输出，避免 \\1 / \\ 被正则转义解释
                new_text, count = pattern.subn(lambda _m: rule.replace, new_text)
        except re.error:
            continue  # 替换模板引用无效组（如 \\1 无对应捕获组）：跳过该规则
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


def _has_undecoded_surrogates(doc) -> bool:
    """文档文字实体中是否存在 surrogateescape 残留（解码失败字节）。"""
    for block in doc.blocks:
        for e in block:
            t = e.dxftype()
            if t not in TEXT_TYPES:
                continue
            text = e.text if t == "MTEXT" else e.dxf.text
            if any(_SURROGATE_MIN <= ord(ch) <= _SURROGATE_MAX for ch in text):
                return True
    return False


def read_doc(src: str | Path) -> ezdxf.document.Drawing:
    """读取 DXF，兼容任意 AutoCAD 代码页。

    R2000+ 的 DXF 由 ezdxf 固定按 UTF-8 解码（$DWGCODEPAGE 只写入
    doc.encoding，不参与实际解码）；非 UTF-8 内容（老图纸 / ANSI 代码页）
    解码失败时不抛异常，而是产生 surrogateescape 残留（U+DC80-U+DCFF），
    导致中文无法匹配。检测到残留后按文件头声明的代码页编码重读
    （ANSI_936→gbk、ANSI_950→big5、ANSI_932→shift_jis、ANSI_125x→cp125x 等），
    声明缺失/不可映射时按常见中文编码兜底。
    """
    src = str(src)
    try:
        doc = ezdxf.readfile(src)
    except ezdxf.DXFStructureError:
        raise
    except Exception as first_err:
        for enc in _candidate_encodings(src):
            try:
                return ezdxf.readfile(src, encoding=enc)
            except Exception:
                continue
        raise first_err
    if _has_undecoded_surrogates(doc):
        for enc in _candidate_encodings(src):
            try:
                retry = ezdxf.readfile(src, encoding=enc)
            except Exception:
                continue
            if not _has_undecoded_surrogates(retry):
                return retry
    return doc


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
