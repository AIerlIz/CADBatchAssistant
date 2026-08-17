"""更新模块共享基础：常量、异常与运行环境检测（无内部依赖）。

其他子模块均从本模块取常量与 UpdateError，避免循环导入。
"""

from __future__ import annotations

import sys

# GitHub 仓库与更新包信息（与 .github/workflows/build.yml 发布的资产一致）
GITHUB_REPO = "AIerlIz/CADBatchAssistant"
ASSET_NAME = "CADBatchAssistant.exe"
# 随 Release 一并发布的 sha256 校验和资产（名 = 安装包名 + ".sha256"）
SHA256_ASSET_NAME = ASSET_NAME + ".sha256"
API_TIMEOUT = 15
USER_AGENT = "CADBatchAssistant-Updater"
# L6：响应体大小上限（10MB），防止恶意服务器返回超大 body 耗尽内存
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class UpdateError(Exception):
    """更新流程失败（网络 / 数据 / 校验），message 可直接展示给用户。"""


def is_frozen() -> bool:
    """是否打包运行（PyInstaller）；开发模式（python main.py）下返回 False。"""
    return bool(getattr(sys, "frozen", False))
