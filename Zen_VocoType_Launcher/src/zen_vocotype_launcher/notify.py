"""状态通知模块（选型八：notify-send 三类时机 + 迁移图标）。

- 通知时机收敛为三类（🔴 禁止每步刷通知）：正在启动 / 启动完成 / 启动失败；
  时机由编排器决定，本模块只负责发送
- ``notify-send`` 缺席 → 降级为仅日志，记 warning（🔴 禁止静默降级）；
  发送失败不阻塞主流程但记 warning
- 图标经 ``icon_loader`` 自定位解析，缺失记 warning（大纲 §5-13）
"""

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from zen_vocotype_launcher import icon_loader

#: notify-send 进程超时（秒）。依据：通知发送为本地 IPC，正常 <100ms，
#: 3s 覆盖极端桌面服务卡顿；超时按发送失败处理（记 warning 不阻塞）
NOTIFY_TIMEOUT_S: float = 3.0

#: 通知标题（桌面通知显示名）
NOTIFY_TITLE: str = "Zen_VocoType"


class Notifier:
    """notify-send 通知器（依赖注入友好：可注入 command_runner 做测试）。"""

    def __init__(self, command_runner=subprocess.run) -> None:
        self._run = command_runner
        self._binary: str | None = shutil.which("notify-send")
        if self._binary is None:
            logger.warning("notify-send 不可用：通知降级为仅日志 + 退出码（无桌面通知）")

    @property
    def available(self) -> bool:
        return self._binary is not None

    def _send(self, body: str, *, urgency: str = "normal") -> None:
        """发送一条通知；任何失败记 warning 不抛出（🔴 禁止阻塞主流程）。"""
        if self._binary is None:
            logger.warning("【通知降级为日志】{}", body)
            return
        cmd = [self._binary, "--urgency", urgency]
        icon = icon_loader.icon_path()
        if icon is not None:
            cmd += ["--icon", str(icon)]
        cmd += [NOTIFY_TITLE, body]
        try:
            self._run(
                cmd,
                timeout=NOTIFY_TIMEOUT_S,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("通知发送失败（{}）：{}", exc, body)

    def notify_starting(self) -> None:
        """「正在启动」：拉起 Service 时。"""
        self._send("正在启动语音输入服务…")

    def notify_done(self, elapsed_s: float) -> None:
        """「启动完成」：客户端就绪后，含总耗时。"""
        self._send(f"启动完成，可以开始语音输入（总耗时 {elapsed_s:.1f} 秒）")

    def notify_failed(self, stage: str, log_hint: str) -> None:
        """「启动失败」：含失败阶段与日志位置提示。"""
        self._send(f"启动失败：{stage}\n详情见日志 {log_hint}", urgency="critical")

    def notify_already_running(self, pid: int | None, mode: str) -> None:
        """「已在运行」：单实例锁冲突时（锁文件元信息可能缺失，pid 可空）。"""
        pid_text = f"PID {pid}" if pid is not None else "PID 未知"
        self._send(f"Zen_VocoType 已在运行（{pid_text}，{mode} 模式），请勿重复启动")
