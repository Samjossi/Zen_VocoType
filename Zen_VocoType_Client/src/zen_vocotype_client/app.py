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

from loguru import logger
from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, QTimer

from .config import Settings
from .hotkey.combo import parse_hotkey
from .hotkey.pynput_backend import PynputBackend
from .output.clipboard import ClipboardError, create_clipboard
from .output.paster import PasteError, create_paster
from .output.restore import OutputPipeline
from .recorder.recorder import DeviceUnavailableError, Recorder
from .state_machine import Event, State, StateMachine
from .transcribe import worker as worker_mod
from .transcribe.worker import NetworkWorker
from .tray.notifier import Notifier
from .tray.tray import APP_DISPLAY_NAME, ClientTray, TrayStatus

#: 提示文案（T2.7 定稿三类 + 通用识别失败；README 故障排查节同步）
MSG_SERVER_ABSENT = "服务端未运行——请启动 Zen_VocoType 服务端后，从托盘菜单「重试连接服务端」"
MSG_VERSION_MISMATCH = "协议版本不兼容：{}——请更新客户端/服务端至匹配版本"
MSG_MODEL_SWITCHING = "模型切换中，请稍候片刻再试"
MSG_NOT_READY = "服务端正在加载模型，请稍候"
MSG_MAX_RECORD = "已达最大录音时长，自动进入识别"
MSG_LOADING_TIMEOUT = "服务端模型加载等待超时——请从托盘菜单「重试连接服务端」"


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

        # --- 热键 ---
        combo = parse_hotkey(settings.hotkey)
        self._hotkey = PynputBackend(
            combo,
            on_press=self.sig_hotkey_press.emit,  # 回调线程仅发信号
            on_release=self.sig_hotkey_release.emit,
        )

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
            self._set_tray(TrayStatus.TRANSCRIBING)
            self.sig_recognize_request.emit(pcm)
        elif to is State.COMPLETED:
            self._handle_output(payload)
        elif to is State.ERROR:
            self._notifier.notify(APP_DISPLAY_NAME, str(payload), key="transient-error")
            self._sm.fire(Event.ERROR_DONE)
        elif to is State.IDLE:
            self._set_tray(self._service_tray_status)

    def _handle_output(self, payload) -> None:
        text = (payload or {}).get("text", "")
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
