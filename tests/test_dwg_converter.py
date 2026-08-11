# -*- coding: utf-8 -*-
"""ODA File Converter 集成（dwg_converter）测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadbatchassistant.core.dwg_converter import require_oda_for_dwg


class RequireOdaForDwgTest(unittest.TestCase):
    """require_oda_for_dwg：DWG 场景启动前校验（纯文本，供面板弹窗）。"""

    def test_pure_dxf_passes(self):
        """纯 DXF 流程（has_dwg=False）一律通过，即使未配置 ODA。"""
        self.assertIsNone(require_oda_for_dwg(False, ""))
        self.assertIsNone(require_oda_for_dwg(False, "C:/nonexistent/oda.exe"))

    def test_dwg_without_oda_fails(self):
        """有 DWG 但未配置 ODA 时返回可展示的错误文案。"""
        err = require_oda_for_dwg(True, "")
        self.assertIsNotNone(err)
        self.assertIn("DWG", err)
        self.assertIn("ODAFileConverter", err)

    def test_dwg_with_blank_oda_fails(self):
        """有 DWG 但 ODA 路径为空白时同样拦截。"""
        err = require_oda_for_dwg(True, "   ")
        self.assertIsNotNone(err)

    def test_dwg_with_missing_file_fails(self):
        """有 DWG 但 ODA 路径指向不存在的文件时拦截。"""
        err = require_oda_for_dwg(True, "C:/no_such_dir/ODAFileConverter.exe")
        self.assertIsNotNone(err)

    def test_dwg_with_valid_oda_passes(self):
        """有 DWG 且 ODA 路径有效时通过。"""
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            p = Path(f.name)
        try:
            self.assertIsNone(require_oda_for_dwg(True, str(p)))
            # 路径带前后空白也能通过（内部 strip）
            self.assertIsNone(require_oda_for_dwg(True, f"  {p}  "))
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
