"""连接管理与请求分发（选型一：每连接一守护线程）。

- 每连接独立 ``MessageBuffer``（协议 §7-3，🔴 禁止跨连接共享）
- 入站校验顺序：必填字段（1004）→ 版本兼容（1003）→ 未知 action（1002）
  → 已定义未实现 action（1005）→ 路由到处理器
- 🔴 任何处理器异常转化为协议错误响应，禁止连接悬挂（协议 §7-2）
- 帧解析失败：非致命错误返回 1001；头/体超限（致命）记日志并断连（协议 §7-1）
"""

import socket
import threading

from zen_vocotype_protocol import actions, errors
from zen_vocotype_protocol.frames import (
    MAX_RESPONSE_HEADER_BYTES,
    FrameError,
    MessageBuffer,
    encode_frame,
)
from zen_vocotype_protocol.version import PROTOCOL_VERSION, is_compatible
from zen_vocotype_protocol.version import parse_version

from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers import HANDLERS
from zen_vocotype_service.logging_setup import logger
from zen_vocotype_service.protocol_io import ProtocolError, build_response

#: 单连接 recv 缓冲区大小
RECV_CHUNK_BYTES: int = 64 * 1024

#: recv 超时（秒）：周期性检查停服标志
RECV_TIMEOUT_S: float = 0.5

#: 未预期异常时按 action 映射的兜底错误码（🔴 禁止擅自新增错误码）
_FALLBACK_ERROR_CODES: dict[str, int] = {
    actions.ACTION_RECOGNIZE: errors.ERR_RECOGNITION_FAILED,
    actions.ACTION_MODEL_SWITCH: errors.ERR_MODEL_SWITCH_FAILED,
    actions.ACTION_AUDIO_CHUNK: errors.ERR_RECOGNITION_FAILED,
}


def validate_inbound(header: dict) -> None:
    """入站请求头校验：必填字段 → 版本兼容 → action 合法性。

    :raises ProtocolError: 携带对应协议错误码
    """
    for field in ("action", "protocol_version", "request_id"):
        if header.get(field) is None:
            raise ProtocolError(
                errors.ERR_MISSING_FIELD, f"请求头缺少必填字段: {field}"
            )
    remote_version = header["protocol_version"]
    try:
        compatible = is_compatible(PROTOCOL_VERSION, str(remote_version))
    except Exception:
        compatible = False
    if not compatible:
        raise ProtocolError(
            errors.ERR_PROTOCOL_VERSION_MISMATCH,
            f"协议版本不兼容: 客户端 {remote_version} / 服务端 {PROTOCOL_VERSION}",
        )
    action = header["action"]
    if action not in actions.ALL_ACTIONS:
        raise ProtocolError(errors.ERR_UNKNOWN_ACTION, f"未知 action: {action!r}")
    if action not in HANDLERS:
        raise ProtocolError(
            errors.ERR_ACTION_NOT_SUPPORTED,
            f"action {action!r} 已定义但本版本未实现",
        )


def dispatch(header: dict, body: bytes, ctx: ServiceContext, owner: object = None) -> dict:
    """校验并路由一个请求帧，返回响应头字典（成功或错误）。

    ``owner`` 为连接令牌（ConnectionHandler 实例），仅 ``audio_chunk``
    处理器需要（会话绑定连接，协议 v1.1 §3.6）。
    """
    try:
        validate_inbound(header)
        action = header["action"]
        if action == actions.ACTION_AUDIO_CHUNK:
            payload = HANDLERS[action](header, body, ctx, owner)
        else:
            payload = HANDLERS[action](header, body, ctx)
        return build_response(header, ok=True, payload=payload)
    except ProtocolError as exc:
        return build_response(
            header, ok=False, error_code=exc.code, error_message=exc.message
        )
    except Exception as exc:  # 🔴 禁止连接悬挂：未预期异常必须转为错误响应
        logger.exception("处理器未预期异常 action={}", header.get("action"))
        code = _FALLBACK_ERROR_CODES.get(
            str(header.get("action")), errors.ERR_RECOGNITION_FAILED
        )
        return build_response(
            header, ok=False, error_code=code, error_message=f"内部错误: {exc}"
        )


