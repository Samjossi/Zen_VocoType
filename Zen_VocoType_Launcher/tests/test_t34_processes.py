"""T3.4 子进程管理测试：拉起/轮询/两段式回收/孙进程整组回收/PDEATHSIG。"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zen_vocotype_launcher.processes import ManagedProcess, read_log_tail, spawn

#: 测试等待条件满足的轮询间隔（秒）
_POLL = 0.05


def _wait_until(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL)
    return False


def _sleep_child() -> list[str]:
    return [sys.executable, "-c", "import time; time.sleep(60)"]


class TestSpawnAndPoll:
    def test_spawn_returns_managed_process(self, tmp_path):
        proc = spawn(_sleep_child(), name="svc", log_path=tmp_path / "c.log")
        try:
            assert proc.pid > 0
            assert proc.is_alive()
            assert proc.poll() is None  # 仍在运行
        finally:
            proc.terminate_group(1.0)
        assert not proc.is_alive()
        assert proc.poll() is not None  # 已死（含退出码）

    def test_output_redirected_to_log(self, tmp_path):
        log = tmp_path / "out.log"
        proc = spawn(
            [
                sys.executable,
                "-c",
                "print('hello-child', flush=True); import time; time.sleep(60)",
            ],
            name="svc",
            log_path=log,
        )
        try:
            assert _wait_until(lambda: "hello-child" in log.read_text(), 5.0)
        finally:
            proc.terminate_group(2.0)
        assert "hello-child" in log.read_text()

    def test_spawn_failure_raises_and_closes(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            spawn(["/nonexistent/binary-xyz"], name="svc", log_path=tmp_path / "c.log")


class TestTerminateGroup:
    def test_graceful_sigterm(self, tmp_path):
        proc = spawn(_sleep_child(), name="svc", log_path=tmp_path / "c.log")
        start = time.monotonic()
        proc.terminate_group(grace_seconds=5.0)
        assert time.monotonic() - start < 5.0  # SIGTERM 即死，未走 SIGKILL
        assert not proc.is_alive()

    def test_sigkill_fallback(self, tmp_path):
        # 忽略 SIGTERM 的子进程 → 必须走 SIGKILL 兜底
        proc = spawn(
            [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "time.sleep(60)",
            ],
            name="svc",
            log_path=tmp_path / "c.log",
        )
        proc.terminate_group(grace_seconds=0.5)
        assert not proc.is_alive()

    def test_ignoring_child_killed_via_kill(self, tmp_path):
        proc = spawn(
            [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                "time.sleep(60)",
            ],
            name="svc",
            log_path=tmp_path / "c.log",
        )
        pid = proc.pid
        proc.terminate_group(grace_seconds=0.5)
        # 进程组已整体消亡
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    def test_grandchild_reaped_by_killpg(self, tmp_path):
        """孙进程不逃逸：子进程 fork 孙子后，killpg 整组回收。"""
        marker = tmp_path / "grandchild.pid"
        code = (
            "import os,time;"
            "pid=os.fork();"
            f"os.write(1, b'');"
            f"open({str(marker)!r},'w').write(str(pid)) if pid else None;"
            "time.sleep(60)"
        )
        proc = spawn(
            [sys.executable, "-c", code], name="svc", log_path=tmp_path / "c.log"
        )
        try:
            assert _wait_until(marker.exists, 5.0), "孙子进程未出现"
            grandchild_pid = int(marker.read_text())
            os.kill(grandchild_pid, 0)  # 孙子存活
        finally:
            proc.terminate_group(grace_seconds=2.0)
        # 父与子均回收后，孙子所在进程组已收到信号
        assert _wait_until(
            lambda: _pid_dead(grandchild_pid), 3.0
        ), f"孙进程 {grandchild_pid} 逃逸"

    def test_terminate_dead_process_is_noop(self, tmp_path):
        proc = spawn(
            [sys.executable, "-c", "pass"], name="svc", log_path=tmp_path / "c.log"
        )
        assert _wait_until(lambda: not proc.is_alive(), 5.0)
        proc.terminate_group(1.0)  # 不抛异常


def _pid_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    # 僵尸进程视为已死
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] == "Z"
    except OSError:
        return True


class TestParentExitSemantics:
    def test_child_survives_parent_normal_exit(self, tmp_path):
        """选型七方案 A 语义固化：Launcher（替身）正常退出后子进程继续存活
        （PDEATHSIG 已移除——2026-07-22 dev 实测其与「拉起即退出」根本冲突）。"""
        child_pid_file = tmp_path / "child.pid"
        wrapper_code = (
            "import sys;"
            "sys.path.insert(0, 'Zen_VocoType_Launcher/src');"
            "from zen_vocotype_launcher.processes import spawn;"
            f"p = spawn([sys.executable, '-c', 'import time; time.sleep(60)'],"
            f" name='svc', log_path={str(tmp_path / 'c.log')!r});"
            f"open({str(child_pid_file)!r}, 'w').write(str(p.pid))"
            # 替身父进程到此正常退出
        )
        wrapper = subprocess.Popen([sys.executable, "-c", wrapper_code])
        wrapper.wait()
        assert wrapper.returncode == 0
        child_pid = int(child_pid_file.read_text())
        try:
            os.kill(child_pid, 0)  # 父已退，子进程必须存活
            assert not _pid_dead(child_pid)
        finally:
            os.kill(child_pid, signal.SIGKILL)


class TestReadLogTail:
    def test_tail_lines(self, tmp_path):
        log = tmp_path / "c.log"
        log.write_text("\n".join(f"line{i}" for i in range(30)))
        tail = read_log_tail(log, max_lines=5)
        assert tail.splitlines() == [f"line{i}" for i in range(25, 30)]

    def test_missing_file(self, tmp_path):
        assert read_log_tail(tmp_path / "none.log") == ""
