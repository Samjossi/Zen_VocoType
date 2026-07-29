"""audio_chunk 会话生命周期与处理器测试（v1.4，CP2/CP3）。

覆盖（CP2 检查点）：正常流（begin→N×data→end→WAV 内容校验）、seq 乱序/
跳号/重复 4003、预告/累计超上限 4004、空闲惰性清理 + 周期兜底、断连清理、
跨连接续传拒绝、end 切换中 2002 会话保留；
以及动态超时公式与引擎上限（CP3 一部分）。
"""

import time
import wave
import uuid

import pytest

from zen_vocotype_protocol import chunk as chunk_proto
from zen_vocotype_protocol import errors

from zen_vocotype_service.config import ModelEntry, Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers import audio_chunk
from zen_vocotype_service.inference.chunk_session import (
    ChunkSessionRegistry,
    SessionStateError,
    SessionTooLargeError,
)
from zen_vocotype_service.inference.worker import InferenceWorker
from zen_vocotype_service.models.loader import (
    EngineLimitError,
    _check_engine_limit,
    _extract_sensevoice_language,
    _parse_sentence_segments,
)
from zen_vocotype_service.protocol_io import ProtocolError
from zen_vocotype_service.state import ServiceState

PCM_1S = b"\x01\x00" * 16000  # 1 秒 16kHz/16bit/单声道
AUDIO_FORMAT = {"sample_rate": 16000, "channels": 1, "sample_width": 2}
OWNER_A, OWNER_B = object(), object()  # 连接令牌


def _sid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Registry（会话表）级
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path):
    return ChunkSessionRegistry(Settings(chunk_session_dir=tmp_path / "sessions"))


