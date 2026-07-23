"""T35 单元测试：托盘「剪贴板恢复延迟」设置项（热切换 + 持久化）。"""

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog

import zen_vocotype_client.app as app_mod
from zen_vocotype_client.app import (
    ClientApp,
    MSG_RESTORE_DELAY_ENV_OVERRIDE,
    MSG_RESTORE_DELAY_INVALID,
    MSG_RESTORE_DELAY_PERSIST_FAILED,
    MSG_RESTORE_DELAY_UPDATED,
)
from zen_vocotype_client.config import RESTORE_DELAY_ENV_VAR, Settings
from zen_vocotype_client.output.clipboard import ClipboardBackend
from zen_vocotype_client.output.paster import PasterBackend
from zen_vocotype_client.output.restore import OutputPipeline
from zen_vocotype_client.state_machine import State
from zen_vocotype_client.tray.tray import ClientTray


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------- OutputPipeline setter


class _FakeClipboard(ClipboardBackend):
    def __init__(self, initial: str = "") -> None:
        self.content = initial

    def read_text(self) -> str:
        return self.content

    def write_text(self, text: str) -> None:
        self.content = text


class _FakePaster(PasterBackend):
    def paste(self) -> None:
        pass


class _RecordingScheduler:
    """调度替身：记录延迟、不执行回调（只观察调度参数）。"""

    def __init__(self) -> None:
        self.delays: list[int] = []

    def __call__(self, delay_ms, callback) -> None:
        self.delays.append(delay_ms)


class TestSetRestoreDelay:
    def test_update_affects_next_output(self):
        """运行态更新后，下一次 output() 按新延迟调度恢复。"""
        scheduler = _RecordingScheduler()
        pipe = OutputPipeline(
            _FakeClipboard(), _FakePaster(),
            restore_delay_ms=200, scheduler=scheduler,
        )
        pipe.output("第一次")
        pipe.set_restore_delay_ms(450)
        pipe.output("第二次")
        assert scheduler.delays == [200, 450]

    def test_zero_accepted(self):
        """0ms（立即恢复）为字段 ge=0 约束内的合法值。"""
        pipe = OutputPipeline(
            _FakeClipboard(), _FakePaster(),
            restore_delay_ms=200, scheduler=_RecordingScheduler(),
        )
        pipe.set_restore_delay_ms(0)

    def test_negative_rejected(self):
        pipe = OutputPipeline(
            _FakeClipboard(), _FakePaster(),
            restore_delay_ms=200, scheduler=_RecordingScheduler(),
        )
        with pytest.raises(ValueError, match="恢复延迟非法"):
            pipe.set_restore_delay_ms(-1)


# ---------------------------------------------------------------------- 托盘菜单结构


class TestTrayMenu:
    def test_item_position(self, qapp):
        """新项位于「修改快捷键…」之后、「保存录音」之前。"""
        tray = ClientTray()
        texts = [a.text() for a in tray._menu.actions()]
        assert texts.index("修改快捷键…") < texts.index("剪贴板恢复延迟（—）…")
        assert texts.index("剪贴板恢复延迟（—）…") < texts.index("保存录音")

    def test_set_restore_delay_label(self, qapp):
        tray = ClientTray()
        tray.set_restore_delay_label(300)
        assert tray._restore_delay_action.text() == "剪贴板恢复延迟（300ms）…"

    def test_action_emits_signal(self, qapp):
        received: list[bool] = []
        tray = ClientTray()
        tray.restore_delay_change_requested.connect(lambda: received.append(True))
        tray._restore_delay_action.trigger()
        assert received == [True]


# ---------------------------------------------------------------------- 装配层热切换


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, key: str | None = None) -> bool:
        self.messages.append((title, message))
        return True


class _FakeTray:
    def __init__(self) -> None:
        self.delay_labels: list[int] = []

    def set_restore_delay_label(self, ms: int) -> None:
        self.delay_labels.append(ms)


class _FakePipeline:
    """输出管道替身：仅记录延迟 setter 入参（校验红线由真实 OutputPipeline 用例覆盖）。"""

    def __init__(self) -> None:
        self.delay: int | None = None

    def set_restore_delay_ms(self, ms: int) -> None:
        self.delay = ms


def _make_client(tmp_path, monkeypatch) -> ClientApp:
    """构造未启动的 ClientApp：替身通知器/托盘/输出管道。

    用户配置路径经 monkeypatch 冻结常量 ``paths.DEFAULT_USER_CONFIG_PATH``
    真正隔离到 tmp（🔴 monkeypatch XDG_CONFIG_HOME 环境变量无效——该常量
    在 paths 模块导入期已冻结）。
    """
    monkeypatch.setattr(
        "zen_vocotype_protocol.paths.DEFAULT_USER_CONFIG_PATH",
        tmp_path / "zen_vocotype" / "user_config.yaml",
    )
    client = ClientApp(Settings(socket_path="/nonexistent/zen_t35.sock"))
    client._notifier = _FakeNotifier()
    client._tray = _FakeTray()
    client._pipeline = _FakePipeline()
    return client


