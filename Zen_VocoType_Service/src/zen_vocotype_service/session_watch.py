"""关机检测点②：logind ``PrepareForShutdown`` 监听（T42，计划 2026-0730-0221）。

经 QtDBus 监听系统总线，收到关机预告即触发 ``on_shutdown`` 回调——进程在
关机流程到达「资源拆除」阶段之前自行退出，避免 AppImage/FUSE + Qt 托盘
高危组合的 SIGBUS/SIGSEGV 崩溃及 Apport 弹窗。

红线：

- 连接失败必须静默降级（仅记 warning），🔴 不得影响启动（容器/CI 无系统总线）；
- QtDBus 信号投递依赖 Qt 事件循环——托盘形态由 ``QApplication`` 承载，
  headless 形态由收敛后的 ``QCoreApplication`` 主循环承载（S3）；
- 回调必须轻量（``threading.Event.set`` 线程安全，可被 Qt 信号槽直接调用），
  🔴 禁止阻塞/交互。
"""

from typing import Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtDBus import QDBusConnection

from zen_vocotype_service.logging_setup import logger

#: logind 系统总线定位常量（单一出处）
LOGIND_SERVICE = "org.freedesktop.login1"
LOGIND_PATH = "/org/freedesktop/login1"
LOGIND_MANAGER_IFACE = "org.freedesktop.login1.Manager"
PREPARE_SIGNAL = "PrepareForShutdown"


class SessionShutdownWatcher(QObject):
    """监听 logind ``PrepareForShutdown``，触发优雅退出回调。

    :param on_shutdown: 关机触发回调（服务端为 ``shutdown_event.set``，
        与 SIGTERM/托盘退出汇流同一事件，天然幂等）。
    :param parent: Qt 父子关系宿主（随应用对象存活）。
    """

    def __init__(
        self, on_shutdown: Callable[[], None], parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._on_shutdown = on_shutdown
        #: 订阅是否成功（测试观察口；降级时为 False）
        self._connected = False
        try:
            bus = QDBusConnection.systemBus()
            if not bus.isConnected():
                logger.warning(
                    "系统总线不可达（容器/CI 环境），logind 关机监听静默降级"
                )
                return
            # 🔴 slot 须为 str 形式的 SLOT 签名（"1方法名(参数)"）——PySide6
            # 传 bytes 会被错误拒绝（ValueError: wrong argument values）
            self._connected = bool(
                bus.connect(
                    LOGIND_SERVICE,
                    LOGIND_PATH,
                    LOGIND_MANAGER_IFACE,
                    PREPARE_SIGNAL,
                    self,
                    "1_on_prepare_for_shutdown(bool)",
                )
            )
            if self._connected:
                logger.info("logind PrepareForShutdown 关机监听已接入")
            else:
                logger.warning("logind PrepareForShutdown 信号订阅失败，静默降级")
        except Exception:
            logger.exception("logind 关机监听初始化异常，静默降级")

    @property
    def connected(self) -> bool:
        """logind 信号订阅是否成功接入。"""
        return self._connected

    @Slot(bool)
    def _on_prepare_for_shutdown(self, start: bool) -> None:
        """``PrepareForShutdown(true)``=即将关机 → 触发回调；``false``=取消，忽略。"""
        if not start:
            return
        logger.info("收到 logind PrepareForShutdown 信号，触发优雅退出")
        self._on_shutdown()
