"""目录数据构建（文件粒度 + 模板动态列，目录助手）。

每个 DWG 文件 = 一个目录条目；输出列 = 模板占位符字段名（出现顺序）+ 页码列；
每图行数 = 多值字段最大取值数（至少 1 行）；无值字段填 NA；页码每文件一页。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

NA = "NA"


@dataclass
class FileEntry:
    """一张图纸（一个 DWG 文件）的取值结果。"""

    filename: str                           # 图纸文件名（去扩展名）
    values: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Catalog:
    """完整目录数据（文件粒度）。"""

    fields: list[str] = field(default_factory=list)   # 列顺序（模板字段名）
    entries: list[FileEntry] = field(default_factory=list)
    page_count: int = 1                    # 目录打印页数 P
    total_pages: int = 1                   # 总页数 = 封皮 + P + 文件数
    na_rows: int = 0                       # 无任何值的条目数（NA 行）


def build_file_catalog(
    entries: list[FileEntry],
    fields: list[str],
    data_rows_per_page: int = 50,
    cover_pages: int = 1,
) -> Catalog:
    """按文件聚合目录数据并计算页码。

    - fields：列顺序（模板字段名，按模板出现顺序去重）
    - 每图行数 = max(各字段值数, 1)；无值字段填 NA
    - 页码：每个文件一页，页码 = cover_pages + P + 文件序（1 基）
    """
    catalog = Catalog(fields=list(fields), entries=entries)

    # NA 行：存在任一字段无值（该字段将填 NA）的条目数
    for e in entries:
        if any(not e.values.get(f) for f in fields):
            catalog.na_rows += 1

    # 页码：目录打印页数 P = ceil(总行数 / 每页数据行数)
    total_rows = sum(
        max((len(e.values.get(f, [])) for f in fields), default=0) or 1
        for e in entries
    )
    per_page = max(1, data_rows_per_page or 50)
    catalog.page_count = max(1, math.ceil(total_rows / per_page))

    # 页码由输出层按文件序计算（cover_pages + P + i + 1）
    catalog.total_pages = cover_pages + catalog.page_count + len(entries)
    return catalog


def entry_rows(entry: FileEntry, fields: list[str]) -> int:
    """该文件的输出行数：多值字段最大取值数，至少 1 行。"""
    return max((len(entry.values.get(f, [])) for f in fields), default=0) or 1