class TestRegistryNormalFlow:
    def test_begin_append_finish_roundtrip(self, registry, tmp_path):
        sid = _sid()
        session = registry.begin(OWNER_A, sid)
        assert session.wav_path.exists()
        received = registry.append(OWNER_A, sid, 0, PCM_1S)
        assert received == len(PCM_1S)
        received = registry.append(OWNER_A, sid, 1, PCM_1S)
        assert received == len(PCM_1S) * 2
        session = registry.finish(OWNER_A, sid)
        assert registry.active_count == 0
        # WAV 头修正且内容完整（落盘往返校验，🔴 非内存驻留）
        with wave.open(str(session.wav_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 16000
            assert wav_file.readframes(wav_file.getnframes()) == PCM_1S * 2
        assert session.pcm_seconds == pytest.approx(2.0)
        registry.discard(session)
        assert not session.wav_path.exists()

    def test_finish_unknown_session_4003(self, registry):
        with pytest.raises(SessionStateError):
            registry.finish(OWNER_A, _sid())

    def test_empty_data_frame_allowed(self, registry):
        """空体 data 帧（keepalive 语义）：seq 照常推进，received 不变。"""
        sid = _sid()
        registry.begin(OWNER_A, sid)
        assert registry.append(OWNER_A, sid, 0, b"") == 0
        assert registry.append(OWNER_A, sid, 1, PCM_1S) == len(PCM_1S)


class TestRegistryStateErrors:
    def test_begin_twice_same_connection_4003(self, registry):
        registry.begin(OWNER_A, _sid())
        with pytest.raises(SessionStateError, match="单连接单活跃会话"):
            registry.begin(OWNER_A, _sid())

    def test_session_id_conflict_across_connections_4003(self, registry):
        sid = _sid()
        registry.begin(OWNER_A, sid)
        with pytest.raises(SessionStateError, match="冲突"):
            registry.begin(OWNER_B, sid)

    def test_cross_connection_access_rejected(self, registry):
        """会话绑定连接：他连接 data/end 一律 4003（🔴 禁止跨连接续传）。"""
        sid = _sid()
        registry.begin(OWNER_A, sid)
        with pytest.raises(SessionStateError, match="跨连接"):
            registry.append(OWNER_B, sid, 0, PCM_1S)
        with pytest.raises(SessionStateError, match="跨连接"):
            registry.finish(OWNER_B, sid)

    def test_seq_out_of_order_4003(self, registry):
        sid = _sid()
        registry.begin(OWNER_A, sid)
        registry.append(OWNER_A, sid, 0, PCM_1S)
        with pytest.raises(SessionStateError, match="乱序"):
            registry.append(OWNER_A, sid, 2, PCM_1S)  # 跳号

    def test_seq_duplicate_4003(self, registry):
        sid = _sid()
        registry.begin(OWNER_A, sid)
        registry.append(OWNER_A, sid, 0, PCM_1S)
        with pytest.raises(SessionStateError, match="乱序"):
            registry.append(OWNER_A, sid, 0, PCM_1S)  # 重复


class TestRegistryTooLarge:
    def test_total_bytes_forewarn_rejected_4004(self, tmp_path):
        registry = ChunkSessionRegistry(
            Settings(chunk_session_dir=tmp_path / "s", chunk_session_max_bytes=100)
        )
        with pytest.raises(SessionTooLargeError):
            registry.begin(OWNER_A, _sid(), total_bytes=101)
        assert registry.active_count == 0  # 会话未创建

    def test_cumulative_overflow_4004_and_destroyed(self, tmp_path):
        registry = ChunkSessionRegistry(
            Settings(chunk_session_dir=tmp_path / "s", chunk_session_max_bytes=len(PCM_1S))
        )
        sid = _sid()
        session = registry.begin(OWNER_A, sid)
        wav_path = session.wav_path
        with pytest.raises(SessionTooLargeError, match="已销毁"):
            registry.append(OWNER_A, sid, 0, PCM_1S * 2)
        assert registry.active_count == 0  # 会话已销毁
        assert not wav_path.exists()  # 文件已删除（🔴 无资源残留）


class TestRegistryIdleAndDisconnect:
    def test_lazy_idle_cleanup(self, tmp_path):
        registry = ChunkSessionRegistry(
            Settings(chunk_session_dir=tmp_path / "s", chunk_session_idle_timeout_s=0.1)
        )
        sid = _sid()
        session = registry.begin(OWNER_A, sid)
        session.last_active = time.monotonic() - 10  # 模拟空闲
        wav_path = session.wav_path
        with pytest.raises(SessionStateError, match="空闲"):
            registry.append(OWNER_A, sid, 0, PCM_1S)
        assert registry.active_count == 0
        assert not wav_path.exists()

    def test_sweep_idle_periodic(self, tmp_path):
        registry = ChunkSessionRegistry(
            Settings(chunk_session_dir=tmp_path / "s", chunk_session_idle_timeout_s=0.1)
        )
        session = registry.begin(OWNER_A, _sid())
        session.last_active = time.monotonic() - 10
        registry.sweep_idle()
        assert registry.active_count == 0
        assert not session.wav_path.exists()

    def test_destroy_for_connection(self, registry):
        """断连清理：本连接会话销毁且文件删除，他连接会话不受影响。"""
        sid_a, sid_b = _sid(), _sid()
        session_a = registry.begin(OWNER_A, sid_a)
        registry.begin(OWNER_B, sid_b)
        registry.destroy_for_connection(OWNER_A)
        assert registry.active_count == 1
        assert not session_a.wav_path.exists()
        registry.destroy_for_connection(OWNER_B)
        assert registry.active_count == 0


# ---------------------------------------------------------------------------
# Handler 级（假模型 worker）
# ---------------------------------------------------------------------------


class _FakeModel:
    def generate(self, **kwargs):
        return [{"text": "长音频识别结果"}]


class _FakeLoaded:
    def __init__(self, name, entry=None):
        self.name = name
        self.model = _FakeModel()
        self.entry = entry or ModelEntry(model_id="fake")

    def release(self):
        pass


class _FakeManager:
    def __init__(self, entry=None):
        self.current = _FakeLoaded("fake-model", entry)

    def switch(self, model_name):
        pass

    def release(self):
        pass


def _make_ctx(settings: Settings) -> ServiceContext:
    ctx = ServiceContext(settings, ServiceState())
    ctx.model_manager = _FakeManager()
    worker = InferenceWorker(settings, ctx.model_manager)
    worker.start()
    ctx.worker = worker
    ctx.state.mark_ready("fake-model")
    return ctx


@pytest.fixture()
def ctx(tmp_path):
    context = _make_ctx(Settings(chunk_session_dir=tmp_path / "sessions"))
    yield context
    context.worker.stop()


def _header(phase_chunk: dict, **extra) -> dict:
    header = {"chunk": phase_chunk}
    header.update(extra)
    return header


class TestAudioChunkHandler:
    def test_full_flow(self, ctx):
        sid = _sid()
        payload = audio_chunk.handle(
            _header(chunk_proto.build_chunk_begin(sid, total_bytes=len(PCM_1S)),
                    audio_format=AUDIO_FORMAT),
            b"", ctx, OWNER_A,
        )
        assert payload["session_id"] == sid
        assert payload["max_session_bytes"] > 0
        assert payload["max_session_seconds"] > 0
        payload = audio_chunk.handle(
            _header(chunk_proto.build_chunk_data(sid, 0), audio_bytes=len(PCM_1S)),
            PCM_1S, ctx, OWNER_A,
        )
        assert payload == {"received_bytes": len(PCM_1S)}
        payload = audio_chunk.handle(
            _header(chunk_proto.build_chunk_end(sid)), b"", ctx, OWNER_A
        )
        assert payload["text"] == "长音频识别结果"
        assert payload["duration_ms"] == 1000
        assert ctx.chunk_sessions.active_count == 0

    def test_not_ready_2001(self, ctx):
        ctx.state.mark_error("x")
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_end(_sid())), b"", ctx, OWNER_A
            )
        assert ei.value.code == errors.ERR_NOT_READY

    def test_missing_chunk_1004(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle({}, b"", ctx, OWNER_A)
        assert ei.value.code == errors.ERR_MISSING_FIELD

    def test_bad_chunk_structure_4003(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(_header({"phase": "bogus"}), b"", ctx, OWNER_A)
        assert ei.value.code == errors.ERR_SESSION_STATE

    def test_begin_with_body_4001(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_begin(_sid()),
                        audio_format=AUDIO_FORMAT),
                PCM_1S, ctx, OWNER_A,
            )
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_begin_bad_audio_format_4001(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_begin(_sid()),
                        audio_format={"sample_rate": 8000, "channels": 1, "sample_width": 2}),
                b"", ctx, OWNER_A,
            )
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_data_body_mismatch_4001(self, ctx):
        sid = _sid()
        audio_chunk.handle(
            _header(chunk_proto.build_chunk_begin(sid), audio_format=AUDIO_FORMAT),
            b"", ctx, OWNER_A,
        )
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_data(sid, 0), audio_bytes=999),
                PCM_1S, ctx, OWNER_A,
            )
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_data_out_of_order_4003(self, ctx):
        sid = _sid()
        audio_chunk.handle(
            _header(chunk_proto.build_chunk_begin(sid), audio_format=AUDIO_FORMAT),
            b"", ctx, OWNER_A,
        )
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_data(sid, 5), audio_bytes=len(PCM_1S)),
                PCM_1S, ctx, OWNER_A,
            )
        assert ei.value.code == errors.ERR_SESSION_STATE

    def test_end_switching_2002_session_kept(self, ctx):
        """切换中 end → 2002 且会话保留（客户端可稍后重发 end）。"""
        sid = _sid()
        audio_chunk.handle(
            _header(chunk_proto.build_chunk_begin(sid), audio_format=AUDIO_FORMAT),
            b"", ctx, OWNER_A,
        )
        audio_chunk.handle(
            _header(chunk_proto.build_chunk_data(sid, 0), audio_bytes=len(PCM_1S)),
            PCM_1S, ctx, OWNER_A,
        )
        ctx.worker._switching.set()
        try:
            with pytest.raises(ProtocolError) as ei:
                audio_chunk.handle(
                    _header(chunk_proto.build_chunk_end(sid)), b"", ctx, OWNER_A
                )
            assert ei.value.code == errors.ERR_BUSY
            assert "model_switching" in ei.value.message
            assert ctx.chunk_sessions.active_count == 1  # 🔴 会话保留
        finally:
            ctx.worker._switching.clear()
        # 切换结束后重发 end 成功
        payload = audio_chunk.handle(
            _header(chunk_proto.build_chunk_end(sid)), b"", ctx, OWNER_A
        )
        assert payload["text"] == "长音频识别结果"

    def test_end_unknown_session_4003(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_end(_sid())), b"", ctx, OWNER_A
            )
        assert ei.value.code == errors.ERR_SESSION_STATE

    def test_forewarn_too_large_4004(self, tmp_path):
        context = _make_ctx(
            Settings(chunk_session_dir=tmp_path / "s", chunk_session_max_bytes=100)
        )
        try:
            with pytest.raises(ProtocolError) as ei:
                audio_chunk.handle(
                    _header(chunk_proto.build_chunk_begin(_sid(), total_bytes=200),
                            audio_format=AUDIO_FORMAT),
                    b"", context, OWNER_A,
                )
            assert ei.value.code == errors.ERR_SESSION_TOO_LARGE
        finally:
            context.worker.stop()

    def test_cumulative_too_large_4004(self, tmp_path):
        context = _make_ctx(
            Settings(
                chunk_session_dir=tmp_path / "s",
                chunk_session_max_bytes=len(PCM_1S),
            )
        )
        try:
            sid = _sid()
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_begin(sid), audio_format=AUDIO_FORMAT),
                b"", context, OWNER_A,
            )
            audio_chunk.handle(
                _header(chunk_proto.build_chunk_data(sid, 0), audio_bytes=len(PCM_1S)),
                PCM_1S, context, OWNER_A,
            )
            with pytest.raises(ProtocolError) as ei:
                audio_chunk.handle(
                    _header(chunk_proto.build_chunk_data(sid, 1), audio_bytes=2),
                    b"\x00\x00", context, OWNER_A,
                )
            assert ei.value.code == errors.ERR_SESSION_TOO_LARGE
            assert context.chunk_sessions.active_count == 0
        finally:
            context.worker.stop()


