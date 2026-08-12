# -*- coding: utf-8 -*-
"""fill_dwg 填表取值行为测试：只从占位符对应的列取值。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf

from cadbatchassistant.core.fill_dwg import fill_one


def _fspec(x: float, y: float) -> dict:
    """构造一个占位符规格（无实体，走 attribs 新建 TEXT 分支）。"""
    return {
        "x": x, "y": y, "height": 3.0,
        "style": "", "halign": 0, "valign": 0,
        "value_rule": "value", "sep": "", "entity": None,
    }


def _texts_of(dxf_path: Path) -> set[str]:
    doc = ezdxf.readfile(str(dxf_path))
    return {
        e.dxf.text
        for e in doc.modelspace()
        if e.dxftype() in ("TEXT", "MTEXT") and e.dxf.text.strip()
    }


class FillOnlyPlaceholderColumnsTest(unittest.TestCase):
    """取值只从占位符对应的列取：数据表其他列的值不会填入图纸。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="fill_dwg_test_")
        self.before = Path(self.tmp) / "before.dxf"
        doc = ezdxf.new("R2013")
        doc.saveas(self.before)

    def test_only_placeholder_columns_written(self) -> None:
        spec = {"0": {
            "图号": _fspec(10.0, 20.0),
            "名称": _fspec(30.0, 20.0),
        }}
        # 数据行含占位符对应列 + 无关列（干扰值不应出现）
        row = {"图号": "D-001", "名称": "舱段A", "无关列": "不应出现"}
        out = Path(self.tmp) / "out.dxf"
        fill_one(str(self.before), str(out), spec, row)

        texts = _texts_of(out)
        self.assertIn("D-001", texts)          # 占位符 [图号] 对应列的值填入
        self.assertIn("舱段A", texts)          # 占位符 [名称] 对应列的值填入
        self.assertNotIn("不应出现", texts)    # 无关列的值绝不进入图纸

    def test_missing_placeholder_column_empties_value(self) -> None:
        """占位符对应的列在数据表中不存在 → 该字段置空（不猜取其他列）。"""
        spec = {"0": {"图号": _fspec(10.0, 20.0)}}
        row = {"名称": "舱段A"}   # 数据行没有「图号」列
        out = Path(self.tmp) / "out.dxf"
        fill_one(str(self.before), str(out), spec, row)

        texts = _texts_of(out)
        # 「图号」列缺失 → 不写入任何值；「名称」列的值绝不冒充图号填入
        self.assertEqual(texts, set())
        self.assertNotIn("舱段A", texts)


class FillExistingTextTest(unittest.TestCase):
    """M1：TEXT 实体在目标位置已有内容时被识别，不覆盖/不重复叠加。

    修复前 getattr(e, "text", "") 对 TEXT 恒为空 → 已有内容检测完全失效，
    每次运行都会在同坐标叠加新 TEXT。
    """

    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="fill_existing_"))
        self.before = self.tmp / "before.dxf"
        self.out = self.tmp / "out.dxf"
        self._shutil = shutil

    def tearDown(self) -> None:
        self._shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_before(self, texts: list[tuple[str, float, float]]) -> None:
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        for t, x, y in texts:
            msp.add_text(t, dxfattribs={"insert": (x, y, 0), "height": 3.0})
        doc.saveas(self.before)

    def _texts_of(self) -> set[str]:
        doc = ezdxf.readfile(str(self.out))
        return {e.dxf.text for e in doc.modelspace()
                if e.dxftype() == "TEXT" and e.dxf.text.strip()}

    def test_text_existing_content_not_overwritten(self) -> None:
        """位置已有 TEXT 内容（不同值）→ 跳过不覆盖，原文本保留且不新增。"""
        from cadbatchassistant.core.fill_dwg import fill_one

        self._make_before([("旧内容", 10.0, 20.0)])
        spec = {"0": {"图号": {**_fspec(10.0, 20.0)}}}
        fill_one(str(self.before), str(self.out), spec, {"图号": "新内容"})

        texts = self._texts_of()
        self.assertEqual(texts, {"旧内容"})  # 原 TEXT 保留，未叠加新值

    def test_text_same_content_skipped(self) -> None:
        """位置已有 TEXT 且内容与待填值相同 → 判定"已存在"跳过（幂等）。"""
        from cadbatchassistant.core.fill_dwg import fill_one

        self._make_before([("D-001", 10.0, 20.0)])
        spec = {"0": {"图号": {**_fspec(10.0, 20.0)}}}
        fill_one(str(self.before), str(self.out), spec, {"图号": "D-001"})

        texts = self._texts_of()
        self.assertEqual(texts, {"D-001"})  # 不重复叠加同值

    def test_text_empty_position_filled(self) -> None:
        """位置无 TEXT → 正常填写新值（回归：不影响正常填入）。"""
        from cadbatchassistant.core.fill_dwg import fill_one

        self._make_before([])
        spec = {"0": {"图号": {**_fspec(10.0, 20.0)}}}
        fill_one(str(self.before), str(self.out), spec, {"图号": "D-001"})

        texts = self._texts_of()
        self.assertIn("D-001", texts)

    def test_mtext_with_format_codes_same_text_skipped(self) -> None:
        """已有 MTEXT 含格式码（\\P 等）时，同值比较忽略格式码判定"已存在"。"""
        from cadbatchassistant.core.fill_dwg import fill_one

        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_mtext("D-001\\P（续）",
                      dxfattribs={"insert": (10, 20), "char_height": 3.0})
        doc.saveas(self.before)
        spec = {"0": {"图号": {**_fspec(10.0, 20.0)}}}
        fill_one(str(self.before), str(self.out), spec, {"图号": "D-001"})

        doc2 = ezdxf.readfile(str(self.out))
        msp2 = doc2.modelspace()
        mt = [e for e in msp2 if e.dxftype() == "MTEXT"]
        text_entities = [e for e in msp2 if e.dxftype() == "TEXT"]
        # 同值被跳过：MTEXT 仍只有 1 个，且未叠加新 TEXT 实体
        # （若 _entity_text 的 MTEXT 分支回归为返回空，fill_one 会在同坐标
        #   新增 TEXT，TEXT 数将 >0 → 测试能抓住该回归）
        self.assertEqual(len(mt), 1)
        self.assertEqual(len(text_entities), 0)


