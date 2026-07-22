"""T34 单元测试：录音 WAV / 识别文本 TXT 落盘与托盘菜单。"""

import wave
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import zen_vocotype_client.app as app_mod
from zen_vocotype_client.app import (
    ClientApp,
    MSG_SAVE_DIR_BUSY,
    MSG_SAVE_DIR_INVALID,
    MSG_SAVE_DIR_PERSIST_FAILED,
    MSG_SAVE_TOGGLE_PERSIST_FAILED,
)
from zen_vocotype_client.config import Settings, validate_startup
from zen_vocotype_client.state_machine import Event, State
from zen_vocotype_client.storage import RecordingStore
from zen_vocotype_client.storage import recording_store as store_mod
from zen_vocotype_client.tray.tray import ClientTray

_PCM = b"\x01\x02" * 1600  # 0.1s 的 16k/16bit 假 PCM


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_store(tmp_path: Path) -> RecordingStore:
    return RecordingStore(tmp_path / "recordings", sample_rate=16000, sample_width=2, channels=1)


# ---------------------------------------------------------------------- WAV 写出


class TestSaveWav:
    def test_wav_header_params(self, tmp_path):
        """写出的 WAV 头与注入参数一致（16k/16bit/单声道），帧数正确。"""
        store = _make_store(tmp_path)
        path = store.save_wav(_PCM)
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getframerate() == 16000
            assert wav_file.getsampwidth() == 2
            assert wav_file.getnchannels() == 1
            assert wav_file.getnframes() == len(_PCM) // 2
            assert wav_file.readframes(10**6) == _PCM

    def test_filename_timestamp_format(self, tmp_path):
        store = _make_store(tmp_path)
        path = store.save_wav(_PCM)
        stem = path.stem
        datetime.strptime(stem, "%Y%m%d_%H%M%S")  # 非法格式抛 ValueError
        assert path.suffix == ".wav"

    def test_same_second_conflict_appends_seq(self, tmp_path, monkeypatch):
        """同秒多次保存追加 _2/_3 序号兜底（不覆盖既有文件）。"""

        class _FixedDatetime:
            @staticmethod
            def now():
                return datetime(2026, 7, 22, 20, 0, 0)

        monkeypatch.setattr(store_mod, "datetime", _FixedDatetime)
        store = _make_store(tmp_path)
        first = store.save_wav(_PCM)
        second = store.save_wav(_PCM)
        third = store.save_wav(_PCM)
        assert first.name == "20260722_200000.wav"
        assert second.name == "20260722_200000_2.wav"
        assert third.name == "20260722_200000_3.wav"

    def test_directory_auto_created(self, tmp_path):
        store = _make_store(tmp_path)
        assert not store.directory.exists()
        store.save_wav(_PCM)
        assert store.directory.is_dir()

    def test_empty_pcm_saved(self, tmp_path):
        """空 PCM（极短录音）仍如实落盘，不做时长门限。"""
        store = _make_store(tmp_path)
        path = store.save_wav(b"")
        assert path.exists()
        with wave.open(str(path), "rb") as wav_file:
            assert wav_file.getnframes() == 0

    def test_unwritable_directory_raises(self, tmp_path):
        """目录不可创建（父路径为文件）时抛 OSError 由调用方转通知。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        store = RecordingStore(blocker / "sub", sample_rate=16000, sample_width=2, channels=1)
        with pytest.raises(OSError):
            store.save_wav(_PCM)


# ---------------------------------------------------------------------- TXT 写出


class TestSaveTxt:
    def test_same_basename_same_dir(self, tmp_path):
        store = _make_store(tmp_path)
        wav_path = store.save_wav(_PCM)
        txt_path = store.save_txt(wav_path, "你好，世界")
        assert txt_path == wav_path.with_suffix(".txt")
        assert txt_path.read_text(encoding="utf-8") == "你好，世界"  # utf-8 中文往返一致


# ---------------------------------------------------------------------- Store 配置


class TestStoreDirectory:
    def test_set_directory_takes_effect(self, tmp_path):
        store = _make_store(tmp_path)
        new_dir = tmp_path / "elsewhere"
        store.set_directory(new_dir)
        path = store.save_wav(_PCM)
        assert path.parent == new_dir
        assert store.directory == new_dir


# ---------------------------------------------------------------------- Settings


class TestSettings:
    def test_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        settings = Settings()
        assert settings.save_recordings is True
        assert settings.recordings_dir == tmp_path / "zen_vocotype" / "recordings"

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ZEN_VOCOTYPE_CLIENT_SAVE_RECORDINGS", "false")
        monkeypatch.setenv("ZEN_VOCOTYPE_CLIENT_RECORDINGS_DIR", str(tmp_path / "custom"))
        settings = Settings()
        assert settings.save_recordings is False
        assert settings.recordings_dir == tmp_path / "custom"

    def test_relative_path_rejected(self):
        """🔴 路径类配置必须绝对路径（对齐 config.yaml 红线）。"""
        with pytest.raises(ValueError, match="绝对路径"):
            Settings(recordings_dir="relative/recordings")

    def test_validate_startup_probes_writable(self, tmp_path):
        """保存开启且目录不可创建时 validate_startup 显式失败（退出码 2 通道）。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        settings = Settings(recordings_dir=blocker / "sub")
        with pytest.raises(ValueError, match="不可创建或不可写"):
            validate_startup(settings)

    def test_validate_startup_creates_default_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        settings = Settings()
        validate_startup(settings)
        assert settings.recordings_dir.is_dir()

    def test_validate_startup_skips_probe_when_disabled(self, tmp_path):
        """保存关闭时不探测（用户有意关闭，不可写目录不构成启动失败）。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        settings = Settings(save_recordings=False, recordings_dir=blocker / "sub")
        validate_startup(settings)  # 不抛异常


# ---------------------------------------------------------------------- 托盘菜单


class TestTrayMenuSave:
    def test_menu_structure(self, qapp):
        tray = ClientTray()
        texts = [a.text() for a in tray.tray_icon.contextMenu().actions()]
        assert "保存录音" in texts
        assert "选择保存路径…" in texts
        assert "打开保存文件夹" in texts
        assert tray._save_action.isCheckable()
        # 三项位于「修改快捷键…」之后、「重试连接服务端」之前（计划 §6 确认点 2）
        assert texts.index("修改快捷键…") < texts.index("保存录音")
        assert texts.index("打开保存文件夹") < texts.index("重试连接服务端")

    def test_three_signals(self, qapp):
        from PySide6.QtTest import QSignalSpy

        tray = ClientTray()
        toggle_spy = QSignalSpy(tray.save_toggled)
        choose_spy = QSignalSpy(tray.choose_dir_requested)
        open_spy = QSignalSpy(tray.open_dir_requested)
        tray._save_action.setChecked(True)  # 用户勾选 → toggled(bool)
        tray._choose_dir_action.trigger()
        tray._open_dir_action.trigger()
        assert toggle_spy.count() == 1 and toggle_spy.at(0) == [True]
        assert choose_spy.count() == 1
        assert open_spy.count() == 1

    def test_set_save_checked_blocks_signal(self, qapp):
        """程序化设置勾选态（初始化/回滚）不得触发 save_toggled 持久化回路。"""
        from PySide6.QtTest import QSignalSpy

        tray = ClientTray()
        spy = QSignalSpy(tray.save_toggled)
        tray.set_save_checked(True)
        assert tray._save_action.isChecked()
        assert spy.count() == 0


# ---------------------------------------------------------------------- 装配层故障隔离与槽函数


class _RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str, key: str | None = None) -> bool:
        self.messages.append((title, message))
        return True


class _FakeTray:
    def __init__(self) -> None:
        self.checked: list[bool] = []
        self.statuses: list = []

    def set_save_checked(self, checked: bool) -> None:
        self.checked.append(checked)

    def set_status(self, status, detail: str = "") -> None:
        self.statuses.append((status, detail))


class _FakePipeline:
    """输出管道替身：仅记录文本，不触碰剪贴板/模拟粘贴。"""

    def __init__(self) -> None:
        self.outputs: list[str] = []

    def output(self, text: str) -> None:
        self.outputs.append(text)


def _make_client(tmp_path: Path, monkeypatch) -> ClientApp:
    """构造未启动的 ClientApp：替身通知器/托盘/输出管道，落盘目录与用户配置隔离到 tmp。"""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))
    client = ClientApp(Settings(socket_path="/nonexistent/zen_t34.sock"))
    client._notifier = _RecordingNotifier()
    client._tray = _FakeTray()
    client._pipeline = _FakePipeline()
    # 断开网络 worker（未启动线程时 emit 为同步直连，socket 失败会同步推进状态机，
    # 干扰落盘断言）；识别结果改由测试手动 fire(TRANSCRIBE_DONE/FAILED) 注入
    client.sig_recognize_request.disconnect(client._worker.recognize)
    return client


class TestSaveIsolation:
    def test_save_wav_failure_does_not_block_recognize(self, qapp, tmp_path, monkeypatch):
        """save_wav 抛错 → 告警通知，但识别请求照常发出（🔴 落盘不得阻断主流程）。"""
        client = _make_client(tmp_path, monkeypatch)
        emitted: list[bytes] = []
        client.sig_recognize_request.connect(emitted.append)

        def _boom(pcm: bytes):
            raise OSError("磁盘已满")

        monkeypatch.setattr(client._store, "save_wav", _boom)
        client._sm._state = State.RECORDING  # 测试直接置位（避免触发录音监听器）
        client._sm.fire(Event.HOTKEY_RELEASE)

        assert emitted == [b""]  # 未在真实录音，recorder.stop 返回空 PCM
        assert client.state is State.TRANSCRIBING  # 主流程未中断
        assert client._current_wav_path is None
        assert any("录音保存失败" in m for _, m in client._notifier.messages)

    def test_wav_and_txt_saved_on_success(self, qapp, tmp_path, monkeypatch):
        """成功路径：wav 在停录时落盘，txt 在识别完成时落盘且同基名。"""
        client = _make_client(tmp_path, monkeypatch)
        client._sm._state = State.RECORDING
        client._sm.fire(Event.HOTKEY_RELEASE)
        wav_path = client._current_wav_path
        assert wav_path is not None and wav_path.exists()

        client._sm.fire(Event.TRANSCRIBE_DONE, {"text": "识别结果文本"})

        txt_path = wav_path.with_suffix(".txt")
        assert txt_path.read_text(encoding="utf-8") == "识别结果文本"
        assert client._current_wav_path is None  # 用后清空
        assert client._pipeline.outputs == ["识别结果文本"]  # 剪贴板主流程不变
        assert client.state is State.IDLE

    def test_save_disabled_skips_wav(self, qapp, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        client._settings.save_recordings = False
        client._sm._state = State.RECORDING
        client._sm.fire(Event.HOTKEY_RELEASE)
        assert client._current_wav_path is None
        if client._store.directory.exists():
            assert not any(client._store.directory.iterdir())

    def test_recognize_failure_keeps_wav_without_txt(self, qapp, tmp_path, monkeypatch):
        """识别失败（ERROR 转移）：wav 保留、不写 txt，仅清空关联。"""
        client = _make_client(tmp_path, monkeypatch)
        client._sm._state = State.RECORDING
        client._sm.fire(Event.HOTKEY_RELEASE)
        wav_path = client._current_wav_path

        client._sm.fire(Event.TRANSCRIBE_FAILED, "模拟识别失败")

        assert wav_path.exists()
        assert not wav_path.with_suffix(".txt").exists()
        assert client._current_wav_path is None
        assert client.state is State.IDLE  # ERROR 瞬态已归位


class TestSaveSlots:
    def test_toggle_persists_and_syncs(self, qapp, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        persisted: list[tuple[str, object]] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        client._on_toggle_save(False)
        assert persisted == [("save_recordings", False)]
        assert client._settings.save_recordings is False

    def test_toggle_persist_failure_rolls_back(self, qapp, tmp_path, monkeypatch):
        """落盘失败 → 回滚勾选态 + 通知，运行态开关不变（🔴 先落盘后切换）。"""
        client = _make_client(tmp_path, monkeypatch)

        def _boom(key, value):
            raise OSError("磁盘只读")

        monkeypatch.setattr(app_mod, "set_user_config_value", _boom)
        before = client._settings.save_recordings
        client._on_toggle_save(not before)
        assert client._settings.save_recordings is before
        assert client._tray.checked == [before]  # 勾选态回滚
        assert any(MSG_SAVE_TOGGLE_PERSIST_FAILED.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_apply_save_dir_success(self, qapp, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        persisted: list[tuple[str, object]] = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        target = tmp_path / "new_recordings"
        assert client._apply_save_dir(target) is True
        assert persisted == [("recordings_dir", str(target))]
        assert client._settings.recordings_dir == target
        assert client._store.directory == target
        assert target.is_dir()  # 可写探测顺带创建
        assert any("已更新" in m for _, m in client._notifier.messages)

    def test_apply_save_dir_unwritable_rejected(self, qapp, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)
        persisted: list = []
        monkeypatch.setattr(
            app_mod, "set_user_config_value",
            lambda key, value: persisted.append((key, value)),
        )
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        old_dir = client._store.directory
        assert client._apply_save_dir(blocker / "sub") is False
        assert persisted == []  # 未落盘
        assert client._store.directory == old_dir
        assert any(MSG_SAVE_DIR_INVALID.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_apply_save_dir_persist_failure(self, qapp, tmp_path, monkeypatch):
        client = _make_client(tmp_path, monkeypatch)

        def _boom(key, value):
            raise OSError("磁盘只读")

        monkeypatch.setattr(app_mod, "set_user_config_value", _boom)
        old_dir = client._store.directory
        assert client._apply_save_dir(tmp_path / "new_recordings") is False
        assert client._store.directory == old_dir
        assert any(MSG_SAVE_DIR_PERSIST_FAILED.split("：")[0] in m
                   for _, m in client._notifier.messages)

    def test_choose_dir_busy_guard(self, qapp, tmp_path, monkeypatch):
        """录音/识别中禁止选目录（与改快捷键同守卫）。"""
        client = _make_client(tmp_path, monkeypatch)
        client._sm._state = State.RECORDING  # 测试直接置位（避免触发录音监听器）
        client._on_choose_dir()
        assert any(MSG_SAVE_DIR_BUSY in m for _, m in client._notifier.messages)
