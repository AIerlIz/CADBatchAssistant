"""core.app_config 纯逻辑单测（拆分自 common.py 后的语义回归）。

覆盖配置读写、全局配置访问、目录助手规则与软件目录定位；
全部用 tmp_path/monkeypatch 隔离，不触碰真实 APPDATA 配置。
"""

from __future__ import annotations

import json
from pathlib import Path

from cadbatchassistant.core.common import app_config


def test_load_config_valid(tmp_path: Path) -> None:
    f = tmp_path / "cfg.json"
    f.write_text('{"oda": "C:\\\\oda.exe"}', encoding="utf-8")
    assert app_config.load_config(f) == {"oda": "C:\\oda.exe"}


def test_load_config_missing(tmp_path: Path) -> None:
    assert app_config.load_config(tmp_path / "nope.json") == {}


def test_load_config_corrupt(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("{invalid", encoding="utf-8")
    assert app_config.load_config(f) == {}


def test_load_config_non_dict(tmp_path: Path) -> None:
    f = tmp_path / "arr.json"
    f.write_text("[1,2]", encoding="utf-8")
    assert app_config.load_config(f) == {}


def test_save_config_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "out.json"
    app_config.save_config(f, {"a": 1, "b": "中文"})
    assert app_config.load_config(f) == {"a": 1, "b": "中文"}


def test_save_app_config_merges(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "APP_CONFIG_FILE", tmp_path / "cfg.json")
    app_config.save_config(app_config.APP_CONFIG_FILE, {"keep": 1})
    cfg = app_config.save_app_config({"oda": "x"})
    assert cfg == {"keep": 1, "oda": "x"}


def test_get_oda_and_out_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "APP_CONFIG_FILE", tmp_path / "cfg.json")
    app_config.save_app_config({"oda": " C:\\oda.exe ", "version": "ACAD2013"})
    assert app_config.get_oda() == "C:\\oda.exe"
    assert app_config.get_out_version() == "ACAD2013"


def test_get_out_version_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "APP_CONFIG_FILE", tmp_path / "cfg.json")
    app_config.save_app_config({})
    assert app_config.get_out_version() == "ACAD2018"


def test_load_catalog_rules_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "rules_file", lambda: tmp_path / "rules.json")
    rules = app_config.load_catalog_rules()
    assert rules["data_rows_per_page"] == 50
    assert rules["cover_pages"] == 1


def test_load_catalog_rules_override_and_filter(tmp_path: Path, monkeypatch) -> None:
    f = tmp_path / "rules.json"
    f.write_text(
        json.dumps({"rules": {"data_rows_per_page": 60, "cover_pages": None}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_config, "rules_file", lambda: f)
    rules = app_config.load_catalog_rules()
    assert rules["data_rows_per_page"] == 60
    # 空值被过滤，回退默认
    assert rules["cover_pages"] == 1


def test_software_dir_is_project_root() -> None:
    d = app_config.software_dir()
    assert (d / "src").is_dir()
    assert (d / "pyproject.toml").is_file()


def test_resource_path_joins() -> None:
    p = app_config.resource_path("assets/logo.ico")
    assert Path(p).name == "logo.ico"
