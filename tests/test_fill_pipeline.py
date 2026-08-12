# -*- coding: utf-8 -*-
"""fill_pipeline 流程保护测试：输出目录与输入目录重合时拒绝处理，防止覆盖源文件。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cadbatchassistant.core import fill_pipeline


class OutputSameAsInputProtectionTest(unittest.TestCase):
    """H1：输出目录与输入目录（或源文件所在目录）重合时必须报错，不得删除/覆盖源文件。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="fill_pipeline_test_")
        self.before_dir = Path(self.tmp) / "input"
        self.before_dir.mkdir()
        # 放一个真实 DXF 作为"源图纸"，用于验证重合时不会被触碰
        self.src_dxf = self.before_dir / "A1.dxf"
        self.src_dxf.write_text("MOCK-DXF-ORIGINAL", encoding="utf-8")
        self.xlsx = Path(self.tmp) / "data.xlsx"
        self.xlsx.write_text("mock", encoding="utf-8")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_pipeline_same_dir_rejected(self) -> None:
        """out_dir == before_dir 时抛 ValueError，且源文件原样保留。"""
        with self.assertRaises(ValueError) as ctx:
            fill_pipeline.run_pipeline(
                str(self.xlsx), str(self.before_dir), str(self.before_dir),
                template=str(self.src_dxf))
        self.assertIn("不能与输入图纸目录相同", str(ctx.exception))
        # 源文件未被删除/覆盖
        self.assertEqual(
            self.src_dxf.read_text(encoding="utf-8"), "MOCK-DXF-ORIGINAL")

    def test_run_pipeline_files_same_source_dir_rejected(self) -> None:
        """run_pipeline_files：输出目录 == 源文件所在目录时抛 ValueError。"""
        with self.assertRaises(ValueError) as ctx:
            fill_pipeline.run_pipeline_files(
                str(self.xlsx), [str(self.src_dxf)], str(self.before_dir))
        self.assertIn("不能与输入图纸所在目录相同", str(ctx.exception))
        self.assertEqual(
            self.src_dxf.read_text(encoding="utf-8"), "MOCK-DXF-ORIGINAL")

    def test_run_pipeline_different_dir_not_rejected(self) -> None:
        """输出目录与输入目录不同时，不应触发重合保护。

        用 mock 阻断模板转换（模拟 ODA 转换阶段失败），断言异常来自流程
        本身（ODAError）而非重合保护（ValueError"目录相同"）。
        """
        out = Path(self.tmp) / "output"
        from unittest import mock

        from cadbatchassistant.core import dwg_converter as dc

        with mock.patch.object(
            dc, "convert_template_to_dxf",
            side_effect=dc.ODAError("mock oda 失败"),
        ):
            with self.assertRaises(dc.ODAError):
                fill_pipeline.run_pipeline(
                    str(self.xlsx), str(self.before_dir), str(out),
                    template=str(self.src_dxf))

    def test_run_pipeline_files_duplicate_basename_rejected(self) -> None:
        """M2：跨目录同名文件（大小写不敏感）复制到临时目录会互相覆盖 → 拒绝。"""
        src2 = Path(self.tmp) / "input2"
        src2.mkdir()
        dup = src2 / "A1.dxf"
        dup.write_text("MOCK-DXF-2", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            fill_pipeline.run_pipeline_files(
                str(self.xlsx), [str(self.src_dxf), str(dup)],
                str(Path(self.tmp) / "output"))
        self.assertIn("输入文件重名", str(ctx.exception))

    def test_run_pipeline_files_duplicate_case_insensitive_rejected(self) -> None:
        """M2：大小写不同也视为重名（a.dwg vs A.DWG 会互相覆盖）。"""
        src2 = Path(self.tmp) / "input2"
        src2.mkdir()
        dup = src2 / "a1.DXF"  # 与 A1.dxf 大小写不同
        dup.write_text("MOCK-DXF-2", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            fill_pipeline.run_pipeline_files(
                str(self.xlsx), [str(self.src_dxf), str(dup)],
                str(Path(self.tmp) / "output"))
        self.assertIn("输入文件重名", str(ctx.exception))

    def test_dwg_priority_when_same_stem_dwg_and_dxf(self) -> None:
        """M8：同名 .dwg 与 .dxf 共存时按 DWG 处理（dwg 优先，结果确定）。

        通过观测 [1/4] 阶段是否调用 convert_dwg_batch_to_dxf 来验证：
        输入目录同时有 A1.dwg 与 A1.dxf 时，dwg 分支被选中（dxf 分支只是 copy2）。
        """
        from unittest import mock

        from cadbatchassistant.core import dwg_converter as dc

        self.before_dir.joinpath("A1.dwg").write_bytes(b"MOCK-DWG")
        with mock.patch.object(
            dc, "require_oda_for_dwg", return_value=None,
        ), mock.patch.object(
            dc, "convert_dwg_batch_to_dxf",
            side_effect=dc.ODAError("mock oda 失败"),
        ), mock.patch.object(
            dc, "convert_template_to_dxf",
            side_effect=dc.ODAError("mock 模板 oda 失败"),
        ):
            with self.assertRaises(dc.ODAError):
                fill_pipeline.run_pipeline(
                    str(self.xlsx), str(self.before_dir),
                    str(Path(self.tmp) / "output"), template=str(self.src_dxf))
            # 若被误判为 dxf，则不会调用 convert_dwg_batch_to_dxf（而只是 copy2 复制）
            dc.convert_dwg_batch_to_dxf.assert_called_once()

    def test_tmp_dir_cleaned_when_auto_created(self) -> None:
        """M3：workdir=None 时自建临时目录，流程结束（含异常路径）后应被清理。"""
        import tempfile
        from unittest import mock

        from cadbatchassistant.core import dwg_converter as dc

        before = list(Path(tempfile.gettempdir()).glob("iso_fill_*"))
        with mock.patch.object(
            dc, "convert_template_to_dxf",
            side_effect=dc.ODAError("mock oda 失败"),
        ):
            with self.assertRaises(dc.ODAError):
                fill_pipeline.run_pipeline(
                    str(self.xlsx), str(self.before_dir),
                    str(Path(self.tmp) / "output"), template=str(self.src_dxf))
        after = list(Path(tempfile.gettempdir()).glob("iso_fill_*"))
        # 异常路径后不残留自建临时目录
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
