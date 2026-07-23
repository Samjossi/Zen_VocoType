"""既有实例识别与冲突检测（选型四：PID 文件 + /proc/exe 精确匹配 + Socket 兜底探测）。

- 🔴 禁止 psutil 全表扫描；🔴 禁止 cmdline 子串匹配（旧 Launcher 误报为反面案例）
- 身份精确到可执行文件本身：PID 复用时 exe 不匹配即识别为陈旧文件并清理
- dev 模式（python 解释器启动）exe 相同无法区分，辅以 cmdline 主脚本路径校验
- Socket 兜底探测：路径已存在且可连接 → ``health`` 握手裁决归属；
  🔴 禁止 unlink 他人 Socket
"""

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from loguru import logger

from zen_vocotype_launcher.readiness import (
    ProtocolClient,
    RequestFailedError,
    ServiceUnavailableError,
    VersionMismatchError,
)


class ComponentStatus(Enum):
    ABSENT = "absent"  # 无锁文件或无有效 PID → 需要拉起
    RUNNING = "running"  # 合法运行实例（幂等命中，跳过拉起）
    STALE = "stale"  # 陈旧锁文件（进程死或 exe 不匹配）→ 清理后拉起


@dataclass
class DiscoveryResult:
    status: ComponentStatus
    pid: int | None = None
    detail: str = ""


class SocketProbeResult(Enum):
    FREE = "free"  # Socket 不存在或不可连接 → 可拉起
    OURS = "ours"  # 本组件协议（health 握手通过）→ 复用/提示
    FOREIGN = "foreign"  # 被外部进程占用 → 报错退出码 5


def read_pid_file(lock_path: str) -> int | None:
    """从锁文件读取 PID（Service/Client 锁内容为纯 PID 文本）；缺失/非法返回 None。"""
    try:
        raw = Path(lock_path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(raw.split()[0])
    except ValueError:
        # Launcher 锁文件为 JSON 元信息时尝试解析 pid 字段
        import json

        try:
            meta = json.loads(raw)
            pid = int(meta["pid"])
        except (ValueError, KeyError, TypeError):
            return None
    return pid if pid > 0 else None


def _exe_of(pid: int) -> str | None:
    """读取 ``/proc/<pid>/exe``（不可读返回 None 并记 warning）。"""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError as exc:
        logger.warning("/proc/{}/exe 不可读（{}），按不匹配处理", pid, exc)
        return None


def _appimage_env_of(pid: int) -> str | None:
    """读取载荷进程 ``APPIMAGE`` 环境变量（AppImage runtime 注入）。

    非 AppImage 形态进程无此变量返回 None；/proc 不可读同样返回 None。
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    for entry in raw.split(b"\0"):
        if entry.startswith(b"APPIMAGE="):
            return entry[len(b"APPIMAGE="):].decode("utf-8", errors="replace")
    return None


def is_pid_running_match(
    pid: int,
    *,
    expected_exe: str | None = None,
    expected_cmdline_fragment: str | None = None,
) -> bool:
    """校验 PID 存活且身份匹配（/proc 精确匹配，非子串扫描）。

    :param expected_exe: 期望可执行路径（与 ``/proc/<pid>/exe`` 读链接精确比较）
    :param expected_cmdline_fragment: dev 模式辅助校验——cmdline 必须包含
        该片段（如组件 ``main.py`` 绝对路径）；仅当 exe 为解释器时才有意义
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        logger.warning("PID {} 存活但无权限探测，按不匹配处理", pid)
        return False

    if expected_exe is not None:
        actual_exe = _exe_of(pid)
        if actual_exe is None:
            return False
        exe_match = actual_exe == expected_exe
        if not exe_match and expected_exe.lower().endswith(".appimage"):
            # AppImage 形态回退（阶段 4 T4.8 实测缺口）：载荷 /proc/exe 指向
            # FUSE 挂载点内路径（随机 .mount_* 前缀），与 .AppImage 路径永不
            # 相等——以 runtime 注入的 APPIMAGE 环境变量精确比对（等值语义）。
            # 后缀判定大小写不敏感（T40 实测：用户重命名 .appimage 小写时
            # 误判陈旧并误删活锁文件）
            env_path = _appimage_env_of(pid)
            exe_match = env_path is not None and os.path.realpath(
                env_path
            ) == os.path.realpath(expected_exe)
        if not exe_match:
            logger.debug(
                "PID {} exe 不匹配：期望 {}，实际 {}", pid, expected_exe, actual_exe
            )
            return False

    if expected_cmdline_fragment is not None:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode("utf-8", errors="replace")
        except OSError as exc:
            logger.warning("/proc/{}/cmdline 不可读（{}），按不匹配处理", pid, exc)
            return False
        if expected_cmdline_fragment not in cmdline:
            logger.debug("PID {} cmdline 不含期望片段 {}", pid, expected_cmdline_fragment)
            return False

    return True


def discover_component(
    lock_path: str,
    *,
    name: str,
    expected_exe: str | None = None,
    expected_cmdline_fragment: str | None = None,
) -> DiscoveryResult:
    """识别组件既有实例（三分支：ABSENT/RUNNING/STALE）。

    STALE 时顺带删除陈旧锁文件（清理动作记日志）。
    """
    pid = read_pid_file(lock_path)
    if pid is None:
        return DiscoveryResult(ComponentStatus.ABSENT, detail="无锁文件或无有效 PID")

    if is_pid_running_match(
        pid,
        expected_exe=expected_exe,
        expected_cmdline_fragment=expected_cmdline_fragment,
    ):
        logger.info("{} 已有合法实例运行（pid={}），幂等命中", name, pid)
        return DiscoveryResult(ComponentStatus.RUNNING, pid=pid, detail="exe 精确匹配")

    # 陈旧：进程已死或 PID 复用（exe 不匹配）
    try:
        os.kill(pid, 0)
        detail = f"PID {pid} 存活但身份不匹配（PID 复用）"
    except (ProcessLookupError, PermissionError):
        detail = f"PID {pid} 已退出"
    logger.info("{} 锁文件陈旧（{}），清理 {}", name, detail, lock_path)
    try:
        Path(lock_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("陈旧锁文件删除失败 {}：{}", lock_path, exc)
    return DiscoveryResult(ComponentStatus.STALE, pid=pid, detail=detail)


def probe_socket(socket_path: str) -> tuple[SocketProbeResult, str]:
    """Socket 占用兜底探测（health 握手裁决归属）。

    :returns: (结果, 详情说明)
    """
    if not Path(socket_path).exists():
        return SocketProbeResult.FREE, "Socket 不存在"

    client = ProtocolClient(socket_path)
    try:
        client.connect()
    except ServiceUnavailableError:
        # 路径存在但不可连接：可能是上次异常退出残留的 Socket 文件
        logger.info("Socket 文件存在但不可连接（残留），由服务端 bind 时自行处理：{}", socket_path)
        return SocketProbeResult.FREE, "残留 Socket 文件（不可连接）"
    try:
        payload = client.health()
        return (
            SocketProbeResult.OURS,
            f"本组件协议实例（status={payload.get('status')}）",
        )
    except VersionMismatchError as exc:
        return SocketProbeResult.FOREIGN, f"协议版本不符：{exc}"
    except (ServiceUnavailableError, RequestFailedError) as exc:
        return SocketProbeResult.FOREIGN, f"非本组件协议：{exc}"
    finally:
        client.close()
