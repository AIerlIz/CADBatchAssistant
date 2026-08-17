"""基于 GitHub Release 的在线更新。

按职责拆分为子模块：
- constants : 常量 / UpdateError / is_frozen（无依赖共享基础）
- version   : 版本号解析与用户忽略记录
- release   : GitHub 最新 Release 查询
- download  : 更新包下载与强校验（镜像 / 重试 / 取消）
- replace   : exe 替换（PowerShell 命令生成与启动）

公共 API（含测试/GUI 层引用的 urllib / http / subprocess 模块引用）
在本包顶层统一导出，调用方以 `updater.xxx` 形式使用。
"""

from __future__ import annotations

import http.client
import subprocess
import urllib.request

from cadbatchassistant.core.updater.constants import (
    API_TIMEOUT,
    ASSET_NAME,
    GITHUB_REPO,
    MAX_RESPONSE_BYTES,
    SHA256_ASSET_NAME,
    USER_AGENT,
    UpdateError,
    is_frozen,
)
from cadbatchassistant.core.updater.download import (
    CANCEL_MSG,
    _cleanup,
    _download_once,
    _fetch_sha256,
    _mirror_url,
    _parse_sha256,
    _validate_mirror_scheme,
    download_asset,
)
from cadbatchassistant.core.updater.release import (
    _find_assets,
    _request_json,
    check_latest,
)
from cadbatchassistant.core.updater.replace import (
    _replace_script,
    build_replace_command,
    current_exe_path,
    run_replace,
)
from cadbatchassistant.core.updater.version import (
    IGNORE_KEY,
    ignored_version,
    is_ignored,
    is_newer,
    parse_version,
    set_ignored_version,
)

__all__ = [
    "API_TIMEOUT",
    "ASSET_NAME",
    "CANCEL_MSG",
    "GITHUB_REPO",
    "IGNORE_KEY",
    "MAX_RESPONSE_BYTES",
    "SHA256_ASSET_NAME",
    "USER_AGENT",
    "UpdateError",
    "_cleanup",
    "_download_once",
    "_fetch_sha256",
    "_find_assets",
    "_mirror_url",
    "_parse_sha256",
    "_replace_script",
    "_request_json",
    "_validate_mirror_scheme",
    "build_replace_command",
    "check_latest",
    "current_exe_path",
    "download_asset",
    "http",
    "ignored_version",
    "is_frozen",
    "is_ignored",
    "is_newer",
    "parse_version",
    "run_replace",
    "set_ignored_version",
    "subprocess",
    "urllib",
]
