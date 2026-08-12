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


if __name__ == "__main__":
    unittest.main()
