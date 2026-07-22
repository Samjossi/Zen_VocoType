"""服务端托盘（PySide6 QSystemTrayIcon）。

菜单结构（自上而下，共 6 类 9 项，计划 §4.2(4) 定稿）：

- ① 版本项（禁用态展示，🔴 必须首行）：``Zen_VocoType_Service v<版本>`` +
  ``版本: <版本>（开发版/打包版）``——与客户端及旧 GridChat 托盘明确区分
- ② 状态行（禁用态展示）：加载中… / 就绪 / 切换中… / 错误（原因）
- ③ 当前模型行（禁用态展示）
- ④ 切换模型 ► 子菜单：注册表逐键列出，当前模型前缀 ✓
- ⑤ 打开日志目录
- ⑥ 退出服务（与 SIGTERM 汇流同一退出序列）

状态色（与客户端选型九同配色）：橙=加载中/切换中、绿=就绪、红=错误，
以基础图标右下角叠加色点实现（不重绘整套图标资产）。

状态同步用 QTimer 轮询 ``ServiceContext``（``state`` 读写持锁、
``worker.switching`` 为 ``threading.Event``，天然线程安全），
🔴 不在 state/worker 核心对象引入 Qt 依赖。
"""

from __future__ import annotations

import enum
import threading

from loguru import logger
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..context import ServiceContext
from ..state import STATUS_ERROR, STATUS_READY
from ..version import SERVICE_VERSION
from .icon_loader import load_tray_icon

#: 应用展示名（版本项与 tooltip 共用，单一出处）
APP_DISPLAY_NAME = "Zen_VocoType_Service"

#: 错误详情在菜单状态行中的最大展示长度（超出截断）
_ERROR_DETAIL_MAX_LEN = 40


class TrayStatus(enum.Enum):
    """托盘持续状态（持续状态走图标色 + 菜单状态行）。"""

    LOADING = ("加载中…", QColor(0xE6, 0xA2, 0x3C))  # 橙（starting / 切换中）
    READY = ("就绪", QColor(0x3C, 0xA5, 0x55))  # 绿
    ERROR = ("错误", QColor(0xCC, 0x33, 0x33))  # 红

    def __init__(self, label: str, color: QColor) -> None:
        self.label = label
        self.color = color


#: 状态色点直径占图标边长比例（右下角叠加）
_STATUS_DOT_RATIO = 0.35


def status_icon(base: QIcon, status: TrayStatus, size: int = 64) -> QIcon:
    """在基础图标右下角叠加状态色点，返回新图标。"""
    pixmap = base.pixmap(size, size)
    if pixmap.isNull():  # 基础图标缺失（降级）：以纯色底生成，保证色点仍可见
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = int(size * _STATUS_DOT_RATIO)
    margin = max(1, int(size * 0.04))
    painter.setBrush(status.color)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(size - d - margin, size - d - margin, d, d)
    painter.end()
    return QIcon(pixmap)


def _truncate(text: str, max_len: int = _ERROR_DETAIL_MAX_LEN) -> str:
    """截断过长文本（菜单状态行展示用）。"""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