class ConnectionHandler:
    """单连接收发循环（守护线程内运行）。"""

    def __init__(
        self,
        conn: socket.socket,
        peer: str,
        ctx: ServiceContext,
        stop_event: threading.Event,
        on_close,
    ) -> None:
        self._conn = conn
        self._peer = peer
        self._ctx = ctx
        self._stop_event = stop_event
        self._on_close = on_close
        self.thread = threading.Thread(
            target=self._run, name=f"conn-{peer}", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        try:
            self._conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._conn.close()
        except OSError:
            pass

    def _run(self) -> None:
        buffer = MessageBuffer()  # 🔴 每连接独立实例（协议 §7-3）
        logger.debug("连接建立: {}", self._peer)
        try:
            self._conn.settimeout(RECV_TIMEOUT_S)
            while not self._stop_event.is_set():
                try:
                    data = self._conn.recv(RECV_CHUNK_BYTES)
                except socket.timeout:
                    # 空转驱动 audio_chunk 会话空闲清理（周期兜底，协议 v1.1 §3.6-3）
                    self._ctx.chunk_sessions.sweep_idle()
                    continue
                if not data:
                    break  # 对端关闭
                buffer.feed(data)
                if not self._drain(buffer):
                    break
        except OSError as exc:
            if not self._stop_event.is_set():
                logger.warning("连接 {} 收发异常: {}", self._peer, exc)
        finally:
            # 🔴 连接断开即销毁其 audio_chunk 会话（协议 v1.1 §3.6-2，禁止残留）
            self._ctx.chunk_sessions.destroy_for_connection(self)
            self.close()
            self._on_close(self)
            logger.debug("连接关闭: {}", self._peer)

    def _drain(self, buffer: MessageBuffer) -> bool:
        """取出并处理所有完整帧；返回 False 表示连接必须关闭。"""
        while True:
            try:
                frame = buffer.next_frame()
            except FrameError as exc:
                if exc.fatal:
                    logger.error("连接 {} 帧超防御性上限，断连: {}", self._peer, exc)
                    return False
                logger.warning("连接 {} 帧解析失败: {}", self._peer, exc)
                self._send_error(errors.ERR_MALFORMED_FRAME, str(exc))
                continue
            if frame is None:
                return True
            header, body = frame
            response = dispatch(header, body, self._ctx, owner=self)
            try:
                # 响应方向用放宽上限：长音频识别文本可超请求方向 64KB
                #（契约库 MAX_RESPONSE_HEADER_BYTES，v1.4 真实缺陷修复——
                # 2h 文本 ≈ 123KB 曾致响应编码 fatal 断连）
                out = encode_frame(response, max_header_bytes=MAX_RESPONSE_HEADER_BYTES)
            except FrameError as exc:
                # 🔴 禁止静默：响应编码失败必须记日志再断连
                logger.error("连接 {} 响应编码失败，断连: {}", self._peer, exc)
                return False
            self._send(out)

    def _send_error(self, code: int, message: str) -> None:
        """无请求上下文时的错误帧（request_id/action 置空）。"""
        frame = encode_frame(
            {
                "action": None,
                "protocol_version": PROTOCOL_VERSION,
                "request_id": None,
                "ok": False,
                "error": {"code": code, "message": message},
            }
        )
        self._send(frame)

    def _send(self, data: bytes) -> None:
        try:
            self._conn.sendall(data)
        except OSError as exc:
            logger.warning("连接 {} 发送失败: {}", self._peer, exc)


__all__ = ["ConnectionHandler", "dispatch", "validate_inbound", "parse_version"]
