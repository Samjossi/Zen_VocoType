"""Zen_VocoType_Service 入口。

启动时序（协议 §6，🔴 先监听后加载）：

1. 第一行设置 ``MODELSCOPE_CACHE``（任何 funasr/modelscope import 之前，
   顺序敏感，单测固化）
2. 加载配置 → 日志 → 单实例锁（flock + PID）
3. bind Socket 并开始 accept（health/ready 立即可答）
4. 后台线程异步加载模型 + 自检 → 推进状态 ready / error
5. 托盘（可选增强）：有显示环境时主线程跑 Qt 事件循环承载
   QSystemTrayIcon；headless / tray_enabled=false / 托盘不可用时
   自动降级为 ``shutdown_event.wait()`` 纯控制台驻留
6. SIGTERM / 托盘「退出服务」确定性退出（同一 shutdown_event 汇流）：
   停止 accept → 通知 worker 停止 → 释放模型 → 删除 Socket 文件 → 释放锁
"""

import os
import sys
import warnings

# ⚠️ 顺序敏感（选型五）：必须在任何 funasr/modelscope 导入之前设置，
# 单元测试 test_main_env_order.py 固化该顺序，重构不得破坏
from zen_vocotype_protocol.paths import ensure_user_dir
from zen_vocotype_protocol.user_config import load_user_config
from zen_vocotype_service.config import CONFIG_FILE, Settings

# 捕获配置加载期 warning（如用户配置文件损坏回退），日志就绪后转述——🔴 禁止静默
with warnings.catch_warnings(record=True) as _early_warnings:
    warnings.simplefilter("always")
    _settings_for_env = Settings()
# 硬设置（非 setdefault）：模型缓存位置是产品契约，外部同名环境变量不得劫持
os.environ["MODELSCOPE_CACHE"] = str(_settings_for_env.models_dir)

import signal
import threading

from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.inference.worker import InferenceWorker
from zen_vocotype_service.instance_lock import InstanceLock, InstanceLockError
from zen_vocotype_service.logging_setup import logger, setup_logging
from zen_vocotype_service.models.manager import ModelManager
from zen_vocotype_service.models.loader import ModelLoadError
from zen_vocotype_service.server import SocketServer, SocketPathError
from zen_vocotype_service.state import ServiceState


def _lock_path_for(settings: Settings) -> str:
    """按 Socket 路径选择锁文件：dev Socket 用 dev 锁（dev/正式并行互不干扰，
    阶段 3 验收标准 4；路径常量唯一出处为契约库 ``paths``）。"""
    from zen_vocotype_protocol.paths import (
        DEV_SERVICE_LOCK_PATH,
        DEV_SOCKET_PATH,
        SERVICE_LOCK_PATH,
    )

    if settings.socket_path == DEV_SOCKET_PATH:
        return DEV_SERVICE_LOCK_PATH
    return SERVICE_LOCK_PATH


def _async_load_model(ctx: ServiceContext) -> None:
    """后台加载线程：加载默认模型 + 自检，推进状态 ready / error。"""
    settings = ctx.settings
    # state 传入 manager：未缓存模型下载时置 downloading 标记（托盘轮询呈现）
    manager = ModelManager(settings, ctx.state)
    ctx.model_manager = manager
    try:
        manager.load_initial(settings.default_model)
    except ModelLoadError as exc:
        logger.error("模型加载失败: {}", exc)
        ctx.state.mark_error(str(exc))
        return
    except Exception as exc:  # 🔴 禁止静默：任何意外失败都要暴露为 error 状态
        logger.exception("模型加载出现未预期异常")
        ctx.state.mark_error(f"未预期异常: {exc}")
        return
    worker = InferenceWorker(
        settings,
        manager,
        on_model_switched=ctx.state.update_model,
    )
    worker.start()
    ctx.worker = worker
    ctx.state.mark_ready(settings.default_model)
    logger.info("服务就绪（ready），当前模型: {}", settings.default_model)


def _create_tray_if_available(
    settings: Settings, ctx: ServiceContext, shutdown_event: threading.Event
):
    """托盘创建（可选增强，任何失败降级为无托盘，不阻断服务）。

    降级路径（任一命中即返回 None）：

    - 配置 ``tray_enabled=false``（服务器/CI 部署）
    - 无显示环境（无 DISPLAY / WAYLAND_DISPLAY，headless）
    - 系统托盘不可用 / Qt 初始化异常
    """
    if not settings.tray_enabled:
        logger.info("配置 tray_enabled=false，纯控制台模式运行")
        return None, None
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        logger.warning("无显示环境（headless），托盘降级为纯控制台模式")
        return None, None
    try:
        # 🔴 延迟导入：严禁提升到模块顶部（MODELSCOPE_CACHE 顺序红线 +
        # headless 降级路径完全不 import Qt）
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        from zen_vocotype_service.tray.tray import ServiceTray

        app = QApplication(sys.argv)  # 必须在主线程
        app.setQuitOnLastWindowClosed(False)  # 🔴 无窗口应用，防意外退出
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("系统托盘不可用，降级为纯控制台模式")
            return None, None
        tray = ServiceTray(ctx, poll_interval_ms=settings.tray_poll_interval_ms)
        tray.quit_requested.connect(shutdown_event.set)  # 与 SIGTERM 汇流
        tray.show()
        logger.info("系统托盘已启动")
        return app, tray
    except Exception:
        logger.exception("托盘初始化失败，降级为纯控制台模式（服务不受影响）")
        return None, None


