"""模拟服务端桩（T2.3 模块隔离测试用，🔴 非生产代码）。

基于契约库 frames 的本机 Unix Socket 桩，支持注入：
health 状态、协议版本（含不兼容版本）、recognize 成功/错误码、连接即断行为。
"""

from __future__ import annotations

import socket
import threading
from pathlib import Path

from zen_vocotype_protocol.frames import MessageBuffer, encode_frame
from zen_vocotype_protocol.version import PROTOCOL_VERSION


class StubServer:
    """可配置行为的模拟服务端（线程内 accept 循环，支持长连接多请求）。"""

    def __init__(
        self,
        socket_path: Path,
        *,
        health_status: str = "ready",
        protocol_version: str = PROTOCOL_VERSION,
        recognize_payload: dict | None = None,
        recognize_error: tuple[int, str] | None = None,
        close_first_n_connections: int = 0,
    ) -> None:
        self.socket_path = str(socket_path)
        self.health_status = health_status
        self.protocol_version = protocol_version
        self.recognize_payload = recognize_payload or {
            "text": "桩识别文本",
            "confidence": 0.99,
            "duration": 1.0,
        }
        self.recognize_error = recognize_error
        self.close_first_n_connections = close_first_n_connections
        # 观测点（断言用）
        self.connection_count = 0
        self.request_log: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._srv: socket.socket | None = None

    def start(self) -> None:
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.socket_path)
        self._srv.listen(4)
        self._srv.settimeout(0.2)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._srv is not None:
            self._srv.close()
        Path(self.socket_path).unlink(missing_ok=True)

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connection_count += 1
            if self.connection_count <= self.close_first_n_connections:
                conn.close()  # 模拟对端立即断开
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        buf = MessageBuffer()
        try:
            while not self._stop.is_set():
                try:
                    data = conn.recv(64 * 1024)
                except socket.timeout:
                    continue
                if not data:
                    return
                buf.feed(data)
                while True:
                    frame = buf.next_frame()
                    if frame is None:
                        break
                    header, body = frame
                    conn.sendall(encode_frame(self._respond(header, body)))
        except OSError:
            return
        finally:
            conn.close()

    def _respond(self, header: dict, body: bytes) -> dict:
        action = header.get("action")
        self.request_log.append(str(action))
        base = {
            "action": action,
            "protocol_version": self.protocol_version,
            "request_id": header.get("request_id"),
        }
        if action == "health":
            return base | {
                "ok": True,
                "payload": {
                    "status": self.health_status,
                    "model_loaded": self.health_status == "ready",
                    "current_model": "stub-model",
                },
            }
        if action == "recognize":
            if self.recognize_error is not None:
                code, message = self.recognize_error
                return base | {"ok": False, "error": {"code": code, "message": message}}
            return base | {"ok": True, "payload": dict(self.recognize_payload)}
        return base | {"ok": False, "error": {"code": 1002, "message": "未知 action"}}