class ServiceTray(QObject):
    """服务端托盘封装：菜单 + 状态色轮询 + 模型切换 + 退出汇流。"""

    #: 用户点击「退出服务」（装配层接 shutdown_event.set，与 SIGTERM 汇流）
    quit_requested = Signal()

    def __init__(
        self,
        ctx: ServiceContext,
        poll_interval_ms: int = 500,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._base_icon: QIcon = load_tray_icon()

        self._tray = QSystemTrayIcon(self)
        self._menu = QMenu()

        # ① 版本项（禁用态展示，🔴 必须首行）
        self._title_action = self._menu.addAction(
            f"{APP_DISPLAY_NAME} v{SERVICE_VERSION}"
        )
        self._title_action.setEnabled(False)
        self._menu.addSeparator()

        # ② 状态行 / ③ 当前模型行（禁用态展示，轮询刷新）
        self._status_action = self._menu.addAction("")
        self._status_action.setEnabled(False)
        self._model_action = self._menu.addAction("")
        self._model_action.setEnabled(False)
        self._menu.addSeparator()

        # ④ 切换模型子菜单（注册表逐键列出，当前模型前缀 ✓）
        self._switch_menu = self._menu.addMenu("切换模型")
        # 🔴 持有子菜单父动作包装：PySide6 在 setContextMenu 后，该动作的临时
        # 包装一旦被 GC 会连带销毁 C++ 子菜单（已最小复现验证）；持久持有可防
        self._switch_action = self._switch_menu.menuAction()
        self._model_actions = {}
        for name in sorted(ctx.settings.models):
            action = self._switch_menu.addAction(name)
            action.triggered.connect(
                lambda checked=False, n=name: self._on_switch_model(n)
            )
            self._model_actions[name] = action
        self._menu.addSeparator()

        # ⑤ 打开日志目录 / ⑥ 退出服务
        log_action = self._menu.addAction("打开日志目录")
        log_action.triggered.connect(self._open_log_dir)
        quit_action = self._menu.addAction("退出服务")
        quit_action.triggered.connect(self.quit_requested)

        self._tray.setContextMenu(self._menu)

        # 状态图标缓存（状态仅 3 种，构造期预生成；避免稳态 2Hz 重绘 + SNI 广播）
        self._status_icons = {s: status_icon(self._base_icon, s) for s in TrayStatus}
        #: 上次应用的快照（status, model, detail, switching）；None 表示尚未应用
        self._last_snapshot: tuple | None = None

        # 状态轮询（Qt 主线程执行；读取源全部线程安全）
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_interval_ms)
        self._poll_timer.timeout.connect(self._refresh)
        self._poll_timer.start()
        self._refresh()

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def show(self) -> None:
        self._tray.show()

    # ------------------------------------------------------------------
    # 状态轮询刷新
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """轮询 ServiceContext 快照，刷新图标色 + 状态行 + 模型行 + 子菜单。

        快照未变化时直接返回（稳态零绘制、零 SNI NewIcon 广播）。
        """
        state = self._ctx.state
        worker = self._ctx.worker
        status = state.status
        model = state.current_model
        detail = state.error_detail or ""
        switching = worker is not None and worker.switching

        snapshot = (status, model, detail, switching)
        if snapshot == self._last_snapshot:
            return
        self._last_snapshot = snapshot

        if status == STATUS_ERROR:
            tray_status = TrayStatus.ERROR
            label = TrayStatus.ERROR.label
            status_text = f"状态：{label}" + (f"（{_truncate(detail)}）" if detail else "")
        elif switching:
            tray_status = TrayStatus.LOADING
            label = "切换中…"
            status_text = f"状态：{label}"
        elif status == STATUS_READY:
            tray_status = TrayStatus.READY
            label = TrayStatus.READY.label
            status_text = f"状态：{label}"
        else:  # starting
            tray_status = TrayStatus.LOADING
            label = TrayStatus.LOADING.label
            status_text = f"状态：{label}"

        self._tray.setIcon(self._status_icons[tray_status])
        self._status_action.setText(status_text)
        self._model_action.setText(f"当前模型：{model}" if model else "当前模型：—")
        self._tray.setToolTip(f"{APP_DISPLAY_NAME} — {label}")

        # 切换模型可用性：就绪 + worker 就位 + 非切换中 + 注册表多于 1 个模型
        can_switch = (
            status == STATUS_READY
            and worker is not None
            and not switching
            and len(self._model_actions) > 1
        )
        self._switch_menu.setEnabled(can_switch)
        for name, action in self._model_actions.items():
            action.setText(f"✓ {name}" if name == model else name)

    # ------------------------------------------------------------------
    # 菜单动作
    # ------------------------------------------------------------------

    def _on_switch_model(self, model_name: str) -> None:
        """切换模型：临时线程调 worker.submit_switch。

        🔴 禁止在 Qt 主线程直接调用——submit 阻塞等待结果（最长
        infer_timeout_s 秒），会冻结托盘；切换与推理在 worker 队列内
        天然互斥（选型四），失败回滚由 worker/manager 负责，此处只记日志。
        """
        worker = self._ctx.worker
        if worker is None:
            logger.warning("切换模型请求被忽略（worker 未就绪）：{}", model_name)
            return
        if worker.switching:
            # 🔴 轮询间隙（≤500ms）内双击或与 Socket 客户端并发切换的守卫：
            # 重复提交会让前序任务的 finally 提前清除后序任务的切换标记，
            # 导致切换期间 recognize 的 2002 拒绝守卫失效（worker.py 语义红线）
            logger.warning("切换模型请求被忽略（切换进行中）：{}", model_name)
            return

        def _do_switch() -> None:
            try:
                worker.submit_switch(model_name)
                logger.info("托盘触发模型切换完成：{}", model_name)
            except Exception as exc:
                logger.error("托盘触发模型切换失败（{}）：{}", model_name, exc)

        threading.Thread(
            target=_do_switch, name="tray-model-switch", daemon=True
        ).start()

    def _open_log_dir(self) -> None:
        """打开日志目录（失败记 warning，不崩溃）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        log_dir = self._ctx.settings.log_dir
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir))):
                logger.warning("打开日志目录失败（系统无可用 handler）：{}", log_dir)
        except Exception as exc:
            logger.warning("打开日志目录失败：{}（{}）", log_dir, exc)