def _run_with_tray(app, shutdown_event: threading.Event) -> None:
    """Qt 事件循环驻留；200ms 检查停服信号 → quit → 返回后走统一退出序列。"""
    from PySide6.QtCore import QTimer

    watchdog = QTimer()
    watchdog.setInterval(200)
    watchdog.timeout.connect(
        lambda: app.quit() if shutdown_event.is_set() else None
    )
    watchdog.start()
    app.exec()



def _models_dir_source() -> str:
    """判定 models_dir 生效来源层（R9 排障：配置链复杂度上升后须可自查）。

    覆盖链：默认值 → 包内 config.yaml → 用户配置文件 → 环境变量（后者优先）。
    """
    if os.environ.get("ZEN_VOCOTYPE_SERVICE_MODELS_DIR") is not None:
        return "环境变量"
    if "models_dir" in load_user_config():
        return "用户配置文件"
    try:
        import yaml

        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "models_dir" in data:
            return "包内 config.yaml"
    except Exception:  # 仅为来源标注，任何失败按默认值层报告（不掩盖真配置错误）
        pass
    return "默认值（契约库 XDG）"


def main() -> int:
    # --version 构建冒烟探针（阶段 4 T4.2）：须在日志初始化前可答、零写盘
    if "--version" in sys.argv:
        from zen_vocotype_service.version import SERVICE_VERSION

        logger.info("Zen_VocoType_Service v{}", SERVICE_VERSION)
        return 0

    settings = _settings_for_env
    setup_logging(settings)
    logger.info("Zen_VocoType_Service 启动中（pid={}）", os.getpid())

    # 配置加载期 warning 转述（R8/R9：用户配置损坏回退等不得只停留在 stderr）
    for w in _early_warnings:
        logger.warning("配置加载警告: {}", w.message)
    logger.info(
        "模型目录生效值: {}（来源：{}）",
        settings.models_dir,
        _models_dir_source(),
    )

    # 模型目录创建 + 可写校验（阶段 4 T4.1）：失败记明确错误日志，
    # 🔴 禁止静默回退——旧事故「名义在线实为缓存」的教训；此处不退出，
    # 模型已就位直载场景只读目录仍可用，加载/下载失败由 loader 显式报错
    try:
        ensure_user_dir(settings.models_dir)
    except OSError as exc:
        logger.error(
            "模型目录不可写（{}）：{}——模型下载将失败，仅已就位模型可加载；"
            "请检查 models_dir 配置或目录权限",
            settings.models_dir,
            exc,
        )

    try:
        lock = InstanceLock(_lock_path_for(settings))
        lock.acquire()
    except InstanceLockError as exc:
        logger.error("{}", exc)
        return 1

    state = ServiceState()
    ctx = ServiceContext(settings, state)
    server = SocketServer(settings, ctx)
    shutdown_event = threading.Event()

    def _on_sigterm(signum, frame) -> None:
        logger.info("收到信号 {}，开始确定性退出", signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGINT, _on_sigterm)

    try:
        server.start(background=True)  # 🔴 先监听：accept 不等待任何模型操作
    except (SocketPathError, OSError) as exc:
        logger.error("Socket 监听失败: {}", exc)
        lock.release()
        return 1

    loader_thread = threading.Thread(
        target=_async_load_model, args=(ctx,), name="model-loader", daemon=True
    )
    loader_thread.start()

    # 主线程驻留至收到停服信号（托盘为可选增强，app 须保持存活至 exec 结束）
    app, tray = _create_tray_if_available(settings, ctx, shutdown_event)
    if app is not None:
        _run_with_tray(app, shutdown_event)  # Qt 事件循环驻留
    else:
        shutdown_event.wait()  # 无托盘降级：原路径驻留

    logger.info("执行退出序列")
    server.shutdown()
    if ctx.worker is not None:
        ctx.worker.stop()
    if ctx.model_manager is not None:
        ctx.model_manager.release()
    lock.release()
    logger.info("Zen_VocoType_Service 已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
