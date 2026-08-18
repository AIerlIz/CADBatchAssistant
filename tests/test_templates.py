"""core.templates 纯文件操作单测（模板库只存占位符 meta JSON）。

覆盖模板库目录定位、枚举（仅 meta JSON 条目）与删除；
templates_dir 用 monkeypatch 隔离到 tmp_path，不触碰真实软件目录 templates/。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadbatchassistant.core.common import templates


def _patch_templates_dir(monkeypatch, base: Path) -> None:
    monkeypatch.setattr(
        templates, "templates_dir", lambda cat: base / "templates" / cat)


def _write_meta(d: Path, name: str, source: str | None = None) -> None:
    """写入一条占位符 meta JSON（source 缺省取模板名）。"""
    data = {"version": 1, "source": source or name, "fields": []}
    (d / (name + ".json")).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_template_path(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.template_path("fill", "a.dwg") == \
        tmp_path / "templates" / "fill" / "a.dwg"


def test_meta_file_for(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.meta_file_for("fill", "a.dwg") == \
        tmp_path / "templates" / "fill" / "a.dwg.json"


def test_list_templates_from_meta(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _write_meta(d, "图框.dwg", source="图框.dwg")
    _write_meta(d, "tpl.dxf", source="tpl.dxf")
    (d / "c.txt").write_text("x")
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == ["tpl.dxf", "图框.dwg"]


def test_list_templates_bad_meta_falls_back_to_name(
    monkeypatch, tmp_path: Path
) -> None:
    """meta JSON 损坏/缺 source 时回退用文件名（去 .json）作模板名。"""
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    (d / "broken.dwg.json").write_text("not json", encoding="utf-8")
    (d / "nosource.dxf.json").write_text('{"version": 1}', encoding="utf-8")
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == ["broken.dwg", "nosource.dxf"]


def test_list_templates_missing_dir(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == []


def test_remove_template_removes_meta(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _write_meta(d, "a.dwg", source="a.dwg")
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.remove_template("fill", "a.dwg")
    assert not (d / "a.dwg.json").exists()


def test_remove_template_leaves_legacy_files_untouched(
    monkeypatch, tmp_path: Path
) -> None:
    """移除 meta 时不再触碰旧库遗留的原始文件（模板库只管理 meta 条目）。"""
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    f = d / "a.dwg"
    f.write_text("x")  # 旧库遗留原文件（无 meta 对应，不再枚举）
    _write_meta(d, "a.dwg", source="a.dwg")
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.remove_template("fill", "a.dwg")
    assert not (d / "a.dwg.json").exists()
    assert f.exists()  # 原文件不在管理范围内，保留


def test_remove_template_by_source_when_filename_detached(monkeypatch, tmp_path):
    """source 与文件名脱钩（手改 JSON 的 source 字段）→ remove 按枚举名扫目录删除。"""
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _write_meta(d, "renamed.dwg", source="原图.dwg")
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == ["原图.dwg"]
    templates.remove_template("fill", "原图.dwg")  # 按枚举名（source）删除
    assert not (d / "renamed.dwg.json").exists()


def test_remove_template_missing_raises(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _patch_templates_dir(monkeypatch, tmp_path)
    with pytest.raises(OSError):
        templates.remove_template("fill", "nope.dwg")


def test_remove_template_rejects_path_traversal(monkeypatch, tmp_path: Path) -> None:
    """路径穿越防护：含分隔符/越界的模板名被拒绝，不做任何删除。

    模板名可能来自被篡改的 meta JSON 的 source 字段（模板库目录本地可写），
    拼接删除前必须校验，防止删除操作逃出模板库目录删任意文件。
    """
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _write_meta(d, "a.dwg", source="a.dwg")
    victim = tmp_path / "victim.dwg"
    victim.write_text("x")
    _patch_templates_dir(monkeypatch, tmp_path)
    for bad in ("../victim.dwg", "..\\victim.dwg", "sub/a.dwg", "..", "."):
        with pytest.raises(ValueError):
            templates.remove_template("fill", bad)
    # 越界目标与库内合法条目均未被删除
    assert victim.exists()
    assert (d / "a.dwg.json").exists()


# ---------------- 编辑：load/save_template_json ----------------

def test_save_load_template_json_roundtrip(monkeypatch, tmp_path: Path) -> None:
    """save_template_json → load_template_json 往返一致（自动补 version/source）。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "fill", "a.dwg", {"placeholders": [{"text": "图号", "height": 3.0}]}
    )
    data = templates.load_template_json("fill", "a.dwg")
    assert data is not None
    assert data["version"] == 1
    assert data["source"] == "a.dwg"
    assert data["placeholders"][0]["text"] == "图号"
    out = tmp_path / "templates" / "fill" / "a.dwg.json"
    assert "图号" in out.read_text(encoding="utf-8")  # ensure_ascii=False


def test_load_template_json_protects_against_traversal(
    monkeypatch, tmp_path: Path
) -> None:
    """编辑读取同样拒绝路径穿越（防越界读模板库之外的 JSON）。"""
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    _patch_templates_dir(monkeypatch, tmp_path)
    for bad in ("../victim.json", "..\\victim", "sub/a.dwg", "..", "."):
        with pytest.raises(ValueError):
            templates.load_template_json("fill", bad)


