"""应用统一日志：输出到软件目录 logs/app.log（打包版为 exe 同目录 logs/）。

各模块用 `logging.getLogger(__name__)` 获取 logger；异常处理处用
logger.exception(...) 记录堆栈，GUI 仍保留用户可读提示（日志仅供排查，
不依赖也不阻塞界面）。写入失败（目录只读等）静默跳过，不影响应用。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from cadbatchassistant.core.app_config import software_dir

_LOGGER_NAME = "cadbatchassistant"
_LOG_MAX_BYTES = 1024 * 1024  # 单文件上限 1MB，超出后轮转
_LOG_BACKUP_COUNT = 3  # 保留最多 3 个历史日志（app.log.1 ~ app.log.3）


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根 logger（幂等）：RotatingFileHandler 到 软件目录/logs/app.log。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return  # 已配置（幂等，防止重复 FileHandler 追加行）
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    log_dir = software_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass  # 日志目录不可写时不阻塞应用


def get_logger(name: str) -> logging.Logger:
    """获取应用命名空间下的 logger（自动带上统一前缀）。"""
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