class FillAllSkippedTest(unittest.TestCase):
    """fill_all 返回 (failed, skipped)：无产出的图纸计入 skipped 而非 failed。

    调用方（run_pipeline）据 skipped 避免把缺失产物当作成功（防止
    FileNotFoundError / ODA 900s 挂起）。
    """

    def setUp(self) -> None:
        import shutil

        import openpyxl

        self.tmp = Path(tempfile.mkdtemp(prefix="fill_all_skip_"))
        self.before_dir = self.tmp / "before"
        self.out_dir = self.tmp / "filled"
        self.before_dir.mkdir()
        self.out_dir.mkdir()

        # 两张 before DXF：A1、B1（B1 不在数据表中）
        for name in ("A1", "B1"):
            doc = ezdxf.new("R2013")
            doc.saveas(self.before_dir / f"{name}.dxf")

        # xlsx 只含 A1（图纸名列第一列）
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["图纸", "图号"])
        ws.append(["A1", "D-001"])
        self.xlsx = self.tmp / "data.xlsx"
        wb.save(self.xlsx)
        wb.close()
        self._shutil = shutil

    def tearDown(self) -> None:
        self._shutil.rmtree(self.tmp, ignore_errors=True)

    def _specs(self, *names: str) -> dict:
        spec = {n: {"0": {"图号": _fspec(10.0, 20.0)}} for n in names}
        return spec

    def test_missing_in_xlsx_counts_as_skipped(self) -> None:
        from cadbatchassistant.core.fill_dwg import fill_all

        failed, skipped = fill_all(
            str(self.before_dir), str(self.out_dir), str(self.xlsx),
            self._specs("A1", "B1"), emit=lambda m: None)
        self.assertEqual(failed, [])
        self.assertEqual(skipped, ["B1"])
        # A1 正常产出，B1 无产物
        self.assertTrue((self.out_dir / "A1.dxf").is_file())
        self.assertFalse((self.out_dir / "B1.dxf").is_file())

    def test_missing_before_dxf_counts_as_skipped(self) -> None:
        from cadbatchassistant.core.fill_dwg import fill_all

        # C1 在数据表中但没有 before DXF → skipped
        failed, skipped = fill_all(
            str(self.before_dir), str(self.out_dir), str(self.xlsx),
            self._specs("A1", "C1"), emit=lambda m: None)
        self.assertEqual(failed, [])
        self.assertEqual(skipped, ["C1"])
        self.assertTrue((self.out_dir / "A1.dxf").is_file())


if __name__ == "__main__":
    unittest.main()
