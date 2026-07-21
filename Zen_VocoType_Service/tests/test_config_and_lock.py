"""T1.2 单元测试：配置注册表校验 + 单实例锁。"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from zen_vocotype_service.config import Settings
from zen_vocotype_service.instance_lock import InstanceLock, InstanceLockError


class TestSettings:
    def test_defaults_load(self):
        s = Settings()
        assert s.default_model == "paraformer-large"
        assert "paraformer-large" in s.models
        assert "sensevoice-small" in s.models
        assert s.infer_timeout_s == 60.0
        assert s.queue_max_pending == 4
        assert s.max_connections == 8

    def test_default_model_must_be_registered(self):
        with pytest.raises(Exception, match="不在模型注册表"):
            Settings(default_model="no-such-model")

    def test_registry_entry_source_exclusive(self):
        with pytest.raises(Exception, match="二选一|之一"):
            Settings(models={"bad": {}})
        with pytest.raises(Exception, match="二选一|之一"):
            Settings(
                models={
                    "bad": {"model_id": "iic/x", "local_path": "/somewhere"}
                }
            )

    def test_local_path_entry_accepted(self):
        s = Settings(
            default_model="m",
            models={"m": {"local_path": "/nonexistent/model"}},
        )
        assert s.models["m"].local_path == Path("/nonexistent/model")

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("ZEN_VOCOTYPE_SERVICE_INFER_TIMEOUT_S", "120")
        s = Settings()
        assert s.infer_timeout_s == 120.0


class TestInstanceLock:
    def test_acquire_writes_pid(self, tmp_path):
        lock_file = tmp_path / "svc.lock"
        with InstanceLock(str(lock_file)):
            assert int(lock_file.read_text()) == os.getpid()

    def test_second_acquire_fails(self, tmp_path):
        lock_file = tmp_path / "svc.lock"
        with InstanceLock(str(lock_file)):
            with pytest.raises(InstanceLockError):
                InstanceLock(str(lock_file)).acquire()

    def test_reacquire_after_release(self, tmp_path):
        lock_file = tmp_path / "svc.lock"
        lock = InstanceLock(str(lock_file))
        lock.acquire()
        lock.release()
        lock2 = InstanceLock(str(lock_file))
        lock2.acquire()
        lock2.release()

    def test_no_stale_lock_after_kill9(self, tmp_path):
        """kill -9 后锁随进程死亡自动释放，可直接重启。"""
        lock_file = tmp_path / "svc.lock"
        code = (
            "import time, sys;"
            "from zen_vocotype_service.instance_lock import InstanceLock;"
            f"InstanceLock({str(lock_file)!r}).acquire();"
            "print('locked', flush=True);"
            "time.sleep(60)"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout.readline().strip() == "locked"
            # 持锁期间抢锁必须失败
            with pytest.raises(InstanceLockError):
                InstanceLock(str(lock_file)).acquire()
            proc.kill()  # kill -9
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        # 进程死后无 stale 锁：可立即抢锁成功
        deadline = time.monotonic() + 5
        while True:
            try:
                lock = InstanceLock(str(lock_file))
                lock.acquire()
                lock.release()
                break
            except InstanceLockError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
