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


class RoundtripChunksTest(unittest.TestCase):
    """run_dwg_roundtrip_chunks 分块流水线：分块转换/处理/转回 + 跳过 + 取消。

    使用 mock converter（dwg_to_dxf/dxf_to_dwg 不实际转换），process_batch
    为真实回调：校验每块各调用一次转换与写回、失败/跳过图纸不写回、
    取消时停止且不残留后台转换线程。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dwg_chunks_"))
        self.in_dir = self.tmp / "in"
        self.out_dir = self.tmp / "out"
        self.in_dir.mkdir()
        self.out_dir.mkdir()
        self._shutil = shutil

    def tearDown(self) -> None:
        self._shutil.rmtree(self.tmp, ignore_errors=True)

    def _conv(self) -> mock.Mock:
        conv = mock.Mock()
        conv.dwg_to_dxf.return_value = None
        conv.dxf_to_dwg.return_value = None
        return conv

    def _make_src(self, *stems: str) -> None:
        for s in stems:
            (self.in_dir / f"{s}.DWG").write_text("dwg", encoding="utf-8")

    def _fake_process(self, calls: list):
        """process_batch：记录调用并为本块每个 stem 生成 filled 产物。"""

        def process_batch(before: str, filled: str, stems: list[str]) -> list:
            calls.append((Path(before).name, sorted(stems)))
            for s in stems:
                (Path(filled) / f"{s}.dxf").write_text("x", encoding="utf-8")
            return [], []

        return process_batch

    def test_chunks_stage_process_writeback(self) -> None:
        """2 块 × 2 张：每块 dwg_to_dxf/dxf_to_dwg 各一次，全部成功。"""
        from cadbatchassistant.core.common.dwg_workflow import (
            run_dwg_roundtrip_chunks,
        )

        self._make_src("A1", "A2", "A3", "A4")
        conv = self._conv()
        calls: list = []
        with tempfile.TemporaryDirectory(prefix="chunk_work_") as td:
            res = run_dwg_roundtrip_chunks(
                conv,
                "oda.exe",
                self.in_dir,
                self.out_dir,
                ["A1", "A2", "A3", "A4"],
                "R2000",
                process_batch=self._fake_process(calls),
                emit=lambda m: None,
                workdir=Path(td) / "chunks",
                chunk_size=2,
            )
        self.assertEqual(res["ok"], 4)
        self.assertEqual(res["failed"], [])
        # 每块一次转换（块 0 同步、块 1 后台）+ 每块一次写回
        self.assertEqual(conv.dwg_to_dxf.call_count, 2)
        self.assertEqual(conv.dxf_to_dwg.call_count, 2)
        # 转换/写回按块边界切分（第 4 位置参为文件名列表）
        dwg_calls = [list(c.args[3]) for c in conv.dwg_to_dxf.mock_calls]
        self.assertEqual(dwg_calls[0], ["A1.DWG", "A2.DWG"])
        self.assertEqual(dwg_calls[1], ["A3.DWG", "A4.DWG"])
        dxf_calls = [list(c.args[3]) for c in conv.dxf_to_dwg.mock_calls]
        self.assertEqual(dxf_calls[0], ["A1.dxf", "A2.dxf"])
        self.assertEqual(dxf_calls[1], ["A3.dxf", "A4.dxf"])
        self.assertEqual(len(calls), 2)  # 两个分块都进入处理

    def test_failed_and_skipped_not_written_back(self) -> None:
        """process_batch 返回 (failed, skipped)：这些图纸不进写回（dxf_to_dwg 缺参）。"""
        from cadbatchassistant.core.common.dwg_workflow import (
            run_dwg_roundtrip_chunks,
        )

        self._make_src("A1", "A2")
        conv = self._conv()

        def process_batch(before, filled, stems):
            # A1 失败：无产物也不应写回
            (Path(filled) / "A2.dxf").write_text("x", encoding="utf-8")
            return ["A1"], []

        with tempfile.TemporaryDirectory(prefix="chunk_work_") as td:
            res = run_dwg_roundtrip_chunks(
                conv,
                "oda.exe",
                self.in_dir,
                self.out_dir,
                ["A1", "A2"],
                "R2000",
                process_batch=process_batch,
                emit=lambda m: None,
                workdir=Path(td) / "chunks",
            )
        self.assertEqual(res["failed"], ["A1"])
        self.assertEqual(res["ok"], 1)
        dxf_calls = [list(c.args[3]) for c in conv.dxf_to_dwg.mock_calls]
        self.assertEqual(dxf_calls, [["A2.dxf"]])

    def test_cancel_stops_after_current_chunk(self) -> None:
        """取消：当前块处理完即停（块 0 写回，块 1 不转换/不写回），线程全部回收。"""
        import threading

        from cadbatchassistant.core.common.dwg_workflow import (
            run_dwg_roundtrip_chunks,
        )

        self._make_src("A1", "A2", "A3", "A4")
        conv = self._conv()
        cancel = threading.Event()
        processed = {"n": 0}

        def process_batch(before, filled, stems):
            processed["n"] += 1
            for s in stems:
                (Path(filled) / f"{s}.dxf").write_text("x", encoding="utf-8")
            cancel.set()  # 第一块处理完成即请求取消
            return [], []

        with tempfile.TemporaryDirectory(prefix="chunk_work_") as td:
            res = run_dwg_roundtrip_chunks(
                conv,
                "oda.exe",
                self.in_dir,
                self.out_dir,
                ["A1", "A2", "A3", "A4"],
                "R2000",
                process_batch=process_batch,
                emit=lambda m: None,
                cancel=cancel,
                workdir=Path(td) / "chunks",
                chunk_size=2,
            )
        # 只处理了第一块；取消后不写回任何块（与旧流程「取消 → 无输出」一致）；
        # 块 1 的预转换线程已完成（已 join，无残留后台线程）
        self.assertEqual(processed["n"], 1)
        self.assertEqual(conv.dwg_to_dxf.call_count, 2)  # 块0 同步 + 块1 预转(已 join)
        self.assertEqual(conv.dxf_to_dwg.call_count, 0)  # 取消：不写回


if __name__ == "__main__":
    unittest.main()