class TestApplyRestoreDelay:
    def test_success_path(self, qapp, tmp_path, monkeypatch):
        """成功路径：落盘 int 原样 → pipeline 切换 → 内存同步 → 托盘刷新 → 通知。"""
        client = _make_client(tmp_path, monkeypatch)
        persisted: list[tuple[str, object]] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        client._apply_restore_delay(300)
        assert persisted == [("paste_restore_delay_ms", 300)]
        assert client._pipeline.delay == 300
        assert client._settings.paste_restore_delay_ms == 300
        assert client._tray.delay_labels == [300]
        assert any(MSG_RESTORE_DELAY_UPDATED.format(300) in m
                   for _, m in client._notifier.messages)

    def test_persist_failure_not_applied(self, qapp, tmp_path, monkeypatch):
        """落盘失败 → 失败通知，pipeline/settings/托盘全部不变（🔴 先落盘后切换）。"""
        client = _make_client(tmp_path, monkeypatch)

        def _boom(key, value):
            raise OSError("磁盘只读")

        monkeypatch.setattr(app_mod, "set_user_config_value", _boom)
        before = client._settings.paste_restore_delay_ms
        client._apply_restore_delay(300)
        assert client._pipeline.delay is None  # 未触达运行态切换
        assert client._settings.paste_restore_delay_ms == before
        assert client._tray.delay_labels == []
        assert any(MSG_RESTORE_DELAY_PERSIST_FAILED.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_negative_rejected_before_persist(self, qapp, tmp_path, monkeypatch):
        """负值兜底拦截：非法通知，落盘从未被调用。"""
        client = _make_client(tmp_path, monkeypatch)
        persisted: list = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        client._apply_restore_delay(-1)
        assert persisted == []
        assert client._pipeline.delay is None
        assert any(MSG_RESTORE_DELAY_INVALID.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_env_var_warning_appended(self, qapp, tmp_path, monkeypatch):
        """环境变量优先级高于用户配置文件：成功通知如实追加提醒，不阻断。"""
        client = _make_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda key, value: None
        )
        monkeypatch.setenv(RESTORE_DELAY_ENV_VAR, "500")
        client._apply_restore_delay(300)
        assert any(
            MSG_RESTORE_DELAY_ENV_OVERRIDE in m
            for _, m in client._notifier.messages
        )
        assert client._settings.paste_restore_delay_ms == 300  # 运行态仍生效

    def test_allowed_while_recording(self, qapp, tmp_path, monkeypatch):
        """忙碌中可改（🔴 无守卫决策固化）：RECORDING 态触发照常生效。"""
        client = _make_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda key, value: None
        )
        client._sm._state = State.RECORDING  # 测试直接置位
        client._apply_restore_delay(300)
        assert client._settings.paste_restore_delay_ms == 300
        assert client._pipeline.delay == 300

    def test_trayless_degradation(self, qapp, tmp_path, monkeypatch):
        """无托盘降级模式（C4）：_tray=None 时切换照常生效且不抛 AttributeError。"""
        client = _make_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda key, value: None
        )
        client._tray = None
        client._apply_restore_delay(300)
        assert client._settings.paste_restore_delay_ms == 300
        assert client._pipeline.delay == 300


class TestEntryDialog:
    def test_cancel_is_noop(self, qapp, tmp_path, monkeypatch):
        """对话框取消 → _apply_restore_delay 未被调用。"""
        client = _make_client(tmp_path, monkeypatch)
        monkeypatch.setattr(
            QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False))
        )
        called: list[int] = []
        monkeypatch.setattr(client, "_apply_restore_delay", called.append)
        client._on_change_restore_delay()
        assert called == []

    def test_accept_applies_value(self, qapp, tmp_path, monkeypatch):
        """对话框确定 → 全链路生效（预填值为当前设置）。"""
        client = _make_client(tmp_path, monkeypatch)
        seen_prefill: list[int] = []

        def _fake_get_int(parent, title, label, value, minimum, maximum, step):
            seen_prefill.append(value)
            return 450, True

        monkeypatch.setattr(
            QInputDialog, "getInt", staticmethod(_fake_get_int)
        )
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda key, value: None
        )
        client._on_change_restore_delay()
        assert seen_prefill == [200]  # Settings 默认值
        assert client._settings.paste_restore_delay_ms == 450
        assert client._pipeline.delay == 450

    def test_out_of_range_current_not_clamped(self, qapp, tmp_path, monkeypatch):
        """当前值 >10000（手改 YAML 合法持有）→ 上限随当前值抬升，预填不被钳制。

        防「确认即篡改」：上限若硬编码 10000，QSpinBox 会静默钳制预填值，
        用户直接「确定」即把 20000 改写为 10000 并落盘（评审修复固化）。
        """
        client = _make_client(tmp_path, monkeypatch)
        client._settings.paste_restore_delay_ms = 20000
        seen: list[tuple[int, int]] = []

        def _fake_get_int(parent, title, label, value, minimum, maximum, step):
            seen.append((value, maximum))
            return value, True  # 用户直接「确定」：预填原样返回

        monkeypatch.setattr(
            QInputDialog, "getInt", staticmethod(_fake_get_int)
        )
        monkeypatch.setattr(
            app_mod, "set_user_config_value", lambda key, value: None
        )
        client._on_change_restore_delay()
        assert seen == [(20000, 20000)]  # 上限抬升至当前值，预填未被钳制
        assert client._settings.paste_restore_delay_ms == 20000  # 原值不被篡改
