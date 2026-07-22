"""客户端单实例锁（flock + PID 记录）。

设计参照服务端 ``instance_lock``（🔴 禁止跨组件 import，本文件为客户端
自有独立实现，大纲原则 7）：

- 启动时 ``fcntl.flock(LOCK_EX | LOCK_NB)`` 抢锁，失败即报「已有实例运行」
- 持锁后写入自身 PID，供 Launcher（阶段 3 选型四）读 PID 做幂等识别
  （``/proc/<pid>/exe`` 可执行路径精确匹配）
- 内核级锁：进程死亡（含 kill -9）自动释放，无 stale 锁问题
- dev 模式锁文件与正式分离（按 Socket 路径推导，见 ``lock_path_for``）

🔴 锁文件路径常量唯一出处为契约库 ``paths``，禁止另写。
"""

import fcntl
import os
from pathlib import Path

from zen_vocotype_protocol.paths import (
    CLIENT_LOCK_PATH,
    DEV_CLIENT_LOCK_PATH,
    DEV_SOCKET_PATH,
)


class InstanceLockError(Exception):
    """单实例锁获取失败（已有实例运行）。"""


def lock_path_for(socket_path: str) -> str:
    """按 Socket 路径选择锁文件：dev Socket 用 dev 锁（dev/正式并行互不干扰）。

    :param socket_path: 配置项 ``Settings.socket_path``
    """
    if socket_path == DEV_SOCKET_PATH:
        return DEV_CLIENT_LOCK_PATH
    return CLIENT_LOCK_PATH


class InstanceLock:
    """单实例锁上下文管理器：进入抢锁写 PID，退出释放。"""

    def __init__(self, lock_path: str = CLIENT_LOCK_PATH) -> None:
        self._lock_path = Path(lock_path)
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> None:
        """抢锁并写入自身 PID。

        :raises InstanceLockError: 已有实例持锁
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise InstanceLockError(
                f"已有客户端实例运行（锁文件 {self._lock_path} 被持有）"
            ) from exc
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd

    def release(self) -> None:
        """释放锁并关闭文件描述符（锁文件保留，内核锁已随 fd 关闭释放）。"""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
