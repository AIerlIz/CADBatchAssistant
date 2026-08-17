"""dwg_workflow 编排测试：write_back_dxf_batch 的 skip 语义（B1 回归）。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadbatchassistant.core.common.dwg_workflow import write_back_dxf_batch


class WriteBackSkipTest(unittest.TestCase):
    """skip 集合为无扩展名 stem，与 fill_all 返回的 failed/skipped 一致。

    回归：曾把含扩展名文件名（"A1.DWG"/"A1.dxf"）与 stem（"A1"）直接比较，
    skip 永不命中 → 失败的 DWG 会进 dxf_to_dwg（产物缺失挂起/整批崩）、
    失败的 DXF 会在 copy2 时 FileNotFoundError 整批崩溃。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dwg_workflow_"))
        self.processed = self.tmp / "processed"
        self.out = self.tmp / "out"
        self.processed.mkdir()
        # processed 里只有 A1/B1 的产物；C1 因处理失败无产物
        for name in ("A1.dxf", "B1.dxf"):
            (self.processed / name).write_text("DXF-" + name, encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conv(self) -> mock.Mock:
        conv = mock.Mock()
        conv.dxf_to_dwg.return_value = None
        return conv

    def test_skip_stem_excludes_dwg_and_dxf(self) -> None:
        """skip={"C1"}：C1.DWG 不进 dxf_to_dwg，C1.dxf 不复制，其余照常。"""
        conv = self._conv()
        write_back_dxf_batch(
            conv,
            "oda.exe",
            self.processed,
            self.out,
            dwg_files=["A1.DWG", "C1.DWG"],
            dxf_files=["A1.dxf", "B1.dxf", "C1.dxf"],
            out_version="R2000",
            skip={"C1"},
        )
        # DWG 源：dxf_to_dwg 只收到 A1 的 DXF 产物（C1.DWG 被过滤）
        conv.dxf_to_dwg.assert_called_once()
        args = conv.dxf_to_dwg.call_args
        self.assertEqual(list(args[0][3]), ["A1.dxf"])
        # DXF 源：A1/B1 复制到输出，C1 跳过
        self.assertTrue((self.out / "A1.dxf").is_file())
        self.assertTrue((self.out / "B1.dxf").is_file())
        self.assertFalse((self.out / "C1.dxf").is_file())

    def test_no_skip_writes_all(self) -> None:
        """skip 为空时全部写回（行为不变）。"""
        conv = self._conv()
        write_back_dxf_batch(
            conv,
            "oda.exe",
            self.processed,
            self.out,
            dwg_files=["A1.DWG"],
            dxf_files=["A1.dxf", "B1.dxf"],
            out_version="R2000",
        )
        conv.dxf_to_dwg.assert_called_once()
        args = conv.dxf_to_dwg.call_args
        self.assertEqual(list(args[0][3]), ["A1.dxf"])
        self.assertTrue((self.out / "A1.dxf").is_file())
        self.assertTrue((self.out / "B1.dxf").is_file())


if __name__ == "__main__":
    unittest.main()
