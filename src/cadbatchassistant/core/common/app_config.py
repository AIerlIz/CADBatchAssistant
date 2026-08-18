"""全局应用配置：JSON 读写、配置目录、目录助手规则、DWG 输出版本。

- OUT_VERSION_CHOICES : DWG 输出版本下拉选项
- load_config / save_config : JSON 配置读写（按配置目录/文件隔离）
- load_app_config / save_app_config / get_oda / get_out_version : 全局配置访问
- software_dir / resource_path / rules_file / load_catalog_rules : 软件目录
  与目录助手规则

本模块不依赖任何 GUI（tkinter），供 core 与 gui 两层共享。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from cadbatchassistant.core.dwg_converter import DEFAULT_OUT_VERSION

# DWG 输出版本下拉选项（三功能面板共用）；默认值取自 dwg_converter.DEFAULT_OUT_VERSION，
# 与转换层默认保持一致，避免多处常量发散
_OUT_VERSION_CHOICES = [
    "ACAD2013",
    "ACAD2010",
    "ACAD2007",
    "ACAD2004",
    "ACAD2000",
]
OUT_VERSION_CHOICES = [DEFAULT_OUT_VERSION, *_OUT_VERSION_CHOICES]

# 全局设置（ODA 路径、DWG 输出版本）存放于统一配置目录，「设置」页与各面板共享
APP_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "CADBatchAssistant"
APP_CONFIG_FILE = APP_CONFIG_DIR / "config.json"


def load_config(config_file: str | Path) -> dict:
    """读取 JSON 配置文件；不存在或损坏时返回空 dict。"""
    try:
        with open(config_file, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置损坏/不存在时返回空
        return {}


def save_config(config_file: str | Path, data: dict) -> None:
    """写入 JSON 配置文件；写失败不抛出（不阻塞使用）。"""
    try:
        Path(config_file).parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001 - 写配置失败不阻塞使用
        pass


# ---------------- 全局配置访问（ODA / DWG 输出版本 / 更新镜像） ----------------


def load_app_config() -> dict:
    """读取全局配置（ODA 路径、DWG 输出版本、更新镜像等），不存在时返回空 dict。"""
    return load_config(APP_CONFIG_FILE)


def save_app_config(updates: dict) -> dict:
    """合并更新全局配置并保存（保留 update_ignore 等其他键），返回新配置。"""
    cfg = load_config(APP_CONFIG_FILE)
    cfg.update(updates)
    save_config(APP_CONFIG_FILE, cfg)
    return cfg


def get_oda() -> str:
    """全局配置中的 ODAFileConverter 路径（未配置时为空串）。"""
    return str(load_app_config().get("oda", "")).strip()


def get_out_version() -> str:
    """全局配置中的 DWG 输出版本（默认与 dwg_converter 一致）。"""
    return (
        str(load_app_config().get("version", DEFAULT_OUT_VERSION)).strip()
        or DEFAULT_OUT_VERSION
    )


def get_max_workers() -> int:
    """全局并行 worker 数：环境变量 CADBATCH_MAX_WORKERS 优先，其次
    config.json 的 max_workers 键；缺失/非法时默认 4（合法范围 1-64）。

    三功能面板的批量并行共用该值（AutoExecutor 的上限）；老机器/大批量
    可在「设置」页调整或直接改 config.json。
    """
    raw = os.environ.get("CADBATCH_MAX_WORKERS")
    if raw is None:
        raw = load_app_config().get("max_workers", 4)
    if isinstance(raw, bool):  # bool 是 int 子类，但 True/False 不是合法 worker 数
        return 4
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 4
    return n if 1 <= n <= 64 else 4


# ---------------- 软件目录 / 模板库 / 目录助手规则 ----------------

# 目录助手（catalog）规则默认值：软件目录 config.json 的 rules 段可覆盖
DEFAULT_CATALOG_RULES = {
    "data_rows_per_page": 50,
    "cover_pages": 1,
}


# 源码运行时的项目根目录（向上查找 pyproject.toml，不再依赖固定目录层级）
def _find_project_root(start: Path) -> Path:
    for p in (start, *start.parents):
        if (p / "pyproject.toml").is_file():
            return p
    return start


_PROJECT_ROOT = _find_project_root(Path(__file__).resolve())


def software_dir() -> Path:
    """软件目录：exe 所在目录（打包运行）或项目根（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return _PROJECT_ROOT


def resource_path(name: str) -> str:
    """返回打包进 exe 的资源文件路径（如 "assets/logo.ico"）。

    打包运行时从 PyInstaller 解压目录 sys._MEIPASS 取；源码运行时取项目根。
    供窗口图标等读取随包分发的资源。
    """
    base = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
    return str(base / name)


def rules_file() -> Path:
    """目录助手规则配置文件：软件目录下的 config.json（可手动编辑）。"""
    return software_dir() / "config.json"


def load_catalog_rules() -> dict:
    """读取目录助手规则（软件目录 config.json 的 rules 段），缺省返回内置默认。"""
    cfg = load_config(rules_file())
    rules = dict(DEFAULT_CATALOG_RULES)
    user_rules = cfg.get("rules")
    if isinstance(user_rules, dict):
        rules.update({k: v for k, v in user_rules.items() if v not in (None, "")})
    return rules