# ---------------------------------------------------------------------------
# 动态超时与引擎上限（CP3）
# ---------------------------------------------------------------------------


class TestDynamicTimeout:
    def _worker(self, tmp_path, entry=None, **kwargs):
        settings = Settings(chunk_session_dir=tmp_path / "s", **kwargs)
        manager = _FakeManager(entry)
        worker = InferenceWorker(settings, manager)
        return worker

    def test_short_audio_uses_base(self, tmp_path):
        worker = self._worker(tmp_path)
        # 1 秒音频：1 × 1.0（未标定缺省）× 2 = 2s → 基础值 300s 兜底
        assert worker._calc_recognize_timeout(len(PCM_1S)) == 300.0

    def test_long_audio_scales(self, tmp_path):
        entry = ModelEntry(model_id="fake", rtf_estimate=0.2)
        worker = self._worker(tmp_path, entry)
        # 2 小时音频（7200s）：7200 × 0.2 × 2 = 2880s
        pcm_bytes = 7200 * 32000
        assert worker._calc_recognize_timeout(pcm_bytes) == pytest.approx(2880.0)

    def test_uncalibrated_entry_uses_conservative_default(self, tmp_path):
        worker = self._worker(tmp_path)  # entry.rtf_estimate=None
        # 1 小时音频：3600 × 1.0 × 2 = 7200s
        assert worker._calc_recognize_timeout(3600 * 32000) == pytest.approx(7200.0)

    def test_safety_factor_configurable(self, tmp_path):
        entry = ModelEntry(model_id="fake", rtf_estimate=0.5)
        worker = self._worker(tmp_path, entry, rtf_safety_factor=4.0)
        # 1 小时：3600 × 0.5 × 4 = 7200s
        assert worker._calc_recognize_timeout(3600 * 32000) == pytest.approx(7200.0)


