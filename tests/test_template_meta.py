"""template_meta 通用存取单测：save/load/remove + 容错。

- save→load 往返字段一致（含中文，ensure_ascii=False）
- 缺失 / JSON 损坏 / version 不符 → None（静默）
- remove 幂等（不存在不抛）
"""

from __future__ import annotations

import json

from cadbatchassistant.core.common.template_meta import (
    load_template_meta,
    meta_path_for,
    remove_template_meta,
    save_template_meta,
)


def test_save_load_roundtrip(tmp_path):
    tpl = tmp_path / "模板.dwg"
    tpl.write_text("x")
    meta = save_template_meta(tpl, {"anchors": [{"field": "图号", "is_area": False}]})
    assert meta == tmp_path / "模板.dwg.json"
    data = load_template_meta(tpl)
    assert data is not None
    assert data["version"] == 1
    assert data["source"] == "模板.dwg"
    assert data["anchors"] == [{"field": "图号", "is_area": False}]


def test_save_meta_is_utf8_readable(tmp_path):
    """中文载荷以 UTF-8 可读文本写出（ensure_ascii=False）。"""
    tpl = tmp_path / "t.dxf"
    tpl.write_text("x")
    save_template_meta(tpl, {"fields": ["图号", "管段编号"]})
    raw = (tmp_path / "t.dxf.json").read_text(encoding="utf-8")
    assert "图号" in raw


def test_load_missing_returns_none(tmp_path):
    tpl = tmp_path / "n.dwg"
    assert load_template_meta(tpl) is None


def test_load_broken_json_returns_none(tmp_path):
    tpl = tmp_path / "b.dwg"
    tpl.write_text("x")
    meta = meta_path_for(tpl)
    meta.write_text("{broken", encoding="utf-8")
    assert load_template_meta(tpl) is None


def test_load_version_mismatch_returns_none(tmp_path):
    tpl = tmp_path / "v.dwg"
    tpl.write_text("x")
    meta_path_for(tpl).write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert load_template_meta(tpl) is None


def test_remove_is_idempotent(tmp_path):
    tpl = tmp_path / "r.dwg"
    tpl.write_text("x")
    save_template_meta(tpl, {"fields": []})
    assert meta_path_for(tpl).is_file()
    remove_template_meta(tpl)
    assert not meta_path_for(tpl).exists()
    remove_template_meta(tpl)  # 幂等：不存在不抛
