"""协议级就绪等待（选型二：两阶段条件等待，🔴 全模块零固定 sleep）。

- 极简同步协议客户端：基于契约库 ``frames.py`` 编解码，仅 ``health``/``ready``
  两 action + 协议版本握手（版本不一致明确报错，🔴 禁止静默继续）；
  Launcher 自有实现，零跨组件 import（大纲原则 7）
- 两阶段等待：阶段一 ``connectable``（建连成功即过），并行检查子进程
  ``poll()``——已退出立即失败（🔴 禁止干等超时）；阶段二 ``model_ready``
  （``ready`` 轮询直到模型就绪）
"""

from __future__ import annotations

import itertools
import socket
import time
from collections.abc import Callable

from loguru import logger

from zen_vocotype_protocol import actions, errors
from zen_vocotype_protocol.frames import FrameError, MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION, is_compatible

#: health/ready 轻量请求的 I/O 超时（秒）。依据：本地 Socket 往返为毫秒级，
#: 5s 已覆盖极端调度抖动
SHORT_IO_TIMEOUT_S: float = 5.0

#: 单次 recv 块大小
_RECV_CHUNK: int = 64 * 1024

_request_ids = itertools.count(1)


class ServiceUnavailableError(Exception):
    """连接层失败：无法连接 / 连接中断 / 对端关闭。"""


class VersionMismatchError(Exception):
    """协议版本不兼容（1003 或响应头版本校验失败；🔴 禁止静默继续）。"""


class RequestFailedError(Exception):
    """服务端返回 ``ok: false``：错误码与 message 原样透传。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code: int = code
        self.message: str = message


class ReadyTimeoutError(Exception):
    """就绪等待超时（两阶段各自抛出，消息含阶段名）。"""


class ProtocolClient:
    """单条短连的极简同步协议客户端（仅 health/ready）。"""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._sock: socket.socket | None = None
        self._buffer: MessageBuffer | None = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        """建立连接。

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
        self._buffer = MessageBuffer()

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buffer = None

    def request(self, action: str, timeout: float = SHORT_IO_TIMEOUT_S) -> dict:
        """发送请求帧并等待响应帧，返回响应头。

        :raises ServiceUnavailableError: 未连接或连接中断
        :raises VersionMismatchError: 版本不兼容（1003 或响应头版本校验失败）
        :raises RequestFailedError: 服务端业务错误（错误码透传）
        """
        if self._sock is None or self._buffer is None:
            raise ServiceUnavailableError("未连接服务端")

        header = {
            "action": action,
            "protocol_version": PROTOCOL_VERSION,
            "request_id": str(next(_request_ids)),
        }
        try:
            self._sock.sendall(encode_frame(header))
            self._sock.settimeout(timeout)
            while True:
                frame = self._buffer.next_frame()
                if frame is not None:
                    resp_header, _body = frame
                    break
                chunk = self._sock.recv(_RECV_CHUNK)
                if not chunk:
                    raise ServiceUnavailableError("服务端关闭了连接")
                self._buffer.feed(chunk)
        except socket.timeout as exc:
            raise ServiceUnavailableError(f"等待服务端响应超时（{timeout}s）") from exc
        except FrameError as exc:
            raise ServiceUnavailableError(f"对端非本协议（帧损坏）: {exc}") from exc
        except OSError as exc:
            raise ServiceUnavailableError(f"连接中断: {exc}") from exc

        if resp_header.get("action") != action:
            raise ServiceUnavailableError(
                f"响应 action 不匹配：期望 {action}，收到 {resp_header.get('action')}"
            )
        remote_version = resp_header.get("protocol_version", "")
        if not is_compatible(PROTOCOL_VERSION, remote_version):
            raise VersionMismatchError(
                f"协议版本不兼容：本地 {PROTOCOL_VERSION}，服务端 {remote_version}"
            )
        if not resp_header.get("ok", False):
            err = resp_header.get("error") or {}
            code = err.get("code", -1)
            message = err.get("message", "未知错误")
            if code == errors.ERR_PROTOCOL_VERSION_MISMATCH:
                raise VersionMismatchError(f"[{code}] {message}")
            raise RequestFailedError(code, message)
        return resp_header

    def health(self) -> dict:
        """``health`` 探测（首请求即版本握手，协议 §5），返回 payload。"""
        return self.request(actions.ACTION_HEALTH).get("payload") or {}

    def ready(self) -> dict:
        """``ready`` 就绪确认，返回 payload（``{"ready": bool, ...}``）。"""
        return self.request(actions.ACTION_READY).get("payload") or {}


