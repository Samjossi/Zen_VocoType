"""Socket 协议客户端（同步阻塞实现，仅由网络 worker 线程调用）。

职责：Unix Socket 连接、复合帧收发、协议版本握手校验、错误码透传。

- 帧编解码/action/错误码/版本策略全部走契约库（🔴 禁止本组件重复定义）
- 收到 ``ok: false`` 响应：1003 → :class:`VersionMismatchError`；
  其余错误码原样透传为 :class:`RequestFailedError`（🔴 禁止改写语义）
- 连接层失败（拒绝/断开/对端关闭）→ :class:`ServiceUnavailableError`
"""

from __future__ import annotations

import itertools
import socket

from loguru import logger

from zen_vocotype_protocol import actions, errors
from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.paths import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_WIDTH,
)
from zen_vocotype_protocol.version import PROTOCOL_VERSION, is_compatible

#: health/ready 等轻量请求的 I/O 超时（秒）。依据：本地 Socket 往返为毫秒级，
#: 5s 已覆盖极端调度抖动；超时即视为服务端异常
SHORT_IO_TIMEOUT_S: float = 5.0

#: recognize 请求的 I/O 超时（秒）。依据：服务端推理超时预算 60s（阶段 1 配置项），
#: 客户端留 15s 网络与排队余量
RECOGNIZE_IO_TIMEOUT_S: float = 75.0

#: 单次 recv 块大小
_RECV_CHUNK: int = 64 * 1024

_request_ids = itertools.count(1)


class ServiceUnavailableError(Exception):
    """连接层失败：无法连接 / 连接中断 / 对端关闭。"""


class VersionMismatchError(Exception):
    """协议版本不兼容（1003 或响应版本校验失败；🔴 禁止静默继续）。"""


class RequestFailedError(Exception):
    """服务端返回 ``ok: false``：错误码与 message 原样透传。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code: int = code
        self.message: str = message


class ProtocolClient:
    """单条长连接的同步协议客户端（非线程安全，归属 worker 线程）。"""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._buffer: MessageBuffer | None = None

    # ------------------------------------------------------------------ 连接

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        """建立连接（重复调用前先关闭旧连接）。

        :raises ServiceUnavailableError: 连接失败（含服务端未运行）
        """
        self.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self._socket_path)
        except OSError as exc:
            sock.close()
            raise ServiceUnavailableError(
                f"无法连接服务端 Socket {self._socket_path}: {exc}"
            ) from exc
        self._sock = sock
        self._buffer = MessageBuffer()  # 每连接独立缓冲（协议 §7-3）
        logger.debug("已连接服务端 {}", self._socket_path)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buffer = None

    # ------------------------------------------------------------------ 请求

    def request(
        self,
        action: str,
        *,
        extra_header: dict | None = None,
        body: bytes = b"",
        timeout: float = SHORT_IO_TIMEOUT_S,
    ) -> dict:
        """发送一个请求帧并等待响应帧。

        :raises ServiceUnavailableError: 未连接或连接中断
        :raises VersionMismatchError: 版本不兼容（1003 或响应头版本校验失败）
        :raises RequestFailedError: 服务端业务错误（错误码透传）
        """
        if self._sock is None or self._buffer is None:
            raise ServiceUnavailableError("未连接服务端")

        header = {
            "action": action,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": next(_request_ids),
        }
        if extra_header:
            header.update(extra_header)
        if body:
            header["audio_bytes"] = len(body)

        try:
            self._sock.settimeout(timeout)
            self._sock.sendall(encode_frame(header, body))
            response = self._recv_response()
        except (ServiceUnavailableError, VersionMismatchError, RequestFailedError):
            raise
        except (OSError, socket.timeout) as exc:
            self.close()
            raise ServiceUnavailableError(f"连接中断: {exc}") from exc

        # 版本握手校验（协议 §5）：响应头版本不兼容 → 明确报错，禁止静默继续
        remote_version = response.get("protocol_version")
        if not isinstance(remote_version, str) or not is_compatible(
            PROTOCOL_VERSION, remote_version
        ):
            self.close()
            raise VersionMismatchError(
                f"协议版本不兼容: 客户端 {PROTOCOL_VERSION} / 服务端 {remote_version!r}"
            )

        if not response.get("ok"):
            error = response.get("error") or {}
            code = error.get("code", -1)
            message = error.get("message", "")
            if code == errors.ERR_PROTOCOL_VERSION_MISMATCH:
                self.close()
                raise VersionMismatchError(message)
            raise RequestFailedError(code, message)
        return response.get("payload") or {}

    def _recv_response(self) -> dict:
        """读取一个完整响应帧。"""
        assert self._sock is not None and self._buffer is not None
        while True:
            frame = self._buffer.next_frame()
            if frame is not None:
                header, _body = frame
                return header
            data = self._sock.recv(_RECV_CHUNK)
            if not data:
                self.close()
                raise ServiceUnavailableError("对端关闭连接")
            self._buffer.feed(data)

    # ------------------------------------------------------------------ 便捷

    def health(self) -> dict:
        """``health`` 探测（首请求即版本握手，协议 §5）。"""
        return self.request(actions.ACTION_HEALTH)

    def recognize(self, pcm: bytes) -> dict:
        """``recognize`` 识别请求（16kHz/16bit/单声道 PCM，契约库冻结参数）。"""
        return self.request(
            actions.ACTION_RECOGNIZE,
            extra_header={
                "audio_format": {
                    "sample_rate": DEFAULT_SAMPLE_RATE,
                    "channels": DEFAULT_CHANNELS,
                    "sample_width": DEFAULT_SAMPLE_WIDTH,
                }
            },
            body=pcm,
            timeout=RECOGNIZE_IO_TIMEOUT_S,
        )
