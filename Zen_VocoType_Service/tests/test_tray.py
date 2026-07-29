"""服务端托盘单元测试（计划 §4.3）。

菜单结构 / 状态映射 / 切换模型子菜单 / 色点叠加 / 退出汇流 /
icon_loader 降级 / main 无显示环境降级。

🔴 offscreen 平台必须在 PySide6 导入前设置（headless CI 兼容）。
"""

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from zen_vocotype_service.config import Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.state import ServiceState
from zen_vocotype_service.tray import icon_loader
from zen_vocotype_service.tray.icon_loader import load_tray_icon
from zen_vocotype_service.tray.tray import (
    APP_DISPLAY_NAME,
    ServiceTray,
    TrayStatus,
    status_icon,
)
from zen_vocotype_service.version import SERVICE_VERSION

#: 组件根（main.py 所在目录，降级测试 import main 用）
COMPONENT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def ctx():
    return ServiceContext(Settings(), ServiceState())


@pytest.fixture()
def tray(qapp, ctx):
    # 轮询间隔拉大：测试内手动 _refresh()，避免事件循环介入产生竞态
    return ServiceTray(ctx, poll_interval_ms=60_000)


def _menu_texts(tray: ServiceTray) -> list[str]:
    return [a.text() for a in tray.tray_icon.contextMenu().actions()]


class _FakeWorker:
    """submit_switch 记录调用与调用线程的假 worker。"""

    def __init__(self, switching: bool = False) -> None:
        self._switching = switching
        self.switched: list[str] = []
        self.call_thread_idents: list[int] = []

    @property
    def switching(self) -> bool:
        return self._switching

    def submit_switch(self, model_name: str) -> None:
        self.call_thread_idents.append(threading.get_ident())
        self.switched.append(model_name)


class TestTrayMenu:
    def test_version_actions_first_and_disabled(self, tray):
        """版本项必须首行且禁用（与客户端及旧 GridChat 托盘明确区分）。"""
        actions = tray.tray_icon.contextMenu().actions()
        assert actions[0].text() == f"{APP_DISPLAY_NAME} v{SERVICE_VERSION}"
        assert not actions[0].isEnabled()
        assert "GridChat" not in actions[0].text()

    def test_version_is_two_part_format(self):
        """🔴 版本号定版为两位数格式 major.minor（如 1.0/1.1），不再三段式。

        具体版本值的唯一真相为仓库根 versions.toml（一致性由
        test_version_consistency.py 比对），此处仅固化格式。
        """
        import re

        assert re.fullmatch(r"\d+\.\d+", SERVICE_VERSION), SERVICE_VERSION

    def test_menu_structure(self, tray):
        texts = _menu_texts(tray)
        assert any(t.startswith("状态：") for t in texts)
        assert any(t.startswith("当前模型：") for t in texts)
        assert "切换模型" in texts
        assert "打开日志目录" in texts
        assert "退出服务" in texts

    def test_switch_submenu_lists_registry_models(self, tray, ctx):
        # 🔴 直接用 tray._switch_menu 持有引用：经 contextMenu().actions()
        # 临时包装查到的子菜单父动作被 GC 时会连带销毁 C++ 子菜单
        # （PySide6 setContextMenu 所有权陷阱，已最小复现验证）
        names = {a.text() for a in tray._switch_menu.actions()}
        assert names == set(ctx.settings.models)