# ---------------------------------------------------------------------- 等待


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float,
    timeout_message: str,
) -> None:
    """条件等待：条件满足即返回，超时抛 :class:`ReadyTimeoutError`。

    🔴 禁止以本函数外的固定 sleep 充当同步手段（C1）。

    :param predicate: 条件函数（每次调用一次探测）
    :param timeout_s: 等待预算（秒）
    :param interval_s: 轮询间隔（秒）
    :param timeout_message: 超时异常消息（含阶段名）
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise ReadyTimeoutError(timeout_message)
        time.sleep(min(interval_s, max(deadline - time.monotonic(), 0.0)))


def wait_for_readiness(
    client: ProtocolClient,
    *,
    socket_wait_timeout_s: float,
    model_ready_timeout_s: float,
    poll_interval_s: float,
    process_alive: Callable[[], bool] | None = None,
    process_exit_info: Callable[[], str] | None = None,
    t0: float | None = None,
) -> None:
    """两阶段就绪等待：Socket 可连 → ``ready`` 模型就绪。

    :param client: 协议客户端（本函数持有并关闭连接）
    :param process_alive: 子进程存活检查（None 跳过并行死亡检测）
    :param process_exit_info: 子进程退出详情（退出码 + 日志尾部，失败诊断用）
    :param t0: 拉起时刻（``time.monotonic()``）；提供时输出结构化耗时字段
        ``启动耗时 T1_socket_connect_s= / T2_model_ready_s=``（阶段 4 选型七
        方案 A 埋点，冷启动测量口径即用户真实感知）
    :raises ServiceUnavailableError: 等待期间子进程已退出（🔴 禁止干等超时）
    :raises ReadyTimeoutError: 任一阶段超时
    :raises VersionMismatchError / RequestFailedError: 协议层失败
    """

    def _check_process() -> None:
        if process_alive is not None and not process_alive():
            detail = process_exit_info() if process_exit_info else ""
            raise ServiceUnavailableError(f"服务端进程在等待期间已退出。{detail}")

    # 阶段一：Socket 可连接（快路径，阶段 1 验收 ≤5s）
    def _connectable() -> bool:
        _check_process()
        try:
            client.connect()
        except ServiceUnavailableError:
            return False
        return True

    wait_until(
        _connectable,
        timeout_s=socket_wait_timeout_s,
        interval_s=poll_interval_s,
        timeout_message=f"阶段一超时：{socket_wait_timeout_s}s 内 Socket 不可连接",
    )
    logger.debug("阶段一通过：Socket 可连接")
    if t0 is not None:
        logger.info("启动耗时 T1_socket_connect_s={:.3f}", time.monotonic() - t0)

    try:
        # 版本握手（health 首请求，协议 §5）
        _check_process()
        payload = client.health()
        logger.debug(
            "health：status={} model_loaded={} service_version={}",
            payload.get("status"),
            payload.get("model_loaded"),
            payload.get("service_version"),
        )

        # 阶段二：模型就绪
        def _model_ready() -> bool:
            _check_process()
            payload = client.ready()
            ready = bool(payload.get("ready", False))
            if not ready:
                logger.debug(
                    "模型未就绪，继续等待（current_model={}）",
                    payload.get("current_model"),
                )
            return ready

        wait_until(
            _model_ready,
            timeout_s=model_ready_timeout_s,
            interval_s=poll_interval_s,
            timeout_message=f"阶段二超时：{model_ready_timeout_s}s 内模型未就绪",
        )
        logger.info("阶段二通过：模型已就绪（current_model={}）", client.ready().get("current_model"))
        if t0 is not None:
            logger.info("启动耗时 T2_model_ready_s={:.3f}", time.monotonic() - t0)
    finally:
        client.close()
