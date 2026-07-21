"""服务端日志封装（loguru，选型八）。

双 sink：控制台 + 组件 ``logs/`` 目录轮转文件；格式含时间/级别/模块/线程名
（线程名对 accept 线程 / 连接线程 / 加载线程 / 推理 worker 排查至关重要）。

🔴 本模块为服务端自有封装，禁止跨组件 import；🔴 全组件禁 print，
统一 ``from zen_vocotype_service.logging_setup import logger``。
"""

import sys

from loguru import logger

from zen_vocotype_service.config import Settings

#: 日志文件名单一出处
LOG_FILE_NAME: str = "service.log"

#: 单文件轮转阈值
LOG_ROTATION: str = "10 MB"

#: 保留份数
LOG_RETENTION: int = 5

#: 日志格式：时间 / 级别 / 模块:函数:行 / 线程名 / 消息
LOG_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
    "{name}:{function}:{line} | {thread.name} | {message}"
)

_configured: bool = False


def setup_logging(settings: Settings) -> None:
    """按配置初始化 loguru 双 sink；重复调用幂等。"""
    global _configured
    if _configured:
        return
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level="INFO", format=LOG_FORMAT)
    logger.add(
        settings.log_dir / LOG_FILE_NAME,
        level="DEBUG",
        format=LOG_FORMAT,
        rotation=LOG_ROTATION,
        retention=LOG_RETENTION,
        encoding="utf-8",
    )
    _configured = True


__all__ = ["logger", "setup_logging"]
