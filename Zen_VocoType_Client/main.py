"""Zen_VocoType_Client 入口。

用法：

- ``python main.py``              正常启动（热键/录音/识别/输出/托盘全装配）
- ``python main.py --screenshot <目录>``  托盘布局截图自检（开发自查工具，
  见 ``tray/selftest.py``；🔴 仅开发用途）

退出码：0 正常退出；2 配置校验失败；3 录音设备不可用；4 热键后端启动失败；
5 已有客户端实例运行（单实例锁冲突）。
"""

import sys
from pathlib import Path


def main() -> int:
    # 启动顺序：日志 → 配置校验 → 装配启动 → Qt 事件循环
    from zen_vocotype_client.config import Settings, validate_startup
    from zen_vocotype_client.logging_setup import setup_logging

    settings = Settings()
    setup_logging(settings.log_dir)

    try:
        validate_startup(settings)
    except ValueError as exc:
        from loguru import logger

        logger.error("配置校验失败：{}", exc)
        return 2

    if "--screenshot" in sys.argv:
        idx = sys.argv.index("--screenshot")
        try:
            output_dir = Path(sys.argv[idx + 1]).resolve()
        except IndexError:
            from loguru import logger

            logger.error("用法: python main.py --screenshot <输出目录>")
            return 2
        from zen_vocotype_client.tray.selftest import run_screenshot_mode

        return run_screenshot_mode(output_dir)

    # 单实例锁（阶段 3 T3.2）：正常启动路径抢锁；截图自检为开发工具不抢锁
    from loguru import logger

    from zen_vocotype_client.instance_lock import (
        InstanceLock,
        InstanceLockError,
        lock_path_for,
    )

    lock = InstanceLock(lock_path_for(settings.socket_path))
    try:
        lock.acquire()
    except InstanceLockError as exc:
        logger.error("{}", exc)
        return 5

    from PySide6.QtWidgets import QApplication

    from zen_vocotype_client.app import ClientApp

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    client = ClientApp(settings)
    code = client.start()
    if code != 0:
        client.shutdown()
        lock.release()
        return code
    if client._tray is not None:
        client._tray.quit_requested.connect(app.quit)
    app.aboutToQuit.connect(client.shutdown)
    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
