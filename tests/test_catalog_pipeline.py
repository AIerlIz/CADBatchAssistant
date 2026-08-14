"""catalog_pipeline 配置解析测试。"""

from __future__ import annotations

from cadbatchassistant.core.catalog_pipeline import _point_tolerance


def test_point_tolerance_default():
    """未配置时回退默认 5.0。"""
    assert _point_tolerance(None) == 5.0
    assert _point_tolerance({}) == 5.0


def test_point_tolerance_valid():
    """合法数字配置正常解析。"""
    assert _point_tolerance({"point_tolerance": "3.5"}) == 3.5
    assert _point_tolerance({"point_tolerance": 8}) == 8.0


def test_point_tolerance_invalid_falls_back():
    """M5：非数字配置（用户可编辑 config.json 写坏）回退默认，不抛异常。"""
    assert _point_tolerance({"point_tolerance": "abc"}) == 5.0
    assert _point_tolerance({"point_tolerance": None}) == 5.0


def test_on_done_progress_and_log_monotonic_when_out_of_order(monkeypatch, tmp_path):
    """S3：并行完成序乱序时，日志编号/进度按「完成序号」单调推进。

    mock map_files 捕获 on_done，按提交序 2,0,1,3 的乱序完成回调：修复前
    日志编号用提交序（打印 3/4 → 1/4 → 2/4 → 4/4）、进度乱跳；修复后按
    完成计数连续编号（1,2,3,4）且进度单调递增。entries 仍按提交序落位。
    """
    from unittest import mock

    import cadbatchassistant.core.catalog_pipeline as cp
    from cadbatchassistant.core.catalog_template_reader import Anchor
    from cadbatchassistant.core.parallel import TaskFailed

    template = tmp_path / "template.dxf"
    template.write_text("x", encoding="utf-8")
    xlsx = tmp_path / "tpl.xlsx"
    xlsx.write_text("x", encoding="utf-8")
    files = []
    for n in ("A1", "A2", "A3", "A4"):
        p = tmp_path / f"{n}.dxf"
        p.write_text("x", encoding="utf-8")
        files.append(p)
    out = tmp_path / "out.xlsx"

    captured: dict = {}

    def fake_map_files(worker, items, **kwargs):
        captured["on_done"] = kwargs["on_done"]
        captured["tasks"] = items
        return [None] * len(items)

    logs: list[str] = []
    progs: list[int] = []
    conv = mock.Mock()
    conv.resolve.return_value = ""
    conv.require_for_dwg.return_value = None
    anchors = [Anchor(field="图号", is_area=False, point_x=1.0, point_y=2.0)]
    with (
        mock.patch.object(cp.dc, "get_converter", return_value=conv),
        mock.patch.object(cp, "map_files", side_effect=fake_map_files),
        mock.patch.object(cp.catalog_excel_writer, "write_catalog_from_template"),
    ):
        result = cp.run_pipeline(
            template,
            xlsx,
            files,
            out,
            log=logs.append,
            progress=progs.append,
            template_anchors=anchors,
        )

    assert result.ok
    on_done = captured["on_done"]
    tasks = captured["tasks"]
    # 模拟并行乱序完成：提交序 2,0,1,3
    on_done(TaskFailed(Exception("取值失败")), 2, tasks[2])
    on_done({"图号": "D-1"}, 0, tasks[0])
    on_done({"图号": "D-3"}, 1, tasks[1])
    on_done({"图号": "D-4"}, 3, tasks[3])

    # 日志编号按完成序连续 1..4（修复前为 3,1,2,4 乱序打印）
    proc_lines = [m for m in logs if m.startswith("  处理 [")]
    nums = [int(line.split("[")[1].split("/")[0]) for line in proc_lines]
    assert nums == [1, 2, 3, 4]
    # on_done 的进度按完成计数单调推进（mock 的 map_files 立即返回后手动
    # 回调，故追加在末尾；修复前为 50→20→35→65 乱跳）
    assert progs[-4:] == [35, 50, 65, 80]
