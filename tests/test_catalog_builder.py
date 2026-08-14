"""catalog_builder 页码/NA/行数计算单测（目录助手）。"""

from cadbatchassistant.core.catalog_builder import (
    FileEntry,
    build_file_catalog,
    entry_rows,
)


def _entry(filename, **values):
    return FileEntry(filename=filename, values=values)


def test_na_rows_count():
    entries = [
        _entry("A", 图号=["D-1"], 管段=["P-1"]),  # 全有 → 不 NA
        _entry("B", 图号=["D-2"]),  # 管段缺失 → NA
        _entry("C", 管段=[]),  # 图号缺失 → NA
    ]
    cat = build_file_catalog(entries, ["管段", "图号"])
    assert cat.na_rows == 2
    assert cat.page_count == 1
    assert cat.total_pages == 1 + 1 + 3  # cover(1) + P(1) + 文件(3)


def test_page_count_ceil():
    # 3 个文件各 20 行 → 60 行,每页 50 → 2 页
    entries = [_entry(f"F{i}", 管段=[f"P{i}-{j}" for j in range(20)]) for i in range(3)]
    cat = build_file_catalog(entries, ["管段"], data_rows_per_page=50)
    assert cat.page_count == 2
    assert cat.total_pages == 1 + 2 + 3


def test_entry_rows_max_value_count():
    e = _entry("A", 管段=["P1", "P2", "P3"], 图号=["D-1"])
    assert entry_rows(e, ["管段", "图号"]) == 3


def test_entry_rows_min_one():
    e = _entry("B")
    assert entry_rows(e, ["管段", "图号"]) == 1


def test_build_file_catalog_fields_order():
    entries = [_entry("A", 图号=["D-1"], 管段=["P-1"])]
    cat = build_file_catalog(entries, ["管段", "图号"])
    assert cat.fields == ["管段", "图号"]
    assert cat.entries[0].values["图号"] == ["D-1"]
