"""T3.3 Launcher 单实例锁测试。"""

import json
import os
import subprocess
import sys
import time

import pytest
from zen_vocotype_protocol.paths import DEV_LAUNCHER_LOCK_PATH, LAUNCHER_LOCK_PATH

from zen_vocotype_launcher.locks import (
    LauncherLock,
    LauncherLockError,
    lock_path_for,
    read_lock_meta,
)


class TestLockPathFor:
    def test_prod_and_dev_paths_differ(self):
        assert lock_path_for(False) == LAUNCHER_LOCK_PATH
        assert lock_path_for(True) == DEV_LAUNCHER_LOCK_PATH
        assert LAUNCHER_LOCK_PATH != DEV_LAUNCHER_LOCK_PATH


class TestLauncherLock:
    def test_acquire_writes_meta(self, tmp_path):
        lock = LauncherLock(str(tmp_path / "l.lock"), "prod")
        lock.acquire()
        try:
            meta = json.loads((tmp_path / "l.lock").read_text())
            assert meta["pid"] == os.getpid()
            assert meta["mode"] == "prod"
            assert meta["started_at"] > 0
        finally:
            lock.release()

    def test_mutual_exclusion(self, tmp_path):
        path = str(tmp_path / "l.lock")
        first = LauncherLock(path, "prod")
        first.acquire()
        try:
            with pytest.raises(LauncherLockError):
                LauncherLock(path, "prod").acquire()
        finally:
            first.release()

    def test_death_releases_lock(self, tmp_path):
        path = tmp_path / "l.lock"
        code = (
            "import sys; sys.path.insert(0, 'Zen_VocoType_Launcher/src');"
            "from zen_vocotype_launcher.locks import LauncherLock;"
            f"LauncherLock({str(path)!r}, 'prod').acquire();"
            "import time; time.sleep(30)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            for _ in range(100):  # 条件等待子进程抢锁
                if path.exists() and path.read_text().strip():
                    break
                if proc.poll() is not None:
                    raise AssertionError("子进程未能抢锁即退出")
                time.sleep(0.05)
        finally:
            proc.kill()
            proc.wait()
        lock = LauncherLock(str(path), "prod")
        lock.acquire()  # 无 stale 锁
        lock.release()

    def test_dual_mode_not_blocking(self, tmp_path):
        prod = LauncherLock(str(tmp_path / "prod.lock"), "prod")
        dev = LauncherLock(str(tmp_path / "dev.lock"), "dev")
        prod.acquire()
        dev.acquire()  # 双模式互不阻塞
        prod.release()
        dev.release()


class TestReadLockMeta:
    def test_reads_meta(self, tmp_path):
        path = tmp_path / "l.lock"
        lock = LauncherLock(str(path), "dev")
        lock.acquire()
        try:
            meta = read_lock_meta(str(path))
            assert meta is not None
            assert meta["mode"] == "dev"
        finally:
            lock.release()

    def test_missing_file_returns_none(self, tmp_path):
        assert read_lock_meta(str(tmp_path / "none.lock")) is None

    def test_corrupt_returns_none(self, tmp_path):
        path = tmp_path / "l.lock"
        path.write_text("not json{{{")
        assert read_lock_meta(str(path)) is None

    def test_empty_returns_none(self, tmp_path):
        path = tmp_path / "l.lock"
        path.write_text("")
        assert read_lock_meta(str(path)) is None
