"""T3.5 既有实例识别测试：PID 三分支、Socket 探测两分支、陈旧清理。"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zen_vocotype_launcher.discovery import (
    ComponentStatus,
    SocketProbeResult,
    discover_component,
    is_pid_running_match,
    probe_socket,
    read_pid_file,
)

#: 测试等待轮询间隔（秒）
_POLL = 0.05

#: 本测试进程的可执行路径（合法 exe 匹配样本）
SELF_EXE = os.readlink(f"/proc/{os.getpid()}/exe")


class TestReadPidFile:
    def test_plain_pid(self, tmp_path):
        f = tmp_path / "x.lock"
        f.write_text("12345")
        assert read_pid_file(str(f)) == 12345

    def test_json_meta_pid(self, tmp_path):
        f = tmp_path / "x.lock"
        f.write_text('{"pid": 23456, "mode": "prod"}')
        assert read_pid_file(str(f)) == 23456

    def test_missing(self, tmp_path):
        assert read_pid_file(str(tmp_path / "none")) is None

    def test_garbage(self, tmp_path):
        f = tmp_path / "x.lock"
        f.write_text("not-a-pid")
        assert read_pid_file(str(f)) is None

    def test_empty(self, tmp_path):
        f = tmp_path / "x.lock"
        f.write_text("")
        assert read_pid_file(str(f)) is None


class TestPidRunningMatch:
    def test_self_matches_exe(self):
        assert is_pid_running_match(os.getpid(), expected_exe=SELF_EXE)

    def test_dead_pid(self):
        # 拉起即死的进程，其 PID 在回收后不存在
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        assert not is_pid_running_match(proc.pid)

    def test_exe_mismatch(self):
        assert not is_pid_running_match(os.getpid(), expected_exe="/bin/false")

    def test_cmdline_fragment(self):
        assert is_pid_running_match(
            os.getpid(),
            expected_exe=SELF_EXE,
            expected_cmdline_fragment="pytest",
        )
        assert not is_pid_running_match(
            os.getpid(),
            expected_exe=SELF_EXE,
            expected_cmdline_fragment="/nonexistent/main.py",
        )


class TestDiscoverComponent:
    def test_absent(self, tmp_path):
        result = discover_component(str(tmp_path / "none.lock"), name="service")
        assert result.status is ComponentStatus.ABSENT

    def test_running_self(self, tmp_path):
        f = tmp_path / "x.lock"
        f.write_text(str(os.getpid()))
        result = discover_component(
            str(f), name="service", expected_exe=SELF_EXE
        )
        assert result.status is ComponentStatus.RUNNING
        assert result.pid == os.getpid()
        assert f.exists()  # 合法实例不清理锁文件

    def test_stale_dead_pid_cleaned(self, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        f = tmp_path / "x.lock"
        f.write_text(str(proc.pid))
        result = discover_component(str(f), name="service")
        assert result.status is ComponentStatus.STALE
        assert not f.exists()  # 陈旧锁文件已清理

    def test_stale_exe_mismatch_cleaned(self, tmp_path):
        """PID 复用（存活但 exe 不匹配）→ 陈旧并清理。"""
        f = tmp_path / "x.lock"
        f.write_text(str(os.getpid()))
        result = discover_component(
            str(f), name="service", expected_exe="/bin/false"
        )
        assert result.status is ComponentStatus.STALE
        assert "复用" in result.detail
        assert not f.exists()


class _ForeignServer:
    """非本组件协议的 Socket 占用者（接受连接后返回垃圾字节）。"""

    def __init__(self, path: Path):
        import threading

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                conn.sendall(b"garbage-not-a-frame")
                conn.close()
            except OSError:
                pass

    def stop(self):
        self._sock.close()


class TestProbeSocket:
    def test_free_when_missing(self, tmp_path):
        result, _ = probe_socket(str(tmp_path / "none.sock"))
        assert result is SocketProbeResult.FREE

    def test_free_when_stale_file(self, tmp_path):
        # 路径存在但无人监听（残留文件）
        f = tmp_path / "stale.sock"
        f.touch()
        result, detail = probe_socket(str(f))
        assert result is SocketProbeResult.FREE
        assert "残留" in detail

    def test_foreign_occupant(self, tmp_path):
        server = _ForeignServer(tmp_path / "f.sock")
        try:
            result, _ = probe_socket(str(tmp_path / "f.sock"))
            assert result is SocketProbeResult.FOREIGN
        finally:
            server.stop()

    def test_ours_via_stub(self, tmp_path):
        # 复用 T3.6 的模拟服务端桩验证 OURS 分支（Launcher 自有桩类）
        from test_t36_readiness import StubServer

        server = StubServer(tmp_path / "o.sock")
        server.start()
        try:
            result, detail = probe_socket(str(tmp_path / "o.sock"))
            assert result is SocketProbeResult.OURS
            assert "本组件协议" in detail
        finally:
            server.stop()
