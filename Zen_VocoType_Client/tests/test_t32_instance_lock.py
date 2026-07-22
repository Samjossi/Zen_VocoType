"""T3.2 客户端实例锁测试：抢锁写 PID、二实例拒绝、死亡释放、dev/正式隔离。"""

import os
import subprocess
import sys

import pytest
from zen_vocotype_protocol.paths import (
    CLIENT_LOCK_PATH,
    DEV_CLIENT_LOCK_PATH,
    DEV_SOCKET_PATH,
)

from zen_vocotype_client.instance_lock import (
    InstanceLock,
    InstanceLockError,
    lock_path_for,
)


class TestLockPathSelection:
    def test_prod_socket_uses_prod_lock(self):
        assert lock_path_for("/some/prod.sock") == CLIENT_LOCK_PATH

    def test_dev_socket_uses_dev_lock(self):
        assert lock_path_for(DEV_SOCKET_PATH) == DEV_CLIENT_LOCK_PATH

    def test_prod_and_dev_paths_differ(self):
        assert CLIENT_LOCK_PATH != DEV_CLIENT_LOCK_PATH


class TestInstanceLock:
    def test_acquire_writes_pid(self, tmp_path):
        lock = InstanceLock(str(tmp_path / "c.lock"))
        lock.acquire()
        try:
            assert (tmp_path / "c.lock").read_text() == str(os.getpid())
        finally:
            lock.release()

    def test_second_instance_rejected(self, tmp_path):
        path = str(tmp_path / "c.lock")
        first = InstanceLock(path)
        first.acquire()
        try:
            second = InstanceLock(path)
            with pytest.raises(InstanceLockError):
                second.acquire()
        finally:
            first.release()

    def test_release_frees_lock_for_next(self, tmp_path):
        """acquire → release → 再次 acquire 成功（锁可复用）。"""
        path = str(tmp_path / "c.lock")
        a = InstanceLock(path)
        a.acquire()
        a.release()
        b = InstanceLock(path)
        b.acquire()
        b.release()

    def test_death_releases_lock(self, tmp_path):
        """持锁进程被杀（含 SIGKILL 语义）后内核自动释放，无 stale 锁。"""
        import time

        path = tmp_path / "c.lock"
        code = (
            "import sys; sys.path.insert(0, 'Zen_VocoType_Client/src');"
            "from zen_vocotype_client.instance_lock import InstanceLock;"
            f"InstanceLock({str(path)!r}).acquire();"
            "import time; time.sleep(30)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(100):  # 等待子进程抢锁（条件等待，非固定 sleep）
                if path.exists() and path.read_text().strip():
                    break
                if proc.poll() is not None:
                    raise AssertionError("子进程未能抢锁即退出")
                time.sleep(0.05)
            assert path.read_text().strip() == str(proc.pid)
        finally:
            proc.kill()
            proc.wait()
        # 子进程已死：新实例应能抢锁成功
        lock = InstanceLock(str(path))
        lock.acquire()
        lock.release()

    def test_dev_and_prod_locks_independent(self, tmp_path, monkeypatch):
        prod = InstanceLock(str(tmp_path / "prod.lock"))
        dev = InstanceLock(str(tmp_path / "dev.lock"))
        prod.acquire()
        dev.acquire()  # 双锁互不阻塞（dev/正式并行语义）
        prod.release()
        dev.release()
