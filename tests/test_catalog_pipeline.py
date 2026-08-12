# -*- coding: utf-8 -*-
"""catalog_pipeline 配置解析测试。"""

from __future__ import annotations

from cadbatchassistant.core.catalog_pipeline import _point_tolerance


def test_point_tolerance_default():
    """未配置时回退默认 5.0。"""
    assert _point_tolerance(None) == 5.0
    assert _point_tolerance({}) == 5.0


def test_point_tolerance_valid():
    """合法数字配置正常解析。"""
    assert _point_tolerance({"point_tolerance": "3.5"}) == 3.5
    assert _point_tolerance({"point_tolerance": 8}) == 8.0


def test_point_tolerance_invalid_falls_back():
    """M5：非数字配置（用户可编辑 config.json 写坏）回退默认，不抛异常。"""
    assert _point_tolerance({"point_tolerance": "abc"}) == 5.0
    assert _point_tolerance({"point_tolerance": None}) == 5.0
