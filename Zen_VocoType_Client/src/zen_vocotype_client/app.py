"""应用装配层（选型一：全部异步事件经 Qt 信号收敛到主线程驱动状态机）。

线程划分（红线，写入评审检查单）：

- Qt 主线程：托盘 UI、状态机持有、全部状态展示、剪贴板读写
- 网络 worker（QThread）：Socket 长连接收发，仅经信号通信
- pynput 回调线程：仅 ``Signal.emit``，🔴 禁止触碰业务状态
- sounddevice 回调线程：仅写线程安全队列 + 置 Event，🔴 禁止业务调用

事件流::

    热键 press（仅 idle 有效）→ 录音开始 → release/上限 → 停录音
    → PCM 投递网络 worker → 识别完成 → 输出（剪贴板+粘贴+恢复）→ 归位 idle
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger
from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, QTimer
from zen_vocotype_protocol.paths import (
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_SAMPLE_WIDTH,
    ensure_user_dir,
)
from zen_vocotype_protocol.user_config import set_user_config_value

from .config import HOTKEY_ENV_VAR, RESTORE_DELAY_ENV_VAR, Settings
from .hotkey.combo import format_hotkey_display, parse_hotkey
from .hotkey.pynput_backend import PynputBackend
from .output.clipboard import ClipboardError, create_clipboard
from .output.paster import PasteError, create_paster
from .output.restore import OutputPipeline
from .recorder.recorder import DeviceUnavailableError, Recorder
from .state_machine import Event, State, StateMachine
from .storage import RecordingStore
from .transcribe import worker as worker_mod
from .transcribe.worker import NetworkWorker
from .tray.hotkey_dialog import HotkeyCaptureDialog
from .tray.notifier import Notifier
from .tray.tray import APP_DISPLAY_NAME, ClientTray, TrayStatus

#: 提示文案（T2.7 定稿三类 + 通用识别失败；README 故障排查节同步）
MSG_SERVER_ABSENT = "服务端未运行——请启动 Zen_VocoType 服务端后，从托盘菜单「重试连接服务端」"
MSG_VERSION_MISMATCH = "协议版本不兼容：{}——请更新客户端/服务端至匹配版本"
MSG_MODEL_SWITCHING = "模型切换中，请稍候片刻再试"
MSG_NOT_READY = "服务端正在加载模型，请稍候"
MSG_MAX_RECORD = "已达最大录音时长，自动进入识别"
MSG_LOADING_TIMEOUT = "服务端模型加载等待超时——请从托盘菜单「重试连接服务端」"
MSG_HOTKEY_BUSY = "录音/识别进行中，请结束后再修改快捷键"
MSG_HOTKEY_UPDATED = "快捷键已更新为 {}"
MSG_HOTKEY_INVALID = "快捷键表达式非法：{}"
MSG_HOTKEY_PERSIST_FAILED = "快捷键配置写入失败：{}——本次修改未生效"
MSG_HOTKEY_SWITCH_FAILED = "热键监听失效，请重启客户端（{}）"
MSG_HOTKEY_ENV_OVERRIDE = f"检测到环境变量 {HOTKEY_ENV_VAR}，重启后将以其为准"
MSG_RESTORE_DELAY_INVALID = "恢复延迟数值非法：{}——本次修改未生效"
MSG_RESTORE_DELAY_PERSIST_FAILED = "恢复延迟配置写入失败：{}——本次修改未生效"
MSG_RESTORE_DELAY_UPDATED = "剪贴板恢复延迟已更新为 {}ms"
MSG_RESTORE_DELAY_ENV_OVERRIDE = f"检测到环境变量 {RESTORE_DELAY_ENV_VAR}，重启后将以其为准"
MSG_SAVE_WAV_FAILED = "录音保存失败：{}——识别与粘贴不受影响"
MSG_SAVE_TXT_FAILED = "识别文本保存失败：{}（录音文件已保留）"
MSG_SAVE_TOGGLE_PERSIST_FAILED = "录音开关配置写入失败：{}——开关状态未变更"
MSG_SAVE_DIR_BUSY = "录音/识别进行中，请结束后再选择保存路径"
MSG_SAVE_DIR_INVALID = "所选保存路径不可写：{}"
MSG_SAVE_DIR_PERSIST_FAILED = "保存路径配置写入失败：{}——本次修改未生效"
MSG_SAVE_DIR_UPDATED = "录音保存路径已更新：{}"
MSG_SAVE_DIR_OPEN_FAILED = "保存文件夹打开失败：{}"


def failure_message(code: int, message: str) -> str:
    """协议错误码 → 用户提示文案映射（🔴 错误码透传不改写，仅附加用户向文案）。"""
    from zen_vocotype_protocol import errors

    if code == 0:
        return MSG_SERVER_ABSENT
    if code == errors.ERR_NOT_READY:
        return MSG_NOT_READY
    if code == errors.ERR_BUSY and "model_switching" in message:
        return MSG_MODEL_SWITCHING
    return f"识别失败 [{code}] {message}"


class ClientApp(QObject):
    """客户端装配：状态机 + 四线程事件源接线。"""

    # 回调线程 → 主线程的收敛信号（回调线程内只允许 emit 这些信号）
    sig_hotkey_press = Signal()
    sig_hotkey_release = Signal()
    sig_record_max = Signal()
    # 主线程 → 网络 worker 线程
    sig_recognize_request = Signal(bytes)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._qthread: QThread | None = None

        # --- 状态机（仅主线程持有） ---
        self._sm = StateMachine()
        self._sm.add_listener(self._on_transition)

        # --- 托盘与通知（无托盘环境降级，C4） ---
        from PySide6.QtWidgets import QSystemTrayIcon

        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray: ClientTray | None = ClientTray()
            self._tray.retry_requested.connect(self._on_retry)
            self._tray.hotkey_change_requested.connect(self._on_change_hotkey)
            self._tray.restore_delay_change_requested.connect(
                self._on_change_restore_delay
            )
            self._tray.save_toggled.connect(self._on_toggle_save)
            self._tray.choose_dir_requested.connect(self._on_choose_dir)
            self._tray.open_dir_requested.connect(self._on_open_dir)
            self._tray.set_hotkey_label(settings.hotkey)
            self._tray.set_restore_delay_label(settings.paste_restore_delay_ms)
            self._tray.set_save_checked(settings.save_recordings)
        else:
            logger.warning("系统托盘不可用，进入无托盘降级模式（C4）")
            self._tray = None
        self._notifier = Notifier(
            self._tray,
            dedup_seconds=settings.notify_dedup_seconds,
            enable_sound=settings.enable_sound_notify,
        )
        self._service_tray_status = TrayStatus.DISCONNECTED

        # --- LOADING 态 health 轮询（服务端在线但模型未就绪 → 周期探测直至终态） ---
        # 🔴 网络 I/O 仍只在 worker 线程：本定时器仅负责调度，探测经
        # QMetaObject.invokeMethod(QueuedConnection) 投递；有次数上限（选型二红线）
        self._loading_poll_timer = QTimer(self)
        self._loading_poll_timer.setInterval(settings.loading_poll_interval_ms)
        self._loading_poll_timer.timeout.connect(self._on_loading_poll_tick)
        self._loading_poll_count = 0

        # --- 录音 ---
        self._recorder = Recorder(
            device=settings.input_device,
            max_record_seconds=settings.max_record_seconds,
            on_max_reached=self.sig_record_max.emit,  # 回调线程仅发信号
        )

        # --- 录音/识别文本落盘（T34）：音频参数唯一出处在契约库 paths 冻结常量；
        # _current_wav_path 单实例成员——状态机保证同一时刻仅一路录音-识别在途
        self._store = RecordingStore(
            settings.recordings_dir,
            sample_rate=DEFAULT_SAMPLE_RATE,
            sample_width=DEFAULT_SAMPLE_WIDTH,
            channels=DEFAULT_CHANNELS,
        )
        self._current_wav_path: Path | None = None

        # --- 热键 ---
        combo = parse_hotkey(settings.hotkey)
        self._hotkey = self._build_hotkey_backend(combo)

        # --- 网络 worker（QThread） ---
        self._worker = NetworkWorker(settings.socket_path)
        self._worker.sig_service_status.connect(self._on_service_status)
        self._worker.sig_recognize_done.connect(self._on_recognize_done)
        self._worker.sig_recognize_failed.connect(self._on_recognize_failed)
        self._worker.sig_version_mismatch.connect(self._on_version_mismatch)
        self.sig_recognize_request.connect(self._worker.recognize)

        # --- 输出流水线 ---
        self._pipeline = OutputPipeline(
            create_clipboard(),
            create_paster(),
            restore_delay_ms=settings.paste_restore_delay_ms,
            scheduler=lambda ms, cb: QTimer.singleShot(ms, cb),
        )

        # --- 信号接线（收敛主线程） ---
        self.sig_hotkey_press.connect(self._on_press)
        self.sig_hotkey_release.connect(self._on_release)
        self.sig_record_max.connect(self._on_record_max)

    # ------------------------------------------------------------------ 启动

    def start(self) -> int:
        """启动序列：设备探测 → 网络线程+health 探测 → 热键 → 托盘。

        任一启动校验失败明确报错并返回非零（C1/C4）。
        """
        try:
            self._recorder.probe_device()
        except DeviceUnavailableError as exc:
            logger.error("录音设备探测失败：{}", exc)
            self._notifier.notify(APP_DISPLAY_NAME, f"录音设备不可用：{exc}")
            return 3

        self._qthread = QThread()
        self._worker.moveToThread(self._qthread)
        self._qthread.start()
        self._request_probe()

        try:
            self._hotkey.start()
        except Exception as exc:
            logger.error("热键后端启动失败：{}", exc)
            self._notifier.notify(APP_DISPLAY_NAME, f"全局热键不可用：{exc}")
            self._stop_network_thread()
            return 4

        if self._tray is not None:
            self._tray.show()
        logger.info("客户端启动完成（热键 {}）", self._settings.hotkey)
        return 0

    def shutdown(self) -> None:
        """确定性退出序列：轮询定时器 → 热键 → 录音 → 网络线程 → 托盘。"""
        logger.info("客户端退出序列开始")
        # 先停轮询，防止退出序列中定时器触发向已停 worker 线程悬挂投递探针
        self._loading_poll_timer.stop()
        self._hotkey.stop()
        self._recorder.close()
        self._stop_network_thread()
        if self._tray is not None:
            self._tray.tray_icon.hide()

    def _stop_network_thread(self) -> None:
        if self._qthread is not None:
            self._worker.shutdown()
            self._qthread.quit()
            self._qthread.wait(2000)
            self._qthread = None

    # ------------------------------------------------------------------ 事件入口（主线程）

    def _on_press(self) -> None:
        if self._sm.state is State.IDLE:
            self._sm.fire(Event.HOTKEY_PRESS)

    def _on_release(self) -> None:
        if self._sm.state is State.RECORDING:
            self._sm.fire(Event.HOTKEY_RELEASE)

    def _on_record_max(self) -> None:
        if self._sm.state is State.RECORDING:
            self._notifier.notify(APP_DISPLAY_NAME, MSG_MAX_RECORD, key="max-record")
            self._sm.fire(Event.RECORD_MAX_REACHED)

    def _on_retry(self) -> None:
        self._request_probe()

    # ------------------------------------------------------------------ 热键修改（主线程）

    def _build_hotkey_backend(self, combo) -> PynputBackend:
        """热键后端构造（单一出处：启动装配/热切换/失败恢复三处共用，🔴 禁止散写）。

        回调注册 sig_hotkey_press/release.emit（回调线程红线：仅发信号）。
        """
        return PynputBackend(
            combo,
            on_press=self.sig_hotkey_press.emit,
            on_release=self.sig_hotkey_release.emit,
        )

    def _on_change_hotkey(self) -> None:
        """托盘「修改快捷键…」入口：仅 IDLE 态放行（🔴 禁止忙碌中热切换，
        避免切换窗口丢失 release 事件卡死状态机）。"""
        if self._sm.state is not State.IDLE:
            self._notifier.notify(APP_DISPLAY_NAME, MSG_HOTKEY_BUSY, key="hotkey-busy")
            return
        dialog = HotkeyCaptureDialog(current=self._settings.hotkey)
        from PySide6.QtWidgets import QDialog  # 局部 import：仅此处需要 DialogCode

        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.expression:
            self._apply_hotkey(dialog.expression)

    def _apply_hotkey(self, expression: str) -> None:
        """校验 → 落盘 → 热切换 → 内存/界面同步；任一步失败回滚并通知。

        顺序决策（🔴 先落盘后切换）：落盘失败整体放弃，运行态快捷键不变，
        避免「运行态已换、重启后丢失」的知行分裂。
        """
        # 状态复查（🔴 必须）：dialog.exec() 嵌套事件循环期间 pynput 信号照常
        # 投递，入口 IDLE 检查后状态可能已进入 RECORDING——此处不切旧 tracker
        # 才能避免丢失 release 事件卡死状态机
        if self._sm.state is not State.IDLE:
            self._notifier.notify(APP_DISPLAY_NAME, MSG_HOTKEY_BUSY, key="hotkey-busy")
            return
        # ① 兜底校验（解析逻辑单一出处在 hotkey.combo）
        try:
            new_combo = parse_hotkey(expression)
        except ValueError as exc:
            logger.warning("快捷键表达式非法，拒绝切换：{}", exc)
            self._notifier.notify(APP_DISPLAY_NAME, MSG_HOTKEY_INVALID.format(exc),
                                  key="hotkey-invalid")
            return
        # ② 持久化（用户配置文件层，AppImage 只读挂载下唯一合法落点）
        try:
            path = set_user_config_value("hotkey", expression)
        except Exception as exc:
            logger.error("快捷键配置写入失败：{}", exc)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_HOTKEY_PERSIST_FAILED.format(exc),
                key="hotkey-persist-failed",
            )
            return
        logger.info("快捷键配置已持久化：{} → {}", expression, path)
        # ③ 热切换：停旧监听 → 起新监听；失败恢复原后端并回滚落盘
        old_backend = self._hotkey
        old_combo = old_backend._combo
        old_backend.stop()
        new_backend = self._build_hotkey_backend(new_combo)
        try:
            new_backend.start()
        except Exception as exc:
            logger.error("新热键监听启动失败，尝试恢复原快捷键：{}", exc)
            self._rollback_hotkey_persist(old_combo.expression)
            restored = self._build_hotkey_backend(old_combo)
            try:
                restored.start()
                self._hotkey = restored
            except Exception as restore_exc:  # 恢复也失败 → 明确报错，不静默空跑
                logger.error("恢复原热键监听失败：{}", restore_exc)
                self._set_tray(TrayStatus.ERROR, "热键监听失效")
                self._notifier.notify(
                    APP_DISPLAY_NAME, MSG_HOTKEY_SWITCH_FAILED.format(restore_exc),
                    key="hotkey-switch-failed",
                )
                return
            self._notifier.notify(
                APP_DISPLAY_NAME,
                f"新快捷键监听启动失败（{exc}），已恢复原快捷键 "
                f"{format_hotkey_display(old_combo.expression)}",
                key="hotkey-switch-failed",
            )
            return
        self._hotkey = new_backend
        # ④ 内存/界面同步 + 成功通知（环境变量优先级高于用户配置文件，如实告知）
        self._settings.hotkey = expression
        if self._tray is not None:
            self._tray.set_hotkey_label(expression)
        message = MSG_HOTKEY_UPDATED.format(format_hotkey_display(expression))
        if os.environ.get(HOTKEY_ENV_VAR):
            message += f"；{MSG_HOTKEY_ENV_OVERRIDE}"
        self._notifier.notify(APP_DISPLAY_NAME, message, key="hotkey-updated")
        logger.info("快捷键热切换完成：{}", expression)

    def _rollback_hotkey_persist(self, old_expression: str) -> None:
        """热切换失败后的落盘回滚（配置文件恢复原快捷键，避免知行分裂）。

        回滚自身失败仅记日志：运行态已恢复原后端，残留配置下次启动仍可
        正常加载（表达式合法），不构成启动失败风险。
        """
        try:
            set_user_config_value("hotkey", old_expression)
            logger.info("快捷键配置已回滚：{}", old_expression)
        except Exception:
            logger.exception("快捷键配置回滚失败（残留新值，下次启动将生效）")

    # ------------------------------------------------------------------ 恢复延迟（主线程，T35）

    def _on_change_restore_delay(self) -> None:
        """托盘「剪贴板恢复延迟…」入口（主线程）。

        🔴 无忙碌守卫：本参数与状态机/事件流零交互，仅影响 OutputPipeline
        下一次 output() 的调度参数，录音/识别中修改安全（决策固化于
        test_t35 忙碌用例）。
        """
        from PySide6.QtWidgets import QInputDialog  # 局部 import：仅此处需要

        value, ok = QInputDialog.getInt(
            None,
            "剪贴板恢复延迟",
            "粘贴后恢复原剪贴板内容的延迟（毫秒）：\n"
            "个别应用粘贴出旧内容时可调大（如 300~500）",
            self._settings.paste_restore_delay_ms,
            0,
            10000,
            50,
        )
        if ok:
            self._apply_restore_delay(value)

    def _apply_restore_delay(self, value: int) -> None:
        """校验 → 落盘 → 运行态切换 → 内存/界面同步；失败不生效并通知。

        顺序决策（🔴 先落盘后切换，沿用 T33/T34）：落盘失败整体放弃，
        运行态延迟不变，避免「运行态已换、重启后丢失」的知行分裂。
        """
        # ① 兜底校验（对齐 Settings 字段 ge=0 约束）
        if value < 0:
            logger.warning("恢复延迟数值非法，拒绝切换：{}", value)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_RESTORE_DELAY_INVALID.format(value),
                key="restore-delay-invalid",
            )
            return
        # ② 持久化（用户配置文件层，AppImage 只读挂载下唯一合法落点）
        try:
            path = set_user_config_value("paste_restore_delay_ms", value)
        except Exception as exc:
            logger.error("恢复延迟配置写入失败：{}", exc)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_RESTORE_DELAY_PERSIST_FAILED.format(exc),
                key="restore-delay-persist-failed",
            )
            return
        logger.info("恢复延迟配置已持久化：{} → {}", value, path)
        # ③ 运行态切换（①已校验，底层红线为双保险）
        self._pipeline.set_restore_delay_ms(value)
        # ④ 内存/界面同步 + 成功通知（环境变量优先级高于用户配置文件，如实告知）
        self._settings.paste_restore_delay_ms = value
        if self._tray is not None:
            self._tray.set_restore_delay_label(value)
        message = MSG_RESTORE_DELAY_UPDATED.format(value)
        if os.environ.get(RESTORE_DELAY_ENV_VAR):
            message += f"；{MSG_RESTORE_DELAY_ENV_OVERRIDE}"
        self._notifier.notify(APP_DISPLAY_NAME, message, key="restore-delay-updated")
        logger.info("恢复延迟热切换完成：{}ms", value)

    # ------------------------------------------------------------------ 录音保存（主线程，T34）

    def _on_toggle_save(self, checked: bool) -> None:
        """托盘「保存录音」勾选项：先落盘后切换（沿用 T33 顺序，🔴 禁止知行分裂）。

        落盘失败回滚勾选态并通知，运行态开关保持不变。
        """
        try:
            set_user_config_value("save_recordings", bool(checked))
        except Exception as exc:
            logger.error("录音开关配置写入失败：{}", exc)
            if self._tray is not None:
                self._tray.set_save_checked(self._settings.save_recordings)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_SAVE_TOGGLE_PERSIST_FAILED.format(exc),
                key="save-toggle-failed",
            )
            return
        self._settings.save_recordings = bool(checked)
        logger.info("录音保存开关已切换并持久化：{}", checked)

    def _on_choose_dir(self) -> None:
        """托盘「选择保存路径…」：仅 IDLE 态放行，选目录后委托 _apply_save_dir。"""
        if self._sm.state is not State.IDLE:
            self._notifier.notify(APP_DISPLAY_NAME, MSG_SAVE_DIR_BUSY, key="save-dir-busy")
            return
        from PySide6.QtWidgets import QFileDialog  # 局部 import：仅此处需要

        directory = QFileDialog.getExistingDirectory(
            None, "选择录音保存路径", str(self._settings.recordings_dir)
        )
        if not directory:  # 用户取消
            return
        self._apply_save_dir(Path(directory))

    def _apply_save_dir(self, target: Path) -> bool:
        """可写探测 → 落盘 → 内存/Store 同步 → 通知；任一步失败不生效。

        :return: True=已生效
        """
        try:
            ensure_user_dir(target)  # 同目录临时文件试写（🔴 非系统临时目录）
        except OSError as exc:
            logger.warning("所选保存路径不可写：{}（{}）", target, exc)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_SAVE_DIR_INVALID.format(exc), key="save-dir-invalid"
            )
            return False
        try:
            set_user_config_value("recordings_dir", str(target))
        except Exception as exc:
            logger.error("保存路径配置写入失败：{}", exc)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_SAVE_DIR_PERSIST_FAILED.format(exc),
                key="save-dir-persist-failed",
            )
            return False
        self._settings.recordings_dir = target
        self._store.set_directory(target)
        logger.info("录音保存路径已更新并持久化：{}", target)
        self._notifier.notify(
            APP_DISPLAY_NAME, MSG_SAVE_DIR_UPDATED.format(target), key="save-dir-updated"
        )
        return True

    def _on_open_dir(self) -> None:
        """托盘「打开保存文件夹」：目录不存在先创建，再经桌面服务打开。"""
        from PySide6.QtCore import QUrl  # 局部 import：仅此处需要
        from PySide6.QtGui import QDesktopServices

        directory = self._settings.recordings_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error("保存文件夹打开失败：{}（{}）", directory, exc)
            self._notifier.notify(
                APP_DISPLAY_NAME, MSG_SAVE_DIR_OPEN_FAILED.format(exc), key="save-dir-open"
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _request_probe(self) -> None:
        """向网络 worker 线程投递一次 health 探测（start/重试/轮询三处共用）。

        🔴 红线：主线程禁止直接调 ``probe()``，必须 QueuedConnection 投递。
        """
        if self._qthread is not None:
            QMetaObject.invokeMethod(self._worker, "probe", Qt.ConnectionType.QueuedConnection)

    def _on_loading_poll_tick(self) -> None:
        """LOADING 态轮询 tick：达上限停止并一次性通知，否则投递探测。"""
        self._loading_poll_count += 1
        if self._loading_poll_count > self._settings.loading_poll_max_count:
            self._loading_poll_timer.stop()
            self._loading_poll_count = 0
            logger.warning("服务端模型加载轮询达上限，停止轮询并提示手动重试")
            self._set_tray(TrayStatus.CONNECTING, "模型加载超时")
            self._notifier.notify(APP_DISPLAY_NAME, MSG_LOADING_TIMEOUT, key="loading-timeout")
            return
        self._request_probe()

    # ------------------------------------------------------------------ 状态机转移监听（主线程）

    def _on_transition(self, from_s: State, event: Event, to: State, payload) -> None:
        logger.debug("状态转移：{} --{}--> {}", from_s.value, event.value, to.value)
        if to is State.RECORDING:
            self._recorder.start()
            self._set_tray(TrayStatus.RECORDING)
        elif event in (Event.HOTKEY_RELEASE, Event.RECORD_MAX_REACHED):
            pcm = self._recorder.stop()
            # 先存 wav 后发识别：wav 是用户原始音频，识别失败时仍应保留；
            # 保存失败仅告警不阻断主流程（🔴 落盘不得影响识别-粘贴链路）
            self._current_wav_path = None
            if self._settings.save_recordings:
                try:
                    self._current_wav_path = self._store.save_wav(pcm)
                    logger.info("录音已保存：{}", self._current_wav_path)
                except OSError as exc:
                    logger.error("录音保存失败：{}", exc)
                    self._notifier.notify(
                        APP_DISPLAY_NAME, MSG_SAVE_WAV_FAILED.format(exc), key="save-wav"
                    )
            self._set_tray(TrayStatus.TRANSCRIBING)
            self.sig_recognize_request.emit(pcm)
        elif to is State.COMPLETED:
            self._handle_output(payload)
        elif to is State.ERROR:
            # 识别/输出失败：wav 保留、不写 txt，仅清空关联（txt 缺失即
            # 识别失败的可观测信号）
            self._current_wav_path = None
            self._notifier.notify(APP_DISPLAY_NAME, str(payload), key="transient-error")
            self._sm.fire(Event.ERROR_DONE)
        elif to is State.IDLE:
            self._set_tray(self._service_tray_status)

    def _handle_output(self, payload) -> None:
        text = (payload or {}).get("text", "")
        # 识别成功即落盘 txt（与 wav 同基名）；保存失败仅告警，不回滚已有 wav
        if self._current_wav_path is not None and self._settings.save_recordings:
            try:
                txt_path = self._store.save_txt(self._current_wav_path, text)
                logger.info("识别文本已保存：{}", txt_path)
            except OSError as exc:
                logger.error("识别文本保存失败：{}", exc)
                self._notifier.notify(
                    APP_DISPLAY_NAME, MSG_SAVE_TXT_FAILED.format(exc), key="save-txt"
                )
        self._current_wav_path = None
        try:
            self._pipeline.output(text)
        except (ClipboardError, PasteError) as exc:
            logger.error("文字输出失败：{}", exc)
            self._sm.fire(Event.OUTPUT_FAILED, f"文字输出失败：{exc}")
            return
        logger.info("识别结果已输出：{!r}", text[:50])
        self._sm.fire(Event.OUTPUT_DONE)

    # ------------------------------------------------------------------ 网络 worker 信号（主线程）

    def _on_service_status(self, status: str, detail: str) -> None:
        mapping = {
            worker_mod.STATUS_DISCONNECTED: TrayStatus.DISCONNECTED,
            worker_mod.STATUS_LOADING: TrayStatus.CONNECTING,
            worker_mod.STATUS_READY: TrayStatus.READY,
            worker_mod.STATUS_ERROR: TrayStatus.ERROR,
        }
        self._service_tray_status = mapping.get(status, TrayStatus.DISCONNECTED)
        if status == worker_mod.STATUS_LOADING:
            # 仅在「新进入」LOADING 时复位计数并启动轮询；轮询探测返回的
            # LOADING 不复位——否则上限检测被每次响应清零而永不触发
            if not self._loading_poll_timer.isActive():
                self._loading_poll_count = 0
                self._loading_poll_timer.start()
        else:
            # 终态（READY/ERROR/DISCONNECTED）收敛：停止轮询
            self._loading_poll_timer.stop()
            self._loading_poll_count = 0
        if status == worker_mod.STATUS_DISCONNECTED and detail == "服务端未运行":
            # 持续状态走图标色；首次探针失败同时给一次瞬时提示（C1）
            self._notifier.notify(APP_DISPLAY_NAME, MSG_SERVER_ABSENT, key="server-absent")
        if self._sm.state is State.IDLE:
            self._set_tray(self._service_tray_status, detail)

    def _on_recognize_done(self, payload: dict) -> None:
        if self._sm.state is State.TRANSCRIBING:
            self._sm.fire(Event.TRANSCRIBE_DONE, payload)

    def _on_recognize_failed(self, code: int, message: str) -> None:
        if self._sm.state is State.TRANSCRIBING:
            self._sm.fire(Event.TRANSCRIBE_FAILED, failure_message(code, message))
        else:
            self._notifier.notify(APP_DISPLAY_NAME, failure_message(code, message),
                                  key="transient-error")

    def _on_version_mismatch(self, detail: str) -> None:
        self._service_tray_status = TrayStatus.ERROR
        self._set_tray(TrayStatus.ERROR, "协议版本不兼容")
        self._notifier.notify(APP_DISPLAY_NAME, MSG_VERSION_MISMATCH.format(detail),
                              key="version-mismatch")
        if self._sm.state is State.TRANSCRIBING:
            self._sm.fire(Event.TRANSCRIBE_FAILED, MSG_VERSION_MISMATCH.format(detail))

    # ------------------------------------------------------------------ 工具

    def _set_tray(self, status: TrayStatus, detail: str = "") -> None:
        if self._tray is not None:
            self._tray.set_status(status, detail)

    # ------------------------------------------------------------------ 测试钩子（等价热键事件注入）
    def inject_press(self) -> None:
        self.sig_hotkey_press.emit()

    def inject_release(self) -> None:
        self.sig_hotkey_release.emit()

    @property
    def state(self) -> State:
        return self._sm.state
