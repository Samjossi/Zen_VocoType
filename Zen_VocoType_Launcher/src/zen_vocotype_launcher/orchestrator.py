"""编排主流程（选型七方案 A：拉起确认就绪后即退出；选型五：ExitStack 逆序清理）。

流程（T40 调整：双延迟 + 客户端门控由就绪确认改为固定间隔）：
抢 Launcher 锁 → 识别既有实例（幂等分支）→ 服务端启动延迟倒计时 → 拉起
Service → 客户端启动间隔倒计时 → 拉起 Client → 确认 Client 存活 → 两阶段
就绪等待（整体成败判定）→ 通知完成 → 释放锁退出 0，
🔴 正常路径不杀子进程；失败路径逆序回收**仅本进程拉起的**子进程
（用户自行启动的既有实例 Launcher 无权终止）。

T40 门控语义：Client 拉起不再等待模型 ready（Client 懒连接，识别请求时才连
Socket，先拉起无害）；就绪等待保留但后移为「两端拉起后的整体成败判定」，
超时仍报错 + 通知。

依赖注入（选型十）：全部外部动作经 :class:`OrchestratorDeps` 可替换，
七场景 fake 注入单测（验收标准 2/3 的自动化核心）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from zen_vocotype_launcher import discovery, processes, readiness
from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.discovery import (
    ComponentStatus,
    SocketProbeResult,
)
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.locks import (
    LauncherLock,
    LauncherLockError,
    read_lock_meta,
)
from zen_vocotype_launcher.notify import Notifier
from zen_vocotype_launcher.readiness import (
    ProtocolClient,
    ReadyTimeoutError,
    RequestFailedError,
    ServiceUnavailableError,
    VersionMismatchError,
)


@dataclass
class ComponentTarget:
    """单个组件的拉起目标（targets.py 产出，正式/dev 模式三处差异之一）。"""

    name: str  # "service" / "client"
    argv: list[str]
    lock_path: str
    log_path: Path
    expected_exe: str | None = None
    expected_cmdline_fragment: str | None = None
    env: dict[str, str] | None = None


@dataclass
class LaunchPlan:
    """编排计划：模式 + Socket 路径 + 两端目标（T40 增双延迟字段）。"""

    mode: str  # "prod" / "dev"
    socket_path: str
    service: ComponentTarget
    client: ComponentTarget
    #: 服务端启动延迟（秒）：抢锁/识别后、拉起 Service 前的固定等待（T40）
    service_delay_s: float = 0.0
    #: 客户端启动间隔（秒）：拉起 Service 后、拉起 Client 前的固定等待（T40）
    client_interval_s: float = 0.0


@dataclass
class OrchestratorDeps:
    """依赖注入集合（默认值即真实实现，测试整体替换为 fake）。"""

    spawn: Callable[..., processes.ManagedProcess] = processes.spawn
    wait_for_readiness: Callable[..., None] = readiness.wait_for_readiness
    discover: Callable[..., discovery.DiscoveryResult] = discovery.discover_component
    probe_socket: Callable[..., tuple] = discovery.probe_socket
    client_factory: Callable[[str], ProtocolClient] = ProtocolClient
    notifier: Notifier = field(default_factory=Notifier)
    lock_factory: Callable[..., LauncherLock] = LauncherLock
    log_file: Path = Path("launcher.log")
    #: 睡眠函数（T40 双延迟；测试替换为 fake，🔴 禁止真实睡眠拖慢测试）
    sleep: Callable[[float], None] = time.sleep
    #: 状态回调（T40 托盘模式桥接 Qt Signal；CLI 模式 None 零影响）
    status_callback: Callable[[str], None] | None = None


def _status(deps: OrchestratorDeps, text: str) -> None:
    """状态回调桥接（T40 托盘进度行；未注入时零开销）。"""
    if deps.status_callback is not None:
        deps.status_callback(text)


def _countdown(deps: OrchestratorDeps, seconds: float, label: str) -> None:
    """固定延迟倒计时：按秒回调剩余秒数（🔴 不做无声等待）。

    经 ``deps.sleep`` 睡眠（测试注入 fake）；不足 1 秒的零头最后一次睡足。
    """
    remaining = float(seconds)
    while remaining > 0:
        _status(deps, f"将于 {int(remaining + 0.999)} 秒后{label}")
        step = min(1.0, remaining)
        deps.sleep(step)
        remaining -= step


def run(
    plan: LaunchPlan,
    settings: Settings,
    launcher_lock_path: str,
    *,
    deps: OrchestratorDeps | None = None,
) -> ExitCode:
    """执行编排主流程，返回退出码（单一出口，🔴 禁止散落 sys.exit）。"""
    deps = deps or OrchestratorDeps()
    notifier = deps.notifier
    started = time.monotonic()

    # ---------------------------------------------------------------- 单实例锁
    lock = deps.lock_factory(launcher_lock_path, plan.mode)
    try:
        lock.acquire()
    except LauncherLockError as exc:
        meta = read_lock_meta(launcher_lock_path)
        pid = meta.get("pid") if meta else None
        mode = meta.get("mode", plan.mode) if meta else plan.mode
        logger.error("{}（持有方 pid={}, mode={}）", exc, pid, mode)
        notifier.notify_already_running(pid, mode)
        return ExitCode.ALREADY_RUNNING

    service_owned = False  # 仅回收本进程拉起的子进程（既有实例无权终止）
    try:
        with ExitStack() as stack:
            # -------------------------------------------------- Service 识别
            _status(deps, "识别既有实例…")
            svc = deps.discover(
                plan.service.lock_path,
                name="service",
                expected_exe=plan.service.expected_exe,
                expected_cmdline_fragment=plan.service.expected_cmdline_fragment,
            )
            service_running = svc.status is ComponentStatus.RUNNING

            if not service_running:
                # Socket 兜底探测（🔴 禁止 unlink 他人 Socket）
                probe, probe_detail = deps.probe_socket(plan.socket_path)
                if probe is SocketProbeResult.FOREIGN:
                    logger.error("Socket 路径被外部占用：{}（{}）", plan.socket_path, probe_detail)
                    notifier.notify_failed(f"Socket 被外部占用（{probe_detail}）", str(deps.log_file))
                    return ExitCode.CONFIG_ERROR
                if probe is SocketProbeResult.OURS:
                    logger.info("Socket 已有本组件协议实例（{}），复用", probe_detail)
                    service_running = True

            # ------------------------------------- 服务端启动延迟（T40）
            # 仅在需要拉起时倒计时；既有实例幂等命中不等待
            if not service_running and plan.service_delay_s > 0:
                _countdown(deps, plan.service_delay_s, "启动服务端")

            # -------------------------------------------------- Service 拉起
            notifier.notify_starting()
            spawn_t0 = time.monotonic()  # T1/T2 计时零点（选型七：拉起时刻）
            if not service_running:
                _status(deps, "正在启动服务端…")
                try:
                    service_proc = deps.spawn(
                        plan.service.argv,
                        name="service",
                        env=plan.service.env,
                        log_path=plan.service.log_path,
                    )
                except OSError as exc:
                    logger.error("服务端拉起失败：{}", exc)
                    _status(deps, f"服务端拉起失败：{exc}")
                    notifier.notify_failed(f"服务端拉起失败（{exc}）", str(deps.log_file))
                    return ExitCode.SERVICE_FAILED
                service_owned = True
                stack.callback(
                    _reap, service_proc, settings.terminate_grace_seconds
                )
            else:
                service_proc = None

            # -------------------------------------------------- Client 识别
            cli = deps.discover(
                plan.client.lock_path,
                name="client",
                expected_exe=plan.client.expected_exe,
                expected_cmdline_fragment=plan.client.expected_cmdline_fragment,
            )
            client_running = cli.status is ComponentStatus.RUNNING

            # ------------------------------------- 客户端启动间隔（T40）
            # 门控为固定间隔（Client 懒连接，先拉起无害）；仅在需要拉起时倒计时
            if not client_running and plan.client_interval_s > 0:
                _countdown(deps, plan.client_interval_s, "启动客户端")

            # -------------------------------------------------- Client 拉起
            if not client_running:
                _status(deps, "正在启动客户端…")
                try:
                    client_proc = deps.spawn(
                        plan.client.argv,
                        name="client",
                        env=plan.client.env,
                        log_path=plan.client.log_path,
                    )
                except OSError as exc:
                    logger.error("客户端拉起失败：{}", exc)
                    _status(deps, f"客户端拉起失败：{exc}")
                    notifier.notify_failed(f"客户端拉起失败（{exc}）", str(deps.log_file))
                    return ExitCode.CLIENT_FAILED  # ExitStack 回收 service（若 owned）
                if not client_proc.is_alive():
                    detail = _exit_info(client_proc, plan.client.log_path)
                    logger.error("客户端拉起后立即退出：{}", detail)
                    _status(deps, f"客户端拉起后立即退出：{detail}")
                    notifier.notify_failed(f"客户端拉起后立即退出（{detail}）", str(deps.log_file))
                    return ExitCode.CLIENT_FAILED
                # 正常路径：Client 独立存活，Launcher 退出后不回收（不入 stack）

            # --------------------------------------- 就绪等待（T40 后移：
            # 两端拉起后的整体成败判定；🔴 间隔不替代就绪判定）
            _status(deps, "等待服务端就绪…")
            client_conn = deps.client_factory(plan.socket_path)
            try:
                deps.wait_for_readiness(
                    client_conn,
                    socket_wait_timeout_s=settings.socket_wait_timeout_s,
                    model_ready_timeout_s=settings.model_ready_timeout_s,
                    poll_interval_s=settings.ready_poll_interval_ms / 1000.0,
                    process_alive=(
                        service_proc.is_alive if service_proc is not None else None
                    ),
                    process_exit_info=(
                        (lambda: _exit_info(service_proc, plan.service.log_path))
                        if service_proc is not None
                        else None
                    ),
                    t0=spawn_t0,
                )
            except (
                ReadyTimeoutError,
                ServiceUnavailableError,
                VersionMismatchError,
                RequestFailedError,
            ) as exc:
                logger.error("服务端就绪等待失败：{}", exc)
                _status(deps, f"服务端就绪失败：{exc}")
                notifier.notify_failed(f"服务端就绪失败（{exc}）", str(deps.log_file))
                if service_owned:
                    return ExitCode.SERVICE_FAILED  # ExitStack 逆序回收
                return ExitCode.ALREADY_RUNNING  # 既有实例异常，Launcher 无权终止

            # -------------------------------------------------- 完成
            elapsed = time.monotonic() - started
            logger.info("编排完成（总耗时 {:.1f}s，模式 {}）", elapsed, plan.mode)
            logger.info("启动耗时 T_total_s={:.3f} mode={}", elapsed, plan.mode)
            _status(deps, f"启动完成（总耗时 {elapsed:.1f} 秒）")
            notifier.notify_done(elapsed)
            # 正常路径：弹出全部清理回调，🔴 不杀子进程（选型七方案 A）
            stack.pop_all()
            return ExitCode.SUCCESS
    finally:
        lock.release()


def _reap(proc: processes.ManagedProcess, grace_seconds: float) -> None:
    """ExitStack 清理回调：两段式回收进程组。"""
    proc.terminate_group(grace_seconds)


def _exit_info(proc: processes.ManagedProcess, log_path: Path) -> str:
    """子进程退出详情：退出码 + 日志尾部（失败诊断用）。"""
    tail = processes.read_log_tail(log_path)
    info = f"pid={proc.pid} code={proc.poll()}"
    if tail:
        info += f"，日志尾部：{tail}"
    return info
