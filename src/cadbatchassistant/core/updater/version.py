"""版本号解析与用户忽略记录。

- parse_version : tag（如 v1.2.3）→ (major, minor, patch)
- is_newer      : 最新版 > 当前版 判定
- is_ignored    : 用户忽略的版本 tag 判定（config update_ignore）
"""

from __future__ import annotations

# 配置 key：用户忽略的版本 tag（如 "v1.1.0"）
IGNORE_KEY = "update_ignore"


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """把 tag（如 v1.2.3 / 1.2.3）解析为 (major, minor, patch)；解析失败返回 None。"""
    text = tag.strip()
    text = text.removeprefix("v")
    parts = text.split(".")
    if len(parts) < 3:
        return None
    try:
        nums = tuple(int(p) for p in parts[:3])
    except ValueError:
        return None
    return nums  # type: ignore[return-value]


def is_newer(
    latest: tuple[int, int, int] | None, current: tuple[int, int, int] | None
) -> bool:
    """latest > current 视为有新版本；任一解析失败返回 False。"""
    if latest is None or current is None:
        return False
    return latest > current


def is_ignored(latest_tag: str, ignored_tag: str | None) -> bool:
    """最新版本 tag 是否等于用户忽略的版本（相等则不再提示）。"""
    return bool(ignored_tag) and ignored_tag == latest_tag


def ignored_version() -> str | None:
    """读取用户忽略的版本 tag（配置 update_ignore）；无则返回 None。"""
    from cadbatchassistant.core.app_config import load_app_config

    v = load_app_config().get(IGNORE_KEY, "")
    return str(v).strip() or None


def set_ignored_version(tag: str) -> None:
    """保存（或传空字符串清除）用户忽略的版本 tag。

    合并写入配置，保留 update_mirror 等其他配置项。
    """
    from cadbatchassistant.core.app_config import save_app_config

    save_app_config({IGNORE_KEY: tag})
