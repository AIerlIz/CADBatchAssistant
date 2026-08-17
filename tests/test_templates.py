"""core.templates 纯文件操作单测（模板库只存占位符 meta JSON）。

覆盖模板库目录定位、枚举（仅 meta JSON 条目）与删除；
templates_dir 用 monkeypatch 隔离到 tmp_path，不触碰真实软件目录 templates/。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadbatchassistant.core import templates


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