class TestEngineLimit:
    def test_qwen3_over_20min_rejected(self):
        entry = ModelEntry(model_id="fake", engine_type="qwen3-asr")
        with pytest.raises(EngineLimitError, match="engine_limit"):
            _check_engine_limit(entry, 20 * 60 + 1)

    def test_qwen3_within_limit_ok(self):
        entry = ModelEntry(model_id="fake", engine_type="qwen3-asr")
        _check_engine_limit(entry, 20 * 60)  # 恰在上限不拒绝

    def test_other_engines_no_limit(self):
        for engine in ("funasr", "funasr-gguf"):
            entry = ModelEntry(model_id="fake", engine_type=engine)
            _check_engine_limit(entry, 5 * 3600)  # 5 小时也不拒（引擎无此限）


class TestFunasrOutputParsing:
    def test_language_extract(self):
        assert _extract_sensevoice_language("<|zh|><|HAPPY|><|Speech|><|woitn|>你好") == "zh"
        assert _extract_sensevoice_language("<|en|>hello") == "en"
        assert _extract_sensevoice_language("干净文本") is None

    def test_segments_parse(self):
        item = {
            "sentence_info": [
                {"start": 0, "end": 1500, "text": "第一段"},
                {"start": 1500, "end": 3200, "text": "第二段"},
            ]
        }
        assert _parse_sentence_segments(item) == [
            {"start_ms": 0, "end_ms": 1500, "text": "第一段"},
            {"start_ms": 1500, "end_ms": 3200, "text": "第二段"},
        ]

    def test_segments_defensive(self):
        """字段缺失/结构不符 → 空表（字段整体省略，禁止编造）。"""
        assert _parse_sentence_segments({}) == []
        assert _parse_sentence_segments({"sentence_info": None}) == []
        assert _parse_sentence_segments({"sentence_info": [{"start": "bad"}]}) == []
        assert _parse_sentence_segments({"sentence_info": ["not-a-dict"]}) == []
