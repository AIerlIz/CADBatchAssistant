"""FilesPanelMixin 默认输出目录行为测试。

- _default_output：待处理列表非空时 = 第一个文件所在目录/output
- 列表从空变非空（导入第一个文件）时总是更新默认输出（即使已有值）
- 列表非空时追加文件不重置输出目录
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from cadbatchassistant.gui.gui_shared import FilesPanelMixin


class _FakePanel(FilesPanelMixin):
    def __init__(self) -> None:
        self.scanned_files: list[str] = []
        self.var_out = mock.Mock()
        self.var_out.get.return_value = ""
        self.file_list = mock.Mock()
        self.var_scan_info = mock.Mock()


@contextmanager
def _patch_deps(paths: list[str]):
    """mock 掉 GUI 依赖（拖放解析/文件存在性/弹窗），模拟导入 paths。"""
    with (
        mock.patch(
            "cadbatchassistant.gui.gui_shared.parse_dnd_data", return_value=paths
        ),
        mock.patch(
            "cadbatchassistant.gui.gui_shared.os.path.isfile", return_value=True
        ),
        mock.patch("cadbatchassistant.gui.gui_shared.messagebox.showwarning"),
    ):
        yield


class DefaultOutputTest(unittest.TestCase):
    def test_default_output_is_first_file_dir_output(self) -> None:
        p = _FakePanel()
        p.scanned_files = [r"D:\a\A1.dwg"]
        p._default_output()
        p.var_out.set.assert_called_once_with(r"D:\a\output")

    def test_empty_list_does_not_set(self) -> None:
        p = _FakePanel()
        p._default_output()
        p.var_out.set.assert_not_called()

    def test_drop_first_file_sets_default_even_if_out_exists(self) -> None:
        """列表从空变非空：即使输出目录已有值也更新为默认。"""
        p = _FakePanel()
        p.var_out.get.return_value = r"D:\existing"
        with _patch_deps([r"D:\a\A1.dwg"]):
            p._on_drop_files(mock.Mock(data="D:/a/A1.dwg"))
        self.assertEqual(p.scanned_files, [r"D:\a\A1.dwg"])
        p.var_out.set.assert_called_once_with(r"D:\a\output")

    def test_browse_first_file_sets_default_even_if_out_exists(self) -> None:
        """列表从空变非空（浏览选择）：已有输出值也更新为默认。"""
        p = _FakePanel()
        p.var_out.get.return_value = r"D:\existing"
        with _patch_deps([r"D:\a\A1.dwg"]), mock.patch(
            "cadbatchassistant.gui.gui_shared.filedialog.askopenfilenames",
            return_value=[r"D:\a\A1.dwg"],
        ):
            p._browse_input_files()
        self.assertEqual(p.scanned_files, [r"D:\a\A1.dwg"])
        p.var_out.set.assert_called_once_with(r"D:\a\output")

    def test_append_when_not_empty_does_not_reset_output(self) -> None:
        """列表非空时追加文件：不重置输出目录。"""
        p = _FakePanel()
        p.scanned_files = [r"D:\a\A1.dwg"]
        p.var_out.get.return_value = r"D:\existing"
        with _patch_deps([r"D:\b\B1.dwg"]):
            p._on_drop_files(mock.Mock(data="D:/b/B1.dwg"))
        self.assertEqual(p.scanned_files, [r"D:\a\A1.dwg", r"D:\b\B1.dwg"])
        p.var_out.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
