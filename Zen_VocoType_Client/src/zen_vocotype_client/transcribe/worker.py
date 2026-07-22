"""网络 worker（选型一：QThread worker + 信号槽回主线程）。

线程模型：本对象 ``moveToThread`` 到网络 QThread；全部 Socket I/O 在该线程
内进行，对外只经 Qt 信号与主线程通信（🔴 worker 线程内禁止触碰 UI/状态机）。

连接管理（选型二）：

- 启动 ``probe()`` health 探测一次，托盘即时展示服务端状态
- 识别请求时懒连接长连复用；连接失败/断线**自动重连一次**，仍失败则置
  「未连接」态并通知主线程（🔴 禁止后台无限重试刷日志；
  用户可经托盘菜单 ``retry_requested`` 手动重试）
- 版本不兼容 → 断开 + ``sig_version_mismatch``（托盘红态 + 通知）
"""

from __future__ import annotations

from loguru import logger
from PySide6.QtCore import QObject, Signal, Slot

from .client import (
    ProtocolClient,
    RequestFailedError,
    ServiceUnavailableError,
    VersionMismatchError,
)

#: 服务端状态文本（信号第一参，主线程据此映射托盘状态色）
STATUS_DISCONNECTED = "disconnected"  # 服务端未运行/连接中断
STATUS_LOADING = "loading"  # 服务端在线但模型未就绪
STATUS_READY = "ready"
STATUS_ERROR = "error"  # 服务端在线但自报 error


class NetworkWorker(QObject):
    """网络 worker：连接管理 + health 探测 + 识别请求。"""

    #: (status, detail)：服务端状态变化（托盘持续状态通道）
    sig_service_status = Signal(str, str)
    #: (payload)：识别成功（含 text/confidence/duration）
    sig_recognize_done = Signal(dict)
    #: (code, message)：识别失败（协议错误码透传；code=0 表示连接层失败）
    sig_recognize_failed = Signal(int, str)
    #: (detail)：协议版本不兼容（已断开；托盘红态 + 通知明确报错）
    sig_version_mismatch = Signal(str)

    def __init__(self, socket_path: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = ProtocolClient(socket_path)

    # ------------------------------------------------------------------ 槽

    @Slot()
    def probe(self) -> None:
        """health 探测（启动时/托盘「重试连接」/LOADING 轮询共用入口）。

        连接健康时直接复用长连接发 health，不重建——轮询期间若识别长连接
        健康存在，反复 close/connect 属无谓重建（``connect()`` 会先 close 旧连接）。
        """
        try:
            if not self._client.connected:
                self._client.connect()
            payload = self._client.health()
        except VersionMismatchError as exc:
            logger.error("协议版本不兼容：{}", exc)
            self.sig_version_mismatch.emit(str(exc))
            return
        except (ServiceUnavailableError, RequestFailedError) as exc:
            logger.warning("health 探测失败：{}", exc)
            self._client.close()
            self.sig_service_status.emit(STATUS_DISCONNECTED, "服务端未运行")
            return
        self._emit_status_from_health(payload)

    @Slot(bytes)
    def recognize(self, pcm: bytes) -> None:
        """识别请求：懒连接长连复用，断线重连一次后仍失败则报错。"""
        if not self._ensure_connected():
            self.sig_recognize_failed.emit(0, "服务端未运行，无法识别")
            return
        try:
            payload = self._client.recognize(pcm)
        except VersionMismatchError as exc:
            logger.error("协议版本不兼容：{}", exc)
            self.sig_version_mismatch.emit(str(exc))
            return
        except ServiceUnavailableError as exc:
            logger.warning("识别请求连接中断：{}", exc)
            self.sig_service_status.emit(STATUS_DISCONNECTED, "连接中断")
            self.sig_recognize_failed.emit(0, "与服务端的连接中断")
            return
        except RequestFailedError as exc:
            logger.info("识别被服务端拒绝：[{}] {}", exc.code, exc.message)
            self.sig_recognize_failed.emit(exc.code, exc.message)
            return
        logger.info("识别完成：{} 字符", len(payload.get("text", "")))
        self.sig_recognize_done.emit(payload)

    @Slot()
    def shutdown(self) -> None:
        """关闭长连接（应用退出序列）。"""
        self._client.close()

    # ------------------------------------------------------------------ 内部

    def _ensure_connected(self) -> bool:
        """确保连接可用：懒连接 + 断线后**仅重连一次**（选型二红线）。"""
        if self._client.connected:
            return True
        for attempt in (1, 2):  # 首次尝试 + 唯一一次重连
            try:
                self._client.connect()
                # 连接建立后立即版本握手（health 兼作握手请求）
                payload = self._client.health()
            except VersionMismatchError as exc:
                logger.error("协议版本不兼容：{}", exc)
                self.sig_version_mismatch.emit(str(exc))
                return False
            except (ServiceUnavailableError, RequestFailedError) as exc:
                logger.warning("连接服务端失败（第 {} 次）：{}", attempt, exc)
                continue
            self._emit_status_from_health(payload)
            return True
        self.sig_service_status.emit(STATUS_DISCONNECTED, "服务端未运行")
        return False

    def _emit_status_from_health(self, payload: dict) -> None:
        """按 health 响应映射服务端状态信号。"""
        status = payload.get("status", "")
        model = payload.get("current_model") or "?"
        if status == "ready":
            self.sig_service_status.emit(STATUS_READY, str(model))
        elif status == "error":
            self.sig_service_status.emit(STATUS_ERROR, "服务端自报错误")
        else:
            self.sig_service_status.emit(STATUS_LOADING, f"模型加载中（{status}）")
