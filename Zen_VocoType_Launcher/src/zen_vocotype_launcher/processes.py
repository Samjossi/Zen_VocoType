"""子进程拉起与生命周期管理（选型一方案 A）。

- ``Popen`` 句柄即精确身份（🔴 禁止 psutil 参与自己拉起的子进程管理）
- ``start_new_session=True`` 独立会话 → ``os.killpg`` 进程组整组回收
  （孙进程不逃逸）
- 两段式终止：SIGTERM → 宽限等待（条件轮询，非固定 sleep）→ SIGKILL 兜底

⚠️ 修订记录（2026-07-22 dev 实测）：原选型五方案 C 的 ``PR_SET_PDEATHSIG``
保险与选型七方案 A「拉起确认后即退出」根本冲突——Launcher 正常退出即触发
子进程 SIGTERM，两端被全部带走（dev 实测 SIGTERM 时间戳与 Launcher 退出
时刻吻合）。已移除；Launcher 编排期意外死亡的残留风险由「组件锁文件 +
下次执行幂等收养」（discovery 模块）覆盖。
"""

import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger

#: 两段式终止等待 poll 间隔（秒）。依据：进程退出为毫秒~秒级事件，
#: 50ms 轮询兼顾响应速度与 CPU 空转
_TERMINATE_POLL_INTERVAL_S: float = 0.05


class ManagedProcess:
    """受管子进程：``Popen`` 句柄即唯一身份。"""

    def __init__(self, popen: subprocess.Popen, name: str) -> None:
        self._popen = popen
        self.name = name

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self) -> int | None:
        """返回退出码；仍在运行返回 ``None``（区分「进程已死」与「仍在启动」）。"""
        return self._popen.poll()

    def is_alive(self) -> bool:
        return self.poll() is None

    def terminate_group(self, grace_seconds: float) -> None:
        """两段式终止进程组：SIGTERM → 宽限等待 → SIGKILL 兜底。每步记日志。"""
        if not self.is_alive():
            logger.debug("{}（pid={}）已退出（code={}），无需回收", self.name, self.pid, self.poll())
            return
        try:
            pgid = os.getpgid(self.pid)
        except ProcessLookupError:
            logger.debug("{}（pid={}）已消失，无需回收", self.name, self.pid)
            return
        logger.info("回收 {}（pid={}, pgid={}）：发送 SIGTERM", self.name, self.pid, pgid)
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if not self.is_alive():
                logger.info("{}（pid={}）已随 SIGTERM 退出（code={}）", self.name, self.pid, self.poll())
                return
            time.sleep(_TERMINATE_POLL_INTERVAL_S)
        logger.warning("{}（pid={}）{}s 内未退出，发送 SIGKILL 兜底", self.name, self.pid, grace_seconds)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        self._popen.wait()


#: Popen 工厂类型（依赖注入点，选型十）：与 ``subprocess.Popen`` 同签名
PopenFactory = Callable[..., subprocess.Popen]


def spawn(
    argv: list[str],
    *,
    name: str,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
    popen_factory: PopenFactory = subprocess.Popen,
) -> ManagedProcess:
    """拉起受管子进程（独立会话 + 输出重定向至日志文件）。

    :param argv: 命令行（绝对路径，🔴 禁止 cwd 相对解析——调用方职责）
    :param name: 进程角色名（日志用，如 ``service``/``client``）
    :param env: 子进程环境（None 继承当前环境）
    :param log_path: stdout/stderr 重定向目标；崩溃时可读日志尾部诊断
    :param popen_factory: 依赖注入点（测试可注入 fake）
    """
    log_handle = None
    stdout = None
    try:
        if log_path is not None:
            log_path = Path(log_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_path, "wb", buffering=0)  # 每次拉起开新日志（诊断不受上轮污染）
            stdout = log_handle
        kwargs: dict = {
            "stdout": stdout,
            "stderr": subprocess.STDOUT if stdout is not None else None,
            "env": env,
            "start_new_session": True,  # 独立会话 → killpg 整组回收
        }
        popen = popen_factory(argv, **kwargs)
    except Exception:
        if log_handle is not None:
            log_handle.close()
        raise
    logger.info("已拉起 {}（pid={}）：{}", name, popen.pid, " ".join(argv))
    return ManagedProcess(popen, name)


def read_log_tail(log_path: Path, max_lines: int = 20) -> str:
    """读取子进程日志尾部（崩溃诊断用）；文件缺失返回空串。"""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-max_lines:])