def test_load_template_json_bad_json_returns_none(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    (d / "broken.dwg.json").write_text("not json", encoding="utf-8")
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.load_template_json("fill", "broken.dwg") is None


def test_load_template_json_missing_returns_none(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.load_template_json("fill", "nope.dwg") is None


# ---------------- 编辑：editable_rows / merge_editable_rows ----------------

def test_editable_rows_catalog(monkeypatch, tmp_path: Path) -> None:
    """目录模板：从 anchors 提取可编辑行（仅含可编辑列）。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "catalog",
        "a.dwg",
        {
            "fields": ["图号"],
            "anchors": [{
                "field": "图号", "is_area": False,
                "min_x": 1, "min_y": 2, "max_x": 3, "max_y": 4,
                "point_x": 2, "point_y": 3,
            }],
        },
    )
    data = templates.load_template_json("catalog", "a.dwg")
    rows = templates.editable_rows("catalog", data)
    assert rows == [{
        "field": "图号", "is_area": False,
        "min_x": 1, "min_y": 2, "max_x": 3, "max_y": 4,
        "point_x": 2, "point_y": 3,
    }]


def test_editable_rows_fill_defaults(monkeypatch, tmp_path: Path) -> None:
    """填表模板：缺键时补类型默认值；entity_desc 不进入可编辑行。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "fill", "t.dxf", {"placeholders": [{"text": "图号"}]}
    )
    data = templates.load_template_json("fill", "t.dxf")
    row = templates.editable_rows("fill", data)[0]
    assert row["text"] == "图号"
    assert row["layer"] == ""
    assert row["x"] == 0.0
    assert row["halign"] == 0


def test_merge_editable_rows_catalog_regenerates_fields(
    monkeypatch, tmp_path: Path
) -> None:
    """目录模板：编辑锚点 field 后，fields 按编辑后的字段名重新生成（去重保序）。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "catalog",
        "a.dwg",
        {
            "fields": ["图号"],
            "anchors": [
                {"field": "图号", "is_area": False, "point_x": 1.0, "point_y": 2.0},
                {"field": "图号", "is_area": False, "point_x": 3.0, "point_y": 4.0},
            ],
        },
    )
    data = templates.load_template_json("catalog", "a.dwg")
    rows = templates.editable_rows("catalog", data)
    rows[0]["field"] = "编号"
    merged = templates.merge_editable_rows("catalog", data, rows)
    assert [a["field"] for a in merged["anchors"]] == ["编号", "图号"]
    assert merged["fields"] == ["编号", "图号"]


def test_merge_editable_rows_fill_keeps_entity_desc(
    monkeypatch, tmp_path: Path
) -> None:
    """填表模板：编辑仅改动可编辑列，entity_desc 等字段按原样保留。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "fill",
        "t.dxf",
        {
            "placeholders": [{
                "text": "图号", "height": 3.0, "entity_desc": {"dxftype": "TEXT"},
            }]
        },
    )
    data = templates.load_template_json("fill", "t.dxf")
    rows = templates.editable_rows("fill", data)
    rows[0]["height"] = "5.5"  # 字符串坐标宽容转换
    merged = templates.merge_editable_rows("fill", data, rows)
    ph = merged["placeholders"][0]
    assert ph["height"] == 5.5
    assert ph["entity_desc"] == {"dxftype": "TEXT"}


def test_merge_editable_rows_invalid_numeric_rejected(
    monkeypatch, tmp_path: Path
) -> None:
    """编辑行数值非法 → merge 抛 ValueError。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json("fill", "t.dxf", {"placeholders": [{"text": "a"}]})
    data = templates.load_template_json("fill", "t.dxf")
    with pytest.raises(ValueError):
        templates.merge_editable_rows(
            "fill", data, [{"height": "abc"}]
        )


def test_edit_columns_cover_both_categories() -> None:
    """两个模板库分类都定义了可编辑列（结构完整）。"""
    for cat in ("fill", "catalog"):
        cols = templates.TEMPLATE_EDIT_COLUMNS[cat]
        assert cols
        for key, header, kind in cols:
            assert key and header
            assert kind in ("str", "float", "int", "bool")


def test_save_template_json_forces_version_source(monkeypatch, tmp_path: Path) -> None:
    """用户手改 meta 里的 version/source 键不能覆盖入库的 version/source。"""
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.save_template_json(
        "fill", "a.dwg", {"version": 99, "source": "被篡改.dwg", "placeholders": []}
    )
    data = templates.load_template_json("fill", "a.dwg")
    assert data["version"] == 1  # 强制为合法版本
    assert data["source"] == "a.dwg"  # 保持模板名绑定
    assert data["placeholders"] == []


def test_coerce_edit_value_types() -> None:
    """编辑值类型解析（str/bool/float/int）的单一实现行为。"""
    # str
    assert templates.coerce_edit_value("str", None) == ""
    assert templates.coerce_edit_value("str", "x") == "x"
    # bool：是/否/1/0/true/false/空串
    assert templates.coerce_edit_value("bool", True) is True
    for yes in ("是", "1", "true", "yes", "TRUE"):
        assert templates.coerce_edit_value("bool", yes) is True
    for no in ("否", "0", "false", "no", ""):
        assert templates.coerce_edit_value("bool", no) is False
    with pytest.raises(ValueError):
        templates.coerce_edit_value("bool", "maybe")
    # float / int：数字或数字字符串宽容转换
    assert templates.coerce_edit_value("float", "5.5") == 5.5
    assert templates.coerce_edit_value("float", 3) == 3.0
    assert templates.coerce_edit_value("int", "2") == 2
    assert templates.coerce_edit_value("int", "2.9") == 2
    with pytest.raises(ValueError):
        templates.coerce_edit_value("float", "abc")
    with pytest.raises(ValueError):
        templates.coerce_edit_value("float", "")
