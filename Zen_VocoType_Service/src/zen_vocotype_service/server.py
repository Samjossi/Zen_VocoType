"""Unix Socket 服务（选型一：主线程 accept 循环 + 每连接守护线程）。

Socket 本地访问控制（协议 §7.1 v1 强制项，🔴 缺失即实现缺陷）：

1. bind 前校验目标路径非符号链接、属主为自身（防预创建/链接攻击）
2. bind 后显式 ``chmod 0600``（不依赖进程 umask）
3. accept 时 ``SO_PEERCRED`` 校验对端 UID，非本 UID 拒绝并返回 1006
"""

import os
import socket
import stat
import struct
import threading
from pathlib import Path

from zen_vocotype_protocol import errors
from zen_vocotype_protocol.frames import encode_frame
from zen_vocotype_protocol.paths import DEFAULT_RUNTIME_DIR
from zen_vocotype_protocol.version import PROTOCOL_VERSION

from zen_vocotype_service.config import Settings
from zen_vocotype_service.connection import ConnectionHandler
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.logging_setup import logger

#: listen 积压队列
LISTEN_BACKLOG: int = 8

#: accept 超时（秒）：周期性检查停服标志
ACCEPT_TIMEOUT_S: float = 0.5


class SocketPathError(Exception):
    """Socket 路径安全检查失败（协议 §7.1，🔴 禁止降级绕过）。"""


class SocketServer:
    """Socket 监听与连接生命周期管理。"""

    def __init__(self, settings: Settings, ctx: ServiceContext) -> None:
        self._settings = settings
        self._ctx = ctx
        self._socket_path = Path(settings.socket_path)
        self._listen_sock: socket.socket | None = None
        self._stop_event = threading.Event()
        self._connections: set[ConnectionHandler] = set()
        self._conn_lock = threading.Lock()
        self._accept_thread: threading.Thread | None = None

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    # ------------------------------------------------------------------
    # 协议 §7.1 强制项：bind 前路径安全校验
    # ------------------------------------------------------------------

    def _prepare_socket_path(self) -> None:
        parent = self._socket_path.parent
        if not parent.exists():
            # 运行目录缺失时以 0700 创建（~/.local/run 回退场景）
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent == DEFAULT_RUNTIME_DIR:
            mode = stat.S_IMODE(parent.stat().st_mode)
            if mode & 0o077:
                logger.warning("运行目录 {} 权限 {:o} 非 0700，修正", parent, mode)
                os.chmod(parent, 0o700)
        if self._socket_path.is_symlink():
            raise SocketPathError(
                f"Socket 路径是符号链接，拒绝 bind（防链接攻击）: {self._socket_path}"
            )
        if self._socket_path.exists():
            st = self._socket_path.stat()
            if st.st_uid != os.getuid():
                raise SocketPathError(
                    f"Socket 路径已存在且属主非自身（uid={st.st_uid}），拒绝 bind: "
                    f"{self._socket_path}"
                )
            # 自身遗留的陈旧 Socket 文件（上次异常退出）：删除后重新 bind
            logger.warning("删除陈旧 Socket 文件: {}", self._socket_path)
            self._socket_path.unlink()

    # ------------------------------------------------------------------
    # 协议 §7.1 强制项：SO_PEERCRED 对端身份校验
    # ------------------------------------------------------------------

    @staticmethod
    def _peer_uid(conn: socket.socket) -> int:
        """返回对端进程 UID（Linux SO_PEERCRED）。"""
        cred = conn.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        _pid, uid, _gid = struct.unpack("3i", cred)
        return uid

    def _reject_unauthorized(self, conn: socket.socket, peer_uid: int) -> None:
        logger.warning(
            "拒绝非本 UID 对端连接: uid={}（本进程 uid={}）", peer_uid, os.getuid()
        )
        try:
            conn.sendall(
                encode_frame(
                    {
                        "action": None,
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": None,
                        "ok": False,
                        "error": {
                            "code": errors.ERR_UNAUTHORIZED_PEER,
                            "message": f"对端身份校验失败: uid={peer_uid} 不在白名单",
                        },
                    }
                )
            )
        except OSError:
            pass
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def bind(self) -> None:
        """bind + chmod 0600 + listen；🔴 不等待任何模型操作（先监听后加载）。"""
        self._prepare_socket_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(self._socket_path))
            # bind 后显式 0600，不依赖 umask（协议 §7.1-3）
            os.chmod(self._socket_path, 0o600)
            sock.listen(LISTEN_BACKLOG)
            sock.settimeout(ACCEPT_TIMEOUT_S)
        except OSError:
            sock.close()
            raise
        self._listen_sock = sock
        logger.info("Socket 已监听: {}（权限 0600）", self._socket_path)

    def start(self, *, background: bool = False) -> None:
        """启动 accept 循环；``background=True`` 时运行于守护线程（测试用）。"""
        if self._listen_sock is None:
            self.bind()
        if background:
            self._accept_thread = threading.Thread(
                target=self._accept_loop, name="accept", daemon=True
            )
            self._accept_thread.start()
        else:
            self._accept_loop()

    def _accept_loop(self) -> None:
        assert self._listen_sock is not None
        while not self._stop_event.is_set():
            try:
                conn, _ = self._listen_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break  # 监听 socket 已关闭（停服）
            if self._peer_uid(conn) != os.getuid():
                self._reject_unauthorized(conn, self._peer_uid(conn))
                continue
            with self._conn_lock:
                if len(self._connections) >= self._settings.max_connections:
                    logger.warning(
                        "连接数达上限 {}，拒绝新连接", self._settings.max_connections
                    )
                    self._reject_busy(conn)
                    continue
                handler = ConnectionHandler(
                    conn,
                    peer=str(len(self._connections) + 1),
                    ctx=self._ctx,
                    stop_event=self._stop_event,
                    on_close=self._on_connection_close,
                )
                self._connections.add(handler)
            handler.start()

    def _reject_busy(self, conn: socket.socket) -> None:
        try:
            conn.sendall(
                encode_frame(
                    {
                        "action": None,
                        "protocol_version": PROTOCOL_VERSION,
                        "request_id": None,
                        "ok": False,
                        "error": {
                            "code": errors.ERR_BUSY,
                            "message": "连接数已达上限，拒绝新连接",
                        },
                    }
                )
            )
        except OSError:
            pass
        finally:
            conn.close()

    def _on_connection_close(self, handler: ConnectionHandler) -> None:
        with self._conn_lock:
            self._connections.discard(handler)

    def shutdown(self) -> None:
        """确定性退出：停止 accept → 关闭全部连接 → 删除 Socket 文件。"""
        self._stop_event.set()
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
        with self._conn_lock:
            connections = list(self._connections)
        for handler in connections:
            handler.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
        try:
            if self._socket_path.exists() and not self._socket_path.is_symlink():
                self._socket_path.unlink()
                logger.info("Socket 文件已删除: {}", self._socket_path)
        except OSError as exc:
            logger.warning("删除 Socket 文件失败: {}", exc)
