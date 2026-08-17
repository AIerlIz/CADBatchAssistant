"""改字助手 DXF 中文编码处理测试（GBK/ANSI 内容 + surrogateescape 残留）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ezdxf

from cadbatchassistant.core.common.text_replace import (
    ReplaceRule,
    _has_undecoded_surrogates,
    apply_rules,
    decode_text,
    process_dxf_file,
)


class DecodeTextTest(unittest.TestCase):
    def test_u_escape_decoded(self):
        self.assertEqual(decode_text(r"机舱A2DK\U+533A\U+57DF"), "机舱A2DK区域")

    def test_plain_text_unchanged(self):
        self.assertEqual(decode_text("普通中文"), "普通中文")


class ApplyRulesModeTest(unittest.TestCase):
    """普通文本模式（默认）与正则模式的查找替换语义。"""

    def test_plain_text_literal_parentheses(self):
        """普通模式：整句含半角括号直接匹配（根因场景）。"""
        text = "机舱A2DK区域管子制作图(四十一)"
        new, cnt = apply_rules(
            text, [ReplaceRule(find="机舱A2DK区域管子制作图(四十一)", replace="X")]
        )
        self.assertEqual(cnt, 1)
        self.assertEqual(new, "X")

    def test_plain_text_dot_is_literal(self):
        new, cnt = apply_rules("v1.0 file", [ReplaceRule(find="v1.0", replace="v2")])
        self.assertEqual((cnt, new), (1, "v2 file"))

    def test_plain_text_replacement_backslash_literal(self):
        """普通模式：替换文本中的 \\1 / 反斜杠按字面输出。"""
        new, cnt = apply_rules("AB", [ReplaceRule(find="AB", replace=r"路径\1")])
        self.assertEqual((cnt, new), (1, r"路径\1"))

    def test_regex_mode_keep_regex_semantics(self):
        """正则模式：保留正则能力（元字符、捕获组反向引用）。"""
        new, cnt = apply_rules(
            "REV1 REV23", [ReplaceRule(find=r"REV(\d+)", replace=r"R\1", regex=True)]
        )
        self.assertEqual((cnt, new), (2, "R1 R23"))

    def test_regex_mode_parentheses_are_groups(self):
        """正则模式：未转义括号是捕获组（与旧行为一致，只匹配括号内文字）。"""
        new, cnt = apply_rules(
            "x(四十一)y", [ReplaceRule(find="(四十一)", replace="Z", regex=True)]
        )
        self.assertEqual((cnt, new), (1, "x(Z)y"))

    def test_empty_find_skipped(self):
        new, cnt = apply_rules("abc", [ReplaceRule(find="", replace="x")])
        self.assertEqual((cnt, new), (0, "abc"))


class ReadDocEncodingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="cad_dxf_test_"))

    def tearDown(self):
        for f in self._tmp.iterdir():
            f.unlink(missing_ok=True)
        self._tmp.rmdir()

    def _make_cn_dxf(self, name: str, text: str = "机舱A2DK区域管子制作图") -> Path:
        doc = ezdxf.new("R2018")
        doc.modelspace().add_text(text)
        p = self._tmp / name
        doc.saveas(p)
        return p

    def test_utf8_content_replaces(self):
        """UTF-8 内容（现代 CAD 默认）中文替换正常。"""
        src = self._make_cn_dxf("u.dxf")
        res = process_dxf_file(
            src, self._tmp / "u_out.dxf", [ReplaceRule(find="管子", replace="管道")]
        )
        self.assertEqual(res.replaced_total, 1)
        self.assertIn("管道".encode(), (self._tmp / "u_out.dxf").read_bytes())

    def test_gbk_content_declared_936_replaces(self):
        """老图纸真实形态：内容 GBK + 声明 ANSI_936，按声明编码重读可替换。"""
        src = self._make_cn_dxf("g.dxf")
        gbk = self._tmp / "g_gbk.dxf"
        body = src.read_bytes().decode("utf-8").encode("gbk")
        gbk.write_bytes(body.replace(b"ANSI_1252", b"ANSI_936"))
        res = process_dxf_file(
            gbk, self._tmp / "g_out.dxf", [ReplaceRule(find="管子", replace="管道")]
        )
        self.assertEqual(res.replaced_total, 1)
        self.assertIn("管道".encode(), (self._tmp / "g_out.dxf").read_bytes())

    def test_gbk_content_unknown_codepage_falls_back(self):
        """声明不可映射（ANSI_9999）时按中文编码兜底，仍可替换。"""
        src = self._make_cn_dxf("g2.dxf")
        gbk = self._tmp / "g2_gbk.dxf"
        body = src.read_bytes().decode("utf-8").encode("gbk")
        gbk.write_bytes(body.replace(b"ANSI_1252", b"ANSI_9999"))
        res = process_dxf_file(
            gbk, self._tmp / "g2_out.dxf", [ReplaceRule(find="管子", replace="管道")]
        )
        self.assertEqual(res.replaced_total, 1)

    def test_english_still_replaces_on_gbk_content(self):
        """GBK 内容文件的英文替换也不受影响。"""
        src = self._make_cn_dxf("e.dxf")
        gbk = self._tmp / "e_gbk.dxf"
        gbk.write_bytes(src.read_bytes().decode("utf-8").encode("gbk"))
        res = process_dxf_file(
            gbk, self._tmp / "e_out.dxf", [ReplaceRule(find="A2DK", replace="X")]
        )
        self.assertEqual(res.replaced_total, 1)

    def _make_declared_cp(self, declared_cp: str, enc: str, name: str) -> Path:
        """内容按 enc 编码、头部 $DWGCODEPAGE 声明为 declared_cp 的 DXF。"""
        src = self._make_cn_dxf("base.dxf")
        p = self._tmp / name
        body = src.read_bytes().decode("utf-8").encode(enc, errors="replace")
        p.write_bytes(body.replace(b"ANSI_1252", declared_cp.encode()))
        return p

    def test_big5_content_uses_declared_codepage(self):
        """Big5 内容 + ANSI_950 声明（繁体中文图纸）：按声明编码重读可替换。"""
        p = self._make_declared_cp("ANSI_950", "big5", "big5.dxf")
        res = process_dxf_file(
            p, self._tmp / "big5_out.dxf", [ReplaceRule(find="管子", replace="管道")]
        )
        self.assertEqual(res.replaced_total, 1)

    def test_shift_jis_content_uses_declared_codepage(self):
        """Shift_JIS 内容 + ANSI_932 声明（日文图纸）：按声明编码重读可替换。"""
        p = self._make_declared_cp("ANSI_932", "shift_jis", "sjis.dxf")
        res = process_dxf_file(
            p, self._tmp / "sjis_out.dxf", [ReplaceRule(find="管子", replace="管道")]
        )
        self.assertEqual(res.replaced_total, 1)

    def test_utf8_content_with_codepage_declaration_not_rerun(self):
        """声明非 UTF-8 但内容实际为 UTF-8（ODA 输出常见形态）：
        首读 UTF-8 成功无残留，不重读，替换正常。"""
        src = self._make_cn_dxf("u8.dxf")
        body = src.read_bytes().replace(b"ANSI_1252", b"ANSI_936")
        p = self._tmp / "u8_decl936.dxf"
        p.write_bytes(body)
        res = process_dxf_file(
            p,
            self._tmp / "u8_decl936_out.dxf",
            [ReplaceRule(find="管子", replace="管道")],
        )
        self.assertEqual(res.replaced_total, 1)


class SurrogateDetectionTest(unittest.TestCase):
    def test_detects_residue(self):
        doc = ezdxf.new("R2018")
        doc.modelspace().add_text("bad\udcbb\udcfa text")
        self.assertTrue(_has_undecoded_surrogates(doc))

    def test_clean_text_not_detected(self):
        doc = ezdxf.new("R2018")
        doc.modelspace().add_text("正常中文 ABC")
        self.assertFalse(_has_undecoded_surrogates(doc))


if __name__ == "__main__":
    unittest.main()
