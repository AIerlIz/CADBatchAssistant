"""ODA File Converter 集成（dwg_converter）测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

import cadbatchassistant.core.dwg_converter as dc
from cadbatchassistant.core.dwg_converter import ODAError, get_converter

CONV = get_converter()


class RequireOdaForDwgTest(unittest.TestCase):
    """require_for_dwg：DWG 场景启动前校验（纯文本，供面板弹窗）。"""

    def test_pure_dxf_passes(self):
        """纯 DXF 流程（has_dwg=False）一律通过，即使未配置 ODA。"""
        self.assertIsNone(CONV.require_for_dwg(False, ""))
        self.assertIsNone(CONV.require_for_dwg(False, "C:/nonexistent/oda.exe"))

    def test_dwg_without_oda_fails(self):
        """有 DWG 但未配置 ODA 时返回可展示的错误文案。"""
        err = CONV.require_for_dwg(True, "")
        self.assertIsNotNone(err)
        self.assertIn("DWG", err)
        self.assertIn("ODAFileConverter", err)

    def test_dwg_with_blank_oda_fails(self):
        """有 DWG 但 ODA 路径为空白时同样拦截。"""
        err = CONV.require_for_dwg(True, "   ")
        self.assertIsNotNone(err)

    def test_dwg_with_missing_file_fails(self):
        """有 DWG 但 ODA 路径指向不存在的文件时拦截。"""
        err = CONV.require_for_dwg(True, "C:/no_such_dir/ODAFileConverter.exe")
        self.assertIsNotNone(err)

    def test_dwg_with_valid_oda_passes(self):
        """有 DWG 且 ODA 路径有效时通过。"""
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            p = Path(f.name)
        try:
            self.assertIsNone(CONV.require_for_dwg(True, str(p)))
            # 路径带前后空白也能通过（内部 strip）
            self.assertIsNone(CONV.require_for_dwg(True, f"  {p}  "))
        finally:
            p.unlink(missing_ok=True)


# ---------------- 探测与参数校验（合并自 CADCatalogAssistant） ----------------


def test_find_oda_converter_via_env_file(tmp_path, monkeypatch):
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("ODA_PATH", str(exe))
    assert CONV.find() == exe


def test_find_oda_converter_via_env_dir(tmp_path, monkeypatch):
    d = tmp_path / "oda"
    d.mkdir()
    exe = d / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("ODA_PATH", str(d))
    assert CONV.find() == exe


def test_find_oda_converter_none(monkeypatch):
    monkeypatch.delenv("ODA_PATH", raising=False)
    # oda 子模块的探测 globs（find() 引用的是模块级名）
    monkeypatch.setattr(
        "cadbatchassistant.core.dwg_converter.oda._CANDIDATE_GLOBS", []
    )
    assert CONV.find() is None


def test_convert_batch_missing_exe(tmp_path):
    with pytest.raises(ODAError, match="不存在"):
        CONV.convert_batch(tmp_path / "none.exe", tmp_path, tmp_path)


def test_convert_batch_invalid_version(tmp_path):
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    out = tmp_path / "out"
    with pytest.raises(ODAError, match="版本"):
        CONV.convert_batch(exe, tmp_path, out, out_version="ACAD2999")


def test_convert_batch_invalid_type(tmp_path):
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    out = tmp_path / "out"
    with pytest.raises(ODAError, match="类型"):
        CONV.convert_batch(exe, tmp_path, out, out_type="PDF")


def test_convert_batch_missing_in_dir(tmp_path):
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    with pytest.raises(ODAError, match="输入目录不存在"):
        CONV.convert_batch(exe, tmp_path / "no_such_dir", tmp_path)


# ---------------- M4：失败时 stderr 并入错误消息 ----------------


def test_convert_batch_nonzero_includes_stderr(tmp_path, monkeypatch):
    """ODA 进程返回非零时，错误消息应包含 stderr/stdout 尾部内容。"""
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    class FakeProc:
        returncode = 3
        stderr = b"[Error] license invalid\nmissing dependency\n"
        stdout = b"ODA File Converter 8.0\n"

    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(ODAError) as ei:
        CONV.convert_batch(exe, in_dir, out_dir)
    msg = str(ei.value)
    assert "退出码 3" in msg
    assert "license invalid" in msg  # stderr 尾部
    assert "missing dependency" in msg
    assert "ODA File Converter" in msg  # stdout 尾部


def test_convert_batch_nonzero_without_output(tmp_path, monkeypatch):
    """无 stderr/stdout 时错误消息不附带空明细。"""
    exe = tmp_path / "ODAFileConverter.exe"
    exe.write_bytes(b"")
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out_dir = tmp_path / "out"

    class FakeProc:
        returncode = 1
        stderr = b""
        stdout = None

    monkeypatch.setattr(dc.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(ODAError) as ei:
        CONV.convert_batch(exe, in_dir, out_dir)
    msg = str(ei.value)
    assert "退出码 1" in msg
    assert "stderr" not in msg


if __name__ == "__main__":
    unittest.main()
