"""gui.tk_widgets 单测：ODA 启动自动检测逻辑（不依赖真实 Tk）。

check_oda 使用 mock 的 StringVar（get/set），无需显示环境；
覆盖：未配置自动填入 / 失效配置自动替换 / 有效配置保留 / 探测不到保留。
"""

from __future__ import annotations

from unittest import mock

from cadbatchassistant.gui import tk_widgets as tw


def _make_vars(current: str = ""):
    var_oda = mock.Mock()
    var_oda.get.return_value = current
    var_info = mock.Mock()
    return var_oda, var_info


def _patch_converter(monkeypatch, find_result):
    """mock get_converter 返回带 .find() 的 converter。"""
    conv = mock.Mock()
    conv.find.return_value = find_result
    monkeypatch.setattr(tw, "get_converter", lambda: conv)
    return conv


def test_check_oda_fills_empty(tmp_path, monkeypatch):
    """未配置 → 启动探测到 ODA 时自动填入。"""
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_text("x", encoding="utf-8")
    _patch_converter(monkeypatch, exe)
    var_oda, var_info = _make_vars()
    tw.check_oda(var_oda, var_info)
    var_oda.set.assert_called_once_with(str(exe))
    var_info.set.assert_called_once_with("✓ 已检测到")


def test_check_oda_replaces_invalid_config(tmp_path, monkeypatch):
    """配置路径已失效（文件不存在）→ 自动替换为探测结果。"""
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_text("x", encoding="utf-8")
    stale = tmp_path / "old" / "ODAFileConverter.exe"
    _patch_converter(monkeypatch, exe)
    var_oda, var_info = _make_vars(current=str(stale))
    tw.check_oda(var_oda, var_info)
    var_oda.set.assert_called_once_with(str(exe))


def test_check_oda_keeps_valid_config(tmp_path, monkeypatch):
    """已配置且路径有效 → 保留用户路径，不覆盖。"""
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_text("x", encoding="utf-8")
    _patch_converter(monkeypatch, exe)
    var_oda, var_info = _make_vars(current=str(exe))
    tw.check_oda(var_oda, var_info)
    var_oda.set.assert_not_called()
    var_info.set.assert_called_once_with("✓ 已检测到")


def test_check_oda_not_found_keeps_value(tmp_path, monkeypatch):
    """探测不到 → 保留当前值，仅提示未检测。"""
    _patch_converter(monkeypatch, None)
    var_oda, var_info = _make_vars(current=r"D:\some\oda.exe")
    tw.check_oda(var_oda, var_info)
    var_oda.set.assert_not_called()
    var_info.set.assert_called_once_with("未检测到（处理 DWG 需要；纯 DXF 无需）")