class TestStatusMapping:
    def test_starting_shows_loading(self, tray):
        tray._refresh()
        assert "状态：加载中…" in _menu_texts(tray)
        assert "当前模型：—" in _menu_texts(tray)
        assert "加载中" in tray.tray_icon.toolTip()

    def test_ready_shows_model_and_checkmark(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = _FakeWorker()
        tray._refresh()
        assert "状态：就绪" in _menu_texts(tray)
        assert "当前模型：fun-asr-nano" in _menu_texts(tray)
        assert "就绪" in tray.tray_icon.toolTip()
        submenu_texts = {a.text() for a in tray._switch_menu.actions()}
        assert "✓ fun-asr-nano" in submenu_texts
        assert "sensevoice-small" in submenu_texts

    def test_error_shows_truncated_detail(self, tray, ctx):
        detail = "x" * 100
        ctx.state.mark_error(detail)
        tray._refresh()
        status_text = next(t for t in _menu_texts(tray) if t.startswith("状态："))
        assert status_text.startswith("状态：错误（")
        assert len(status_text) < len(detail)  # 已截断
        assert "错误" in tray.tray_icon.toolTip()

    def test_switching_shows_switching_label(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = _FakeWorker(switching=True)
        tray._refresh()
        assert "状态：切换中…" in _menu_texts(tray)


class TestSwitchAvailability:
    def _switch_menu(self, tray):
        return tray._switch_menu

    def test_disabled_when_worker_none(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = None
        tray._refresh()
        assert not self._switch_menu(tray).isEnabled()

    def test_disabled_when_not_ready(self, tray, ctx):
        ctx.worker = _FakeWorker()
        tray._refresh()  # starting
        assert not self._switch_menu(tray).isEnabled()

    def test_disabled_when_switching(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = _FakeWorker(switching=True)
        tray._refresh()
        assert not self._switch_menu(tray).isEnabled()

    def test_enabled_when_ready(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = _FakeWorker()
        tray._refresh()
        assert self._switch_menu(tray).isEnabled()

    def test_click_triggers_submit_switch_off_main_thread(self, tray, ctx):
        """点击子菜单项 → submit_switch 被调用且在非 Qt 主线程执行。"""
        ctx.state.mark_ready("fun-asr-nano")
        worker = _FakeWorker()
        ctx.worker = worker
        tray._refresh()
        main_thread_ident = threading.get_ident()
        action = next(
            a for a in self._switch_menu(tray).actions() if a.text() == "sensevoice-small"
        )
        action.trigger()
        deadline = time.monotonic() + 5
        while not worker.switched and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.switched == ["sensevoice-small"]
        # 🔴 必须在非 Qt 主线程执行（主线程同步调用会冻结托盘最长 60s）
        assert worker.call_thread_idents[0] != main_thread_ident

    def test_click_ignored_while_switching(self, tray, ctx):
        """切换进行中点击被守卫拦截，不重复提交（竞态窗口修复固化）。"""
        ctx.state.mark_ready("fun-asr-nano")
        worker = _FakeWorker(switching=True)
        ctx.worker = worker
        tray._on_switch_model("sensevoice-small")
        assert worker.switched == []


class TestDownloadNotice:
    """下载提醒（模型缺失与下载提醒计划 T5/T6）。"""

    def test_downloading_shows_label_and_model(self, tray, ctx):
        ctx.state.mark_ready("fun-asr-nano")
        ctx.state.mark_downloading("qwen3-asr-1.7b")
        tray._refresh()
        assert "状态：下载中…（qwen3-asr-1.7b）" in _menu_texts(tray)
        assert "下载中" in tray.tray_icon.toolTip()

    def test_downloading_takes_priority_over_switching(self, tray, ctx):
        """切换中收到下载标记：状态行呈现下载中（下载是切换的子阶段）。"""
        ctx.state.mark_ready("fun-asr-nano")
        ctx.worker = _FakeWorker(switching=True)
        ctx.state.mark_downloading("sensevoice-small")
        tray._refresh()
        assert "状态：下载中…（sensevoice-small）" in _menu_texts(tray)

    def test_downloading_uses_orange_loading_icon(self, tray, ctx):
        """下载中沿用橙色 LOADING 图标（不新增颜色语义）。"""
        ctx.state.mark_ready("fun-asr-nano")
        ctx.state.mark_downloading("qwen3-asr-1.7b")
        tray._refresh()
        expected = tray._status_icons[TrayStatus.LOADING].pixmap(64, 64).toImage()
        actual = tray.tray_icon.icon().pixmap(64, 64).toImage()
        assert bytes(actual.constBits()) == bytes(expected.constBits())

    def test_balloon_fires_once_per_download(self, tray, ctx, monkeypatch):
        """空→非空跳变只弹一次气泡；快照不变/下载持续期间不重复弹。"""
        balloons: list[tuple[str, str]] = []
        monkeypatch.setattr(
            tray, "_show_balloon", lambda t, m: balloons.append((t, m))
        )
        tray._messages_supported = True
        ctx.state.mark_downloading("qwen3-asr-1.7b")
        tray._refresh()
        tray._refresh()
        tray._refresh()
        assert len(balloons) == 1
        title, message = balloons[0]
        assert title == "模型下载"
        assert "qwen3-asr-1.7b" in message and "尚未缓存" in message

    def test_balloon_fires_again_for_next_download(self, tray, ctx, monkeypatch):
        """下载结束后再次下载（换模型）应再次提醒。"""
        balloons: list[tuple[str, str]] = []
        monkeypatch.setattr(
            tray, "_show_balloon", lambda t, m: balloons.append((t, m))
        )
        tray._messages_supported = True
        ctx.state.mark_downloading("m1")
        tray._refresh()
        ctx.state.clear_downloading()
        tray._refresh()
        ctx.state.mark_downloading("m2")
        tray._refresh()
        assert len(balloons) == 2
        assert "m1" in balloons[0][1] and "m2" in balloons[1][1]

    def test_no_balloon_when_messages_unsupported(self, tray, ctx, monkeypatch):
        """supportsMessages=False：降级仅状态行，不触发 Qt 气泡调用。"""
        called: list[tuple[str, str]] = []
        monkeypatch.setattr(
            tray, "_show_balloon", lambda t, m: called.append((t, m))
        )
        tray._messages_supported = False
        ctx.state.mark_downloading("qwen3-asr-1.7b")
        tray._refresh()
        assert called == []
        assert "状态：下载中…（qwen3-asr-1.7b）" in _menu_texts(tray)

    def test_download_finished_no_completion_balloon(self, tray, ctx, monkeypatch):
        """下载完成（标记清除）不弹完成气泡（决策裁定 2）。"""
        balloons: list[tuple[str, str]] = []
        monkeypatch.setattr(
            tray, "_show_balloon", lambda t, m: balloons.append((t, m))
        )
        tray._messages_supported = True
        ctx.state.mark_downloading("m1")
        tray._refresh()
        ctx.state.clear_downloading()
        ctx.state.mark_ready("m1")
        tray._refresh()
        assert len(balloons) == 1  # 仅开始下载那一次


class TestStatusIcon:
    def test_all_statuses_produce_nonnull_icons(self):
        base = load_tray_icon()
        for status in TrayStatus:
            assert not status_icon(base, status).isNull(), status

    def test_statuses_produce_distinct_pixmaps(self):
        base = load_tray_icon()
        images = {
            s: status_icon(base, s).pixmap(64, 64).toImage() for s in TrayStatus
        }
        distinct = {bytes(img.constBits()) for img in images.values()}
        assert len(distinct) == len(TrayStatus)

    def test_null_base_icon_still_shows_dot(self):
        """基础图标缺失（降级）时状态色点仍可见。"""
        from PySide6.QtGui import QIcon

        assert not status_icon(QIcon(), TrayStatus.ERROR).isNull()


class TestQuitConvergence:
    def test_quit_action_sets_shutdown_event(self, tray):
        """「退出服务」与 SIGTERM 汇流：触发后 shutdown_event 置位。"""
        shutdown_event = threading.Event()
        tray.quit_requested.connect(shutdown_event.set)
        quit_action = next(
            a for a in tray.tray_icon.contextMenu().actions() if a.text() == "退出服务"
        )
        quit_action.trigger()
        assert shutdown_event.is_set()


class TestIconLoader:
    def test_missing_icons_return_empty_qicon_without_crash(self, monkeypatch, tmp_path):
        monkeypatch.setattr(icon_loader, "assets_dir", lambda: tmp_path)
        icon = load_tray_icon()
        assert icon.isNull()  # 空 QIcon 降级，不崩溃

    def test_real_assets_load(self):
        """复制自 GridChat_Service 的四档图标全部可加载。"""
        icon = load_tray_icon()
        assert not icon.isNull()


class TestMainDegradation:
    @pytest.fixture()
    def main_module(self):
        if str(COMPONENT_ROOT) not in sys.path:
            sys.path.insert(0, str(COMPONENT_ROOT))
        import main

        return main

    def test_headless_returns_none(self, main_module, monkeypatch, ctx):
        """无 DISPLAY/WAYLAND_DISPLAY → 降级纯控制台，不触碰 Qt。"""
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        app, tray = main_module._create_tray_if_available(
            ctx.settings, ctx, threading.Event()
        )
        assert app is None and tray is None

    def test_tray_disabled_by_config_returns_none(self, main_module, ctx):
        """tray_enabled=false → 纯控制台模式。"""
        ctx.settings.tray_enabled = False
        app, tray = main_module._create_tray_if_available(
            ctx.settings, ctx, threading.Event()
        )
        assert app is None and tray is None
