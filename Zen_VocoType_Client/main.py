"""Zen_VocoType_Client 入口。

用法：

- ``python main.py``              正常启动（热键/录音/识别/输出/托盘全装配）
- ``python main.py --screenshot <目录>``  托盘布局截图自检（开发自查工具，
  见 ``tray/selftest.py``；🔴 仅开发用途）
- ``python main.py --version``          打印版本并退出（构建冒烟探针）

退出码：0 正常退出；2 配置校验失败；3 录音设备不可用；4 热键后端启动失败；
5 已有客户端实例运行（单实例锁冲突）；6 无显示环境（headless，T41）。
"""

import os
import signal
import sys
from pathlib import Path


def display_available() -> bool:
    """显示环境探测（🔴 QApplication 创建前必须先探测：headless 下 Qt 会
    SIGABRT 硬崩而非抛 Python 异常，无法经 try 捕获降级——与 Service/Launcher
    同款防御，T41 补齐三组件对齐；2026-07-23 systemd 无显示环境实机事故）。"""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main() -> int:
    # --version 构建冒烟探针（阶段 4 T4.2）：须在配置/日志初始化前可答、零写盘
    if "--version" in sys.argv:
        from loguru import logger

        from zen_vocotype_client import __version__

        logger.info("Zen_VocoType_Client v{}", __version__)
        return 0

    # 启动顺序：日志 → 配置校验 → 装配启动 → Qt 事件循环
    from zen_vocotype_client.config import Settings, validate_startup
    from zen_vocotype_client.logging_setup import setup_logging

    try:
        # Settings 构造本身即执行字段校验（如 recordings_dir 绝对路径红线），
        # pydantic ValidationError 为 ValueError 子类，与启动校验同一失败通道
        settings = Settings()
    except ValueError as exc:
        from loguru import logger

        logger.error("配置校验失败：{}", exc)
        return 2
    setup_logging(settings.log_dir)

    # 显示环境探测（T41）：须在 validate_startup 与 Qt 之前——validate_startup
    # 经 hotkey.combo 间接 import pynput，pynput 在 import 期即连 X（headless
    # 下 ImportError），Qt 更是 SIGABRT 硬崩（非 Python 异常，无法捕获降级）；
    # headless 属确定性环境错误，早失败不触锁、不触 Qt/pynput。
    # --screenshot 分支亦在其后：selftest 第 1 步即在当前桌面会话启动真实
    # 托盘，本身就需要显示环境，探测不构成误伤
    if not display_available():
        from loguru import logger

        logger.error(
            "无显示环境（DISPLAY/WAYLAND_DISPLAY 均未设置），客户端无法运行："
            "托盘/热键/粘贴均依赖图形会话。请在已登录的桌面会话内启动；"
            "headless 部署请仅运行服务端"
        )
        return 6

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
    from zen_vocotype_client.session_watch import SessionShutdownWatcher

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

    # --- 关机优雅退出触发源（T42，计划 2026-0730-0221）：全部挂接须在
    # client.start() 成功之后、app.exec() 之前，避免启动失败早退路径被干扰；
    # 触发源只做 app.quit()（轻量投递），实际清理经 aboutToQuit → shutdown()
    # 在事件循环内完成，无重入风险；清理幂等由 ClientApp._shutdown_done 保证
    # 检测点 ①：GNOME 会话注销/关机（Qt6 xcb 插件自动注册会话客户端，
    # 🔴 禁止交互/阻塞/弹窗，不调用 session.cancel()）
    app.commitDataRequest.connect(lambda _session: app.quit())
    # 检测点 ②：logind PrepareForShutdown 兜底（无系统总线静默降级）
    SessionShutdownWatcher(app.quit, parent=app)
    # SIGTERM/SIGINT：README 明示的退出路径必须确定性清理
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
