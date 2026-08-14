"""core.templates 纯文件操作单测（拆分自 common.py 后的语义回归）。

覆盖模板库目录定位、枚举、复制与删除；templates_dir 用 monkeypatch
隔离到 tmp_path，不触碰真实软件目录 templates/。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cadbatchassistant.core import templates


def _patch_templates_dir(monkeypatch, base: Path) -> None:
    monkeypatch.setattr(
        templates, "templates_dir", lambda cat: base / "templates" / cat)


def test_template_path(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.template_path("fill", "a.dwg") == \
        tmp_path / "templates" / "fill" / "a.dwg"


def test_list_templates_filters(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    (d / "a.dwg").write_text("x")
    (d / "b.dxf").write_text("x")
    (d / "c.txt").write_text("x")
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == ["a.dwg", "b.dxf"]


def test_list_templates_missing_dir(monkeypatch, tmp_path: Path) -> None:
    _patch_templates_dir(monkeypatch, tmp_path)
    assert templates.list_templates("fill") == []


def test_copy_template(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "tpl.dwg"
    src.write_text("data")
    _patch_templates_dir(monkeypatch, tmp_path)
    name = templates.copy_template("fill", str(src))
    assert name == "tpl.dwg"
    assert (tmp_path / "templates" / "fill" / "tpl.dwg").read_text() == "data"


def test_copy_template_overwrites(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "tpl.dwg"
    src.write_text("new")
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    (d / "tpl.dwg").write_text("old")
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.copy_template("fill", str(src))
    assert (d / "tpl.dwg").read_text() == "new"


def test_remove_template(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    f = d / "a.dwg"
    f.write_text("x")
    _patch_templates_dir(monkeypatch, tmp_path)
    templates.remove_template("fill", "a.dwg")
    assert not f.exists()


def test_remove_template_missing_raises(monkeypatch, tmp_path: Path) -> None:
    d = tmp_path / "templates" / "fill"
    d.mkdir(parents=True)
    _patch_templates_dir(monkeypatch, tmp_path)
    with pytest.raises(OSError):
        templates.remove_template("fill", "nope.dwg")
