"""Launcher 自身单实例锁（flock + 元信息记录，选型三方案 A）。

- ``fcntl.flock(LOCK_EX | LOCK_NB)`` 非阻塞抢锁；内核级锁，进程死亡
  （含 SIGKILL）自动释放，🔴 无陈旧锁清理逻辑
- 持锁期间写入 JSON 元信息（pid/mode/started_at），供第二次执行读取并提示
- 正式/dev 双锁文件（契约库 ``LAUNCHER_LOCK_PATH``/``DEV_LAUNCHER_LOCK_PATH``，
  唯一出处；🔴 禁止 /tmp 固定路径——契约库已保证用户私有运行目录）
- 抢锁失败 → 读取元信息 → 通知「已在运行」→ 退出码 2（orchestrator 职责）
"""

import fcntl
import json
import os
import time
from pathlib import Path

from zen_vocotype_protocol.paths import DEV_LAUNCHER_LOCK_PATH, LAUNCHER_LOCK_PATH


class LauncherLockError(Exception):
    """Launcher 单实例锁获取失败（已有 Launcher 在编排/运行）。"""


def lock_path_for(dev_mode: bool) -> str:
    """按模式选择锁文件（正式/dev 互不阻塞）。"""
    return DEV_LAUNCHER_LOCK_PATH if dev_mode else LAUNCHER_LOCK_PATH


class LauncherLock:
    """Launcher 单实例锁：抢锁成功写入元信息，退出释放。"""

    def __init__(self, lock_path: str, mode: str) -> None:
        self._lock_path = Path(lock_path)
        self._mode = mode
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> None:
        """抢锁并写入元信息。

        :raises LauncherLockError: 已有实例持锁
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LauncherLockError(
                f"已有 Launcher 实例运行（锁文件 {self._lock_path} 被持有）"
            ) from exc
        meta = {
            "pid": os.getpid(),
            "mode": self._mode,
            "started_at": round(time.time(), 3),
        }
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        self._fd = fd

    def release(self) -> None:
        """释放锁并关闭文件描述符（锁文件保留，内核锁已随 fd 关闭释放）。"""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "LauncherLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def read_lock_meta(lock_path: str) -> dict | None:
    """读取锁文件内元信息（用于「已在运行」提示）；缺失/损坏返回 ``None``。"""
    try:
        raw = Path(lock_path).read_text(encoding="utf-8").strip()
        if not raw:
            return None
        meta = json.loads(raw)
        if not isinstance(meta, dict):
            return None
        return meta
    except (OSError, ValueError):
        return None
