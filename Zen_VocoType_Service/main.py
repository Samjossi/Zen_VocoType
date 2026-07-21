"""Zen_VocoType_Service 入口。

启动时序（协议 §6，🔴 先监听后加载）：

1. 第一行设置 ``MODELSCOPE_CACHE``（任何 funasr/modelscope import 之前，
   顺序敏感，单测固化）
2. 加载配置 → 日志 → 单实例锁（flock + PID）
3. bind Socket 并开始 accept（health/ready 立即可答）
4. 后台线程异步加载模型 + 自检 → 推进状态 ready / error
5. SIGTERM 确定性退出：停止 accept → 通知 worker 停止 → 释放模型
   → 删除 Socket 文件 → 释放锁
"""

import os
import sys

# ⚠️ 顺序敏感（选型五）：必须在任何 funasr/modelscope 导入之前设置，
# 单元测试 test_main_env_order.py 固化该顺序，重构不得破坏
from zen_vocotype_service.config import Settings

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


def _async_load_model(ctx: ServiceContext) -> None:
    """后台加载线程：加载默认模型 + 自检，推进状态 ready / error。"""
    settings = ctx.settings
    manager = ModelManager(settings)
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


def main() -> int:
    settings = _settings_for_env
    setup_logging(settings)
    logger.info("Zen_VocoType_Service 启动中（pid={}）", os.getpid())

    try:
        lock = InstanceLock()
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

    # 主线程驻留至收到停服信号
    shutdown_event.wait()

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
