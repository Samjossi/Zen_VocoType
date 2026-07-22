"""启动器日志基座（loguru 双 sink）。

- 控制台 + ``logs/`` 轮转文件（轮转 10MB × 保留 5 份，命名常量见下）
- 格式含时间/级别/模块
- 🔴 全组件禁 print；本模块为启动器日志唯一配置入口，禁止跨组件 import
  （与 Service/Client 的 logging_setup 各自独立实现，大纲原则 7）
"""

import sys
from pathlib import Path

from loguru import logger

#: 单文件轮转上限（字节）：10MB
LOG_ROTATION_BYTES: int = 10 * 1024 * 1024

#: 轮转文件保留份数
LOG_RETENTION_COUNT: int = 5

#: 日志格式：时间 | 级别 | 模块:函数:行 | 消息
_LOG_FORMAT: str = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
    "{name}:{function}:{line} | {message}"
)


def setup_logging(log_dir, level: str = "INFO") -> Path:
    """初始化 loguru 双 sink。应在入口最早调用，且仅调用一次。

    :param log_dir: 日志目录（配置项 Settings.log_dir，须为绝对路径）
    :param level: 控制台 sink 级别；文件 sink 固定 DEBUG 便于事后追查
    :returns: 日志文件路径（供失败通知提示用户查看位置）
    """
    log_path = Path(log_dir)
    log_file = log_path / "launcher.log"

    logger.remove()  # 去除 loguru 默认 stderr sink，避免重复
    logger.add(sys.stderr, level=level, format=_LOG_FORMAT)
    # 日志不可写容错（T4.6 验收口径：不崩溃、stderr 兜底、记 warning）；
    # 返回值保持既定路径（供失败通知提示位置），仅 sink 创建受保护
    try:
        log_path.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level="DEBUG",
            format=_LOG_FORMAT,
            rotation=LOG_ROTATION_BYTES,
            retention=LOG_RETENTION_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("日志文件 sink 初始化失败（{}），仅 stderr 输出：{}", log_path, exc)
    logger.info("启动器日志初始化完成，日志文件：{}", log_file)
    return log_file
