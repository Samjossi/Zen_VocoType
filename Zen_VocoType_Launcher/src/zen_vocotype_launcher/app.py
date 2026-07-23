"""Launcher 托盘模式装配层（T40）。

职责（🔴 托盘零业务逻辑，全部编排/持久化集中于此）：

- QApplication + LauncherTray 装配；编排经 QThread 执行（🔴 禁止阻塞 Qt
  主线程），状态回调经 Qt Signal 桥接回主线程
- 成功路径：编排完成后经 ``exit_after_success_s`` 单一定时器逐秒倒计时
  自行退出（T44：默认 60 秒，范围 30~300；倒计时显示保留）；
  设置对话框打开期间倒计时显式暂停（try/finally，不依赖 SNI 菜单事件——
  GNOME 上 Shell 预取菜单使 aboutToShow 假阳性 100%，该机制已于
  2026-07-24 拆除）；编排完成若任一端未检出（客户端 AppImage 锁文件
  晚出现），启动 2 秒间隔、15 秒上限的状态重检轮询，命中即停；
  失败路径：托盘停留（状态行红字错误 + 补救入口），🔴 不自动退出
- 三个延迟设置 + 组件位置设置：「校验 → 先落盘 → 后切内存 → 刷标签 → 通知」
  （T33/T35 模板；落盘走契约库 ``set_user_config_value``，🔴 禁止写包内
  config.yaml——AppImage 只读挂载点）
- 无显示环境探测：``QApplication`` 创建前检查 DISPLAY/WAYLAND_DISPLAY
  （headless 下 Qt 可能 SIGABRT 硬崩而非抛异常，🔴 必须先探测后创建）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from loguru import logger

from zen_vocotype_protocol.paths import CLIENT_LOCK_PATH, SERVICE_LOCK_PATH
from zen_vocotype_protocol.user_config import set_user_config_value

from zen_vocotype_launcher.config import (
    CLIENT_BINARY_ENV_VAR,
    CLIENT_START_INTERVAL_ENV_VAR,
    EXIT_AFTER_SUCCESS_ENV_VAR,
    SERVICE_BINARY_ENV_VAR,
    SERVICE_START_DELAY_ENV_VAR,
    Settings,
)
from zen_vocotype_launcher.discovery import ComponentStatus, discover_component
from zen_vocotype_launcher.exit_codes import ExitCode
from zen_vocotype_launcher.locks import lock_path_for
from zen_vocotype_launcher.orchestrator import OrchestratorDeps, run
from zen_vocotype_launcher.targets import TargetResolutionError, build_plan

#: 环境变量覆盖警示文案模板（与 Client T33/T35 同模式）
MSG_ENV_OVERRIDE_TEMPLATE = "检测到环境变量 {}，重启后将以其为准"

#: 延迟设置项 UI 下限/上限（与字段 ge/le 约束对齐）
_DELAY_UI_MAX = 300
_EXIT_UI_MIN = 30
_EXIT_UI_MAX = 300

#: 编排成功后状态重检：客户端 AppImage 启动慢（FUSE 挂载 + 引导 + Qt 初始化），
#: 锁文件晚于编排完成数秒才出现——只查一次会把慢启动客户端误显为「未运行」
_STATUS_RECHECK_INTERVAL_MS = 2000
_STATUS_RECHECK_MAX_S = 15.0

#: 成功后退出定时器轮询间隔（毫秒）：逐秒刷新倒计时显示
_EXIT_TICK_MS = 1000


class TrayUnavailableError(Exception):
    """托盘模式不可用（无显示环境/QApplication 创建失败）→ 回退 CLI。"""


def display_available() -> bool:
    """显示环境探测（🔴 QApplication 创建前必须先探测：headless 下 Qt 可能
    SIGABRT 硬崩而非抛 Python 异常，无法经 try 捕获回退）。"""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _env_override_suffix(env_var: str) -> str:
    """环境变量存在时返回警示后缀（不阻断操作，仅如实告知）。"""
    if os.environ.get(env_var):
        return f"；{MSG_ENV_OVERRIDE_TEMPLATE.format(env_var)}"
    return ""


class _OrchestrationWorker:
    """编排工作对象（QThread 内执行；状态经回调桥 Signal 出线程）。

    不继承 QObject——回调由装配层包成 Signal emit 传入，线程边界只在
    Signal 一处（Qt 自动排队回主线程）。
    """

    def __init__(
        self,
        settings: Settings,
        log_file: Path,
        status_emit,
        finished_emit,
    ) -> None:
        self._settings = settings
        self._log_file = log_file
        self._status_emit = status_emit
        self._finished_emit = finished_emit

    def run(self) -> None:
        """编排入口（工作线程）；任何路径都经 finished_emit 汇报退出码。"""
        try:
            plan = build_plan(self._settings, dev_mode=False)
        except TargetResolutionError as exc:
            logger.error("目标解析失败：{}", exc)
            self._status_emit(f"目标解析失败：{exc}")
            self._finished_emit(int(ExitCode.CONFIG_ERROR))
            return
        deps = OrchestratorDeps(
            log_file=self._log_file,
            status_callback=self._status_emit,
        )
        try:
            code = run(
                plan,
                self._settings,
                lock_path_for(False),
                deps=deps,
            )
        except Exception:  # 未预期异常兜底：🔴 禁止静默成功
            logger.exception("Launcher 内部错误")
            code = ExitCode.INTERNAL_ERROR
        self._finished_emit(int(code))


class LauncherTrayApp:
    """托盘模式控制器：托盘 + 编排线程 + 设置项 + 自动退出倒计时。"""

    def __init__(self, settings: Settings, log_file: Path) -> None:
        if not display_available():
            raise TrayUnavailableError("无 DISPLAY/WAYLAND_DISPLAY 显示环境")
        from PySide6.QtCore import QThread, QTimer
        from PySide6.QtWidgets import QApplication

        try:
            # 复用既有实例（测试进程内可能已创建），否则新建
            self._qapp = QApplication.instance() or QApplication(sys.argv[:1])
        except Exception as exc:
            raise TrayUnavailableError(f"QApplication 创建失败：{exc}") from exc
        self._qapp.setQuitOnLastWindowClosed(False)  # 纯托盘应用，无窗口

        from zen_vocotype_launcher.tray import LauncherTray

        self._settings = settings
        self._log_file = log_file
        self._last_exit_code = int(ExitCode.SUCCESS)
        self._busy = False

        self._tray = LauncherTray()
        self._init_labels()
        self._connect_signals()

        # 编排线程桥（Signal 持有于 self，🔴 防 GC；显式 QueuedConnection——
        # 槽为非 QObject 可调用，Qt 无法按线程亲和自动排队）
        from PySide6.QtCore import Qt

        self._bridge = _TrayBridge()
        self._bridge.progress.connect(
            self._on_progress, Qt.ConnectionType.QueuedConnection
        )
        self._bridge.finished.connect(
            self._on_finished, Qt.ConnectionType.QueuedConnection
        )
        self._thread: QThread | None = None
        self._worker: _OrchestrationWorker | None = None

        # 成功后退出单一定时器（T44；主线程 QTimer，逐秒倒数显示）
        self._exit_remaining = 0
        self._exit_timer = QTimer(self._qapp)
        self._exit_timer.setInterval(_EXIT_TICK_MS)
        self._exit_timer.timeout.connect(self._on_exit_tick)

        # 编排成功后状态重检轮询（客户端锁文件晚出现的误显修正）
        self._status_recheck_deadline = 0.0
        self._status_recheck_timer = QTimer(self._qapp)
        self._status_recheck_timer.setInterval(_STATUS_RECHECK_INTERVAL_MS)
        self._status_recheck_timer.timeout.connect(self._on_status_recheck_tick)

        self._QThread = QThread

    # ------------------------------------------------------------------
    # 装配
    # ------------------------------------------------------------------

    def _init_labels(self) -> None:
        s = self._settings
        self._tray.set_service_delay_label(s.service_start_delay_s)
        self._tray.set_client_interval_label(s.client_start_interval_s)
        self._tray.set_auto_exit_label(s.exit_after_success_s)
        self._tray.set_service_binary_label(s.service_binary)
        self._tray.set_client_binary_label(s.client_binary)

    def _connect_signals(self) -> None:
        t = self._tray
        t.start_requested.connect(self._on_start)
        t.refresh_requested.connect(self.refresh_status)
        t.service_delay_change_requested.connect(self._on_change_service_delay)
        t.client_interval_change_requested.connect(self._on_change_client_interval)
        t.auto_exit_change_requested.connect(self._on_change_auto_exit)
        t.service_binary_change_requested.connect(self._on_change_service_binary)
        t.service_binary_reset_requested.connect(self._on_reset_service_binary)
        t.client_binary_change_requested.connect(self._on_change_client_binary)
        t.client_binary_reset_requested.connect(self._on_reset_client_binary)
        t.quit_requested.connect(self._on_quit)

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def exec(self) -> int:
        """托盘主循环：显示托盘 → 状态检测 → 自动触发首次编排 → 事件循环。"""
        self._tray.show()
        self.refresh_status()
        self._on_start()  # 启动器的本职：启动即编排
        code = self._qapp.exec()
        logger.info("托盘模式退出（事件循环 code={}，编排退出码={}）", code, self._last_exit_code)
        return self._last_exit_code

    # ------------------------------------------------------------------
    # 状态检测（两端实例识别；目标解析失败显式可见——痛点一闭环）
    # ------------------------------------------------------------------

    def refresh_status(self) -> bool:
        """检测两端状态并刷新状态行（「重新检测状态」与编排后调用）。

        :returns: 两端均为运行中返回 True（重检轮询的提前停止条件）；
            目标解析失败或任一端未运行返回 False
        """
        parts = []
        try:
            plan = build_plan(self._settings, dev_mode=False)
            expected = {
                "service": plan.service.expected_exe,
                "client": plan.client.expected_exe,
            }
        except TargetResolutionError as exc:
            logger.warning("状态检测：目标解析失败（{}）", exc)
            self._tray.set_status(f"✗ 目标解析失败：{exc}（可经下方「位置…」项设置）")
            return False
        all_running = True
        for name, label, lock_path in (
            ("service", "Service", SERVICE_LOCK_PATH),
            ("client", "Client", CLIENT_LOCK_PATH),
        ):
            result = discover_component(
                lock_path, name=name, expected_exe=expected[name]
            )
            if result.status is ComponentStatus.RUNNING:
                parts.append(f"{label}：●运行中（pid={result.pid}）")
            elif result.status is ComponentStatus.STALE:
                parts.append(f"{label}：○未运行（已清理陈旧锁）")
                all_running = False
            else:
                parts.append(f"{label}：○未运行")
                all_running = False
        self._tray.set_status("   ".join(parts))
        return all_running

    # ------------------------------------------------------------------
    # 编排（QThread 执行；🔴 禁止阻塞 Qt 主线程）
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._busy:
            logger.info("编排进行中，忽略重复触发")
            return
        self._busy = True
        self._tray.set_busy(True)
        self._stop_exit_countdown()  # 重试时撤销待定退出
        self._status_recheck_timer.stop()
        worker = _OrchestrationWorker(
            self._settings,
            self._log_file,
            self._bridge.progress.emit,
            self._bridge.finished.emit,
        )
        # thread.started 在新线程上下文发射，直连的 worker.run 即在新线程执行
        # （worker 非 QObject，无需 moveToThread）；worker 引用自持防 GC
        thread = self._QThread(self._qapp)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        self._worker = worker
        self._thread = thread
        thread.start()

    def _on_progress(self, text: str) -> None:
        """编排状态回调（主线程）：进度行刷新。"""
        self._tray.set_progress(text)

    def _on_finished(self, exit_code: int) -> None:
        """编排完成（主线程）：成功倒计时退出；失败托盘停留。"""
        self._last_exit_code = exit_code
        self._busy = False
        self._tray.set_busy(False)
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None
        all_running = self.refresh_status()
        if exit_code == int(ExitCode.SUCCESS):
            self._start_exit_countdown()
            if not all_running:
                # 客户端 AppImage 启动慢，锁文件晚于编排完成数秒才出现——
                # 短轮询重检，命中或超时即停（🔴 禁止一次性快照误显未运行）
                self._start_status_recheck()
        else:
            # 🔴 失败路径不自动退出（防静默失败）：错误态停留 + 通知
            self._tray.set_progress(f"✗ 启动失败（退出码 {exit_code}），可调整后经「立即启动」重试")
            self._notify("Zen_VocoType 启动失败", f"退出码 {exit_code}，详情见托盘菜单与日志")

    # ------------------------------------------------------------------
    # 成功后退出（T44 单一定时器；设置对话框打开期间显式暂停）
    # ------------------------------------------------------------------

    def _start_exit_countdown(self) -> None:
        self._exit_remaining = int(self._settings.exit_after_success_s + 0.999)
        self._tray.set_progress(
            f"✓ 启动完成，启动器将于 {self._exit_remaining} 秒后退出"
        )
        self._exit_timer.start()

    def _stop_exit_countdown(self) -> None:  # 「立即启动」重试时撤销
        self._exit_timer.stop()
        self._exit_remaining = 0

    def _on_exit_tick(self) -> None:
        self._exit_remaining -= 1
        if self._exit_remaining <= 0:
            self._exit_timer.stop()
            logger.info(
                "成功后退出定时器到点（{} 秒），Launcher 退出（两端不受影响）",
                self._settings.exit_after_success_s,
            )
            self._qapp.quit()
        else:
            self._tray.set_progress(
                f"✓ 启动完成，启动器将于 {self._exit_remaining} 秒后退出"
            )

    def _pause_exit_timer(self) -> bool:
        """设置对话框弹出期间停走（返回是否原本在走，供恢复判断）。

        🔴 只允许经本显式 try/finally 路径暂停——禁止重引 SNI 菜单事件
        （aboutToShow 在 GNOME 上被 Shell 预取触发，假阳性 100%，2026-07-24
        诊断定案后拆除）。
        """
        if self._exit_timer.isActive():
            self._exit_timer.stop()
            return True
        return False

    def _resume_exit_timer(self, was_active: bool) -> None:
        if was_active:
            self._exit_timer.start()  # interval 不变，从剩余秒数续走

    # ------------------------------------------------------------------
    # 编排成功后状态重检（慢启动客户端的锁文件等待；命中/超时即停）
    # ------------------------------------------------------------------

    def _start_status_recheck(self) -> None:
        self._status_recheck_deadline = time.monotonic() + _STATUS_RECHECK_MAX_S
        self._status_recheck_timer.start()
        logger.info(
            "状态重检轮询已启动（每 {} ms，上限 {} 秒）",
            _STATUS_RECHECK_INTERVAL_MS,
            _STATUS_RECHECK_MAX_S,
        )

    def _on_status_recheck_tick(self) -> None:
        if self.refresh_status():
            self._status_recheck_timer.stop()
            logger.info("状态重检：两端均已运行，停止轮询")
        elif time.monotonic() >= self._status_recheck_deadline:
            self._status_recheck_timer.stop()
            logger.info("状态重检：已达上限（{} 秒），停止轮询", _STATUS_RECHECK_MAX_S)

    # ------------------------------------------------------------------
    # 延迟设置项（T33/T35 模板：校验 → 先落盘 → 后切内存 → 刷标签 → 通知）
    # ------------------------------------------------------------------

    def _apply_delay(self, key: str, value: int, env_var: str, label: str) -> None:
        """延迟设置热切换：落盘失败整体不生效。"""
        try:
            set_user_config_value(key, value)
        except OSError as exc:
            logger.error("配置写入失败（{}）：{}", key, exc)
            self._notify("设置未生效", f"配置写入失败：{exc}")
            return
        setattr(self._settings, key, float(value))
        refresh = {
            "service_start_delay_s": self._tray.set_service_delay_label,
            "client_start_interval_s": self._tray.set_client_interval_label,
            "exit_after_success_s": self._tray.set_auto_exit_label,
        }[key]
        refresh(float(value))
        self._notify("设置已更新", f"{label}已更新为 {value} 秒{_env_override_suffix(env_var)}")
        logger.info("{} 已更新：{} 秒（已持久化）", key, value)

    def _ask_int(
        self,
        title: str,
        label: str,
        current: float,
        maximum: int,
        minimum: int = 0,
    ) -> int | None:
        from PySide6.QtWidgets import QInputDialog

        # 模态对话框弹出期间显式停走倒计时（关闭后从剩余秒数续走）
        was_active = self._pause_exit_timer()
        try:
            value, ok = QInputDialog.getInt(
                None, title, label, int(current), minimum, maximum, 1
            )
        finally:
            self._resume_exit_timer(was_active)
        return value if ok else None

    def _on_change_service_delay(self) -> None:
        value = self._ask_int(
            "服务端启动延迟",
            "Launcher 启动后多少秒才拉起服务端（0~300）：",
            self._settings.service_start_delay_s,
            _DELAY_UI_MAX,
        )
        if value is not None:
            self._apply_delay(
                "service_start_delay_s", value, SERVICE_START_DELAY_ENV_VAR, "服务端启动延迟"
            )

    def _on_change_client_interval(self) -> None:
        value = self._ask_int(
            "客户端启动间隔",
            "服务端拉起后隔多少秒再拉起客户端（0~300）：\n"
            "模型加载慢的机器建议给足（如 10~20 秒）",
            self._settings.client_start_interval_s,
            _DELAY_UI_MAX,
        )
        if value is not None:
            self._apply_delay(
                "client_start_interval_s", value, CLIENT_START_INTERVAL_ENV_VAR, "客户端启动间隔"
            )

    def _on_change_auto_exit(self) -> None:
        value = self._ask_int(
            "成功后自动退出",
            "编排成功后托盘停留多少秒自动退出（30~300）：",
            self._settings.exit_after_success_s,
            _EXIT_UI_MAX,
            _EXIT_UI_MIN,
        )
        if value is not None:
            self._apply_delay(
                "exit_after_success_s", value, EXIT_AFTER_SUCCESS_ENV_VAR, "成功后自动退出"
            )

    # ------------------------------------------------------------------
    # 组件位置设置（校验：存在 + 是文件 + 可执行；🔴 非法拒绝落盘）
    # ------------------------------------------------------------------

    def _apply_binary(self, key: str, path: str | None, env_var: str, label: str) -> None:
        try:
            set_user_config_value(key, path)
        except OSError as exc:
            logger.error("配置写入失败（{}）：{}", key, exc)
            self._notify("设置未生效", f"配置写入失败：{exc}")
            return
        setattr(self._settings, key, path)
        if key == "service_binary":
            self._tray.set_service_binary_label(path)
        else:
            self._tray.set_client_binary_label(path)
        shown = path if path is not None else "自动解析（邻接目录约定）"
        self._notify("设置已更新", f"{label}：{shown}{_env_override_suffix(env_var)}")
        logger.info("{} 已更新：{}（已持久化）", key, shown)
        self.refresh_status()  # 位置变更可能影响解析结果，立即重检

    def _pick_binary(self, title: str) -> str | None:
        from PySide6.QtWidgets import QFileDialog

        # 模态对话框弹出期间显式停走倒计时（关闭后从剩余秒数续走）
        was_active = self._pause_exit_timer()
        try:
            path, _ = QFileDialog.getOpenFileName(
                None,
                title,
                str(Path.home()),
                "AppImage/可执行文件 (*.AppImage *.appimage);;全部文件 (*)",
            )
        finally:
            self._resume_exit_timer(was_active)
        if not path:
            return None
        candidate = Path(path)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            self._notify("设置未生效", f"不可执行或不是文件：{path}")
            logger.warning("位置设置被拒绝（不可执行/非文件）：{}", path)
            return None
        return path

    def _on_change_service_binary(self) -> None:
        path = self._pick_binary("选择 Service 二进制（AppImage 或 onedir 内可执行文件）")
        if path is not None:
            self._apply_binary("service_binary", path, SERVICE_BINARY_ENV_VAR, "Service 位置")

    def _on_reset_service_binary(self) -> None:
        self._apply_binary("service_binary", None, SERVICE_BINARY_ENV_VAR, "Service 位置")

    def _on_change_client_binary(self) -> None:
        path = self._pick_binary("选择 Client 二进制（AppImage 或 onedir 内可执行文件）")
        if path is not None:
            self._apply_binary("client_binary", path, CLIENT_BINARY_ENV_VAR, "Client 位置")

    def _on_reset_client_binary(self) -> None:
        self._apply_binary("client_binary", None, CLIENT_BINARY_ENV_VAR, "Client 位置")

    # ------------------------------------------------------------------
    # 通知 / 退出
    # ------------------------------------------------------------------

    def _notify(self, title: str, message: str) -> None:
        """托盘气泡通知（showMessage；桌面服务缺失时 Qt 内部降级，不崩溃）。"""
        from PySide6.QtWidgets import QSystemTrayIcon

        self._tray.tray_icon.showMessage(
            title, message, QSystemTrayIcon.MessageIcon.Information, 5000
        )

    def _on_quit(self) -> None:
        """退出启动器（🔴 不终止两端——选型七红线）。"""
        logger.info("用户退出启动器（已启动组件不受影响）")
        self._qapp.quit()


class _TrayBridge:
    """跨线程 Signal 桥（🔴 工作线程只经 Signal 回主线程，禁止直触 QWidget）。

    槽为普通 Python 可调用（非 QObject 方法），Qt 无法按线程亲和自动排队，
    🔴 连接必须显式 ``QueuedConnection``（offscreen 测试同线程亦安全）。
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            progress = Signal(str)
            finished = Signal(int)

        self._inner = _Bridge()
        self.progress = self._inner.progress
        self.finished = self._inner.finished


def run_tray_mode(settings: Settings, log_file: Path) -> int:
    """托盘模式入口：装配并进入事件循环，返回编排退出码。

    :raises TrayUnavailableError: 无显示环境/QApplication 创建失败（回退 CLI）
    """
    app = LauncherTrayApp(settings, log_file)
    return app.exec()
