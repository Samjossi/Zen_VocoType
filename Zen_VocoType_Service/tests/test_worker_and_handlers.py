"""T1.5 单元测试：推理队列 worker 与 recognize/model_switch 处理器错误码映射。

以假模型管理器替代真实 AutoModel，快速覆盖：队列满 2002、切换中拒绝 2002、
音频非法 4001、未就绪 2001、未注册 3001、加载失败 3002、自检失败 3003、
推理异常 4002。
"""

import time

import pytest

from zen_vocotype_protocol import errors

from zen_vocotype_service.config import Settings
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.handlers import model_switch, recognize
from zen_vocotype_service.inference.worker import InferenceWorker
from zen_vocotype_service.models.manager import ModelSwitchError
from zen_vocotype_service.models.registry import ModelNotRegisteredError
from zen_vocotype_service.protocol_io import ProtocolError
from zen_vocotype_service.state import ServiceState

PCM_1S = b"\x01\x00" * 16000  # 1 秒 16kHz/16bit/单声道

VALID_HEADER = {
    "audio_format": {"sample_rate": 16000, "channels": 1, "sample_width": 2},
    "audio_bytes": len(PCM_1S),
}


class _FakeModel:
    """假 AutoModel：generate 返回合法结构。"""

    def __init__(self, text="你好", delay=0.0, fail=False):
        self._text = text
        self._delay = delay
        self._fail = fail

    def generate(self, **kwargs):
        if self._delay:
            time.sleep(self._delay)
        if self._fail:
            raise RuntimeError("模拟推理崩溃")
        return [{"text": self._text}]


class _FakeLoaded:
    def __init__(self, name, model):
        self.name = name
        self.model = model

    def release(self):
        pass


class FakeModelManager:
    """可控假模型管理器（结构与 ModelManager 对齐）。"""

    def __init__(self, model=None, switch_exc: Exception | None = None):
        self.current = _FakeLoaded("fake-model", model or _FakeModel())
        self._switch_exc = switch_exc
        self.switched_to: list[str] = []

    def switch(self, model_name: str) -> None:
        if self._switch_exc is not None:
            raise self._switch_exc
        self.current = _FakeLoaded(model_name, _FakeModel())
        self.switched_to.append(model_name)

    def model_info(self) -> dict:
        return {
            "current_model": self.current.name,
            "available_models": [
                {"name": self.current.name, "loaded": True, "source": "model_id:fake"}
            ],
        }

    def release(self):
        pass


def _make_ctx(settings: Settings, manager, *, ready=True) -> ServiceContext:
    ctx = ServiceContext(settings, ServiceState())
    ctx.model_manager = manager
    worker = InferenceWorker(settings, manager)
    worker.start()
    ctx.worker = worker
    if ready:
        ctx.state.mark_ready(manager.current.name)
    return ctx


@pytest.fixture()
def ctx():
    settings = Settings()
    context = _make_ctx(settings, FakeModelManager())
    yield context
    context.worker.stop()


class TestRecognizeHandler:
    def test_success(self, ctx):
        result = recognize.handle(dict(VALID_HEADER), PCM_1S, ctx)
        assert result["text"] == "你好"
        assert result["confidence"] is None  # 模型不给置信度 → None，禁止编造
        assert result["duration_ms"] == 1000

    def test_not_ready_2001(self, ctx):
        ctx.state.mark_error("x")
        # error 状态下 recognize 拒绝
        with pytest.raises(ProtocolError) as ei:
            recognize.handle(dict(VALID_HEADER), PCM_1S, ctx)
        assert ei.value.code == errors.ERR_NOT_READY

    def test_invalid_format_4001(self, ctx):
        header = dict(VALID_HEADER)
        header["audio_format"] = {"sample_rate": 8000, "channels": 1, "sample_width": 2}
        with pytest.raises(ProtocolError) as ei:
            recognize.handle(header, PCM_1S, ctx)
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_missing_audio_format_4001(self, ctx):
        header = {"audio_bytes": len(PCM_1S)}
        with pytest.raises(ProtocolError) as ei:
            recognize.handle(header, PCM_1S, ctx)
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_body_length_mismatch_4001(self, ctx):
        header = dict(VALID_HEADER)
        header["audio_bytes"] = len(PCM_1S) + 2
        with pytest.raises(ProtocolError) as ei:
            recognize.handle(header, PCM_1S, ctx)
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_empty_body_4001(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            recognize.handle({"audio_format": VALID_HEADER["audio_format"], "audio_bytes": 0}, b"", ctx)
        assert ei.value.code == errors.ERR_INVALID_AUDIO

    def test_inference_failure_4002(self):
        settings = Settings()
        context = _make_ctx(settings, FakeModelManager(model=_FakeModel(fail=True)))
        try:
            with pytest.raises(ProtocolError) as ei:
                recognize.handle(dict(VALID_HEADER), PCM_1S, context)
            assert ei.value.code == errors.ERR_RECOGNITION_FAILED
            assert "模拟推理崩溃" in ei.value.message
        finally:
            context.worker.stop()

    def test_queue_full_2002(self):
        settings = Settings(queue_max_pending=1, infer_timeout_s=30)
        # 慢模型占住 worker，第二个请求在队列中，第三个触发满队列
        context = _make_ctx(settings, FakeModelManager(model=_FakeModel(delay=1.0)))
        try:
            import threading

            results = []

            def call():
                try:
                    recognize.handle(dict(VALID_HEADER), PCM_1S, context)
                    results.append("ok")
                except ProtocolError as exc:
                    results.append(exc.code)

            threads = [threading.Thread(target=call) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert errors.ERR_BUSY in results
        finally:
            context.worker.stop()

    def test_recognize_rejected_while_switching_2002(self, ctx):
        ctx.worker._switching.set()  # 模拟切换进行中
        try:
            with pytest.raises(ProtocolError) as ei:
                recognize.handle(dict(VALID_HEADER), PCM_1S, ctx)
            assert ei.value.code == errors.ERR_BUSY
            assert "model_switching" in ei.value.message
        finally:
            ctx.worker._switching.clear()


class TestModelSwitchHandler:
    def test_success(self, ctx):
        result = model_switch.handle(
            {"payload": {"model_name": "other-model"}}, b"", ctx
        )
        assert result == {"current_model": "other-model"}

    def test_missing_model_name_1004(self, ctx):
        with pytest.raises(ProtocolError) as ei:
            model_switch.handle({"payload": {}}, b"", ctx)
        assert ei.value.code == errors.ERR_MISSING_FIELD

    def test_not_registered_3001(self):
        settings = Settings()
        context = _make_ctx(
            settings,
            FakeModelManager(switch_exc=ModelNotRegisteredError("不在注册表")),
        )
        try:
            with pytest.raises(ProtocolError) as ei:
                model_switch.handle({"payload": {"model_name": "x"}}, b"", context)
            assert ei.value.code == errors.ERR_MODEL_NOT_FOUND
        finally:
            context.worker.stop()

    def test_load_failed_3002(self):
        settings = Settings()
        context = _make_ctx(
            settings,
            FakeModelManager(switch_exc=ModelSwitchError("LOAD_FAILED: 磁盘满")),
        )
        try:
            with pytest.raises(ProtocolError) as ei:
                model_switch.handle({"payload": {"model_name": "x"}}, b"", context)
            assert ei.value.code == errors.ERR_MODEL_LOAD_FAILED
            assert "磁盘满" in ei.value.message
        finally:
            context.worker.stop()

    def test_selftest_failed_3003(self):
        settings = Settings()
        context = _make_ctx(
            settings,
            FakeModelManager(switch_exc=ModelSwitchError("SELFTEST_FAILED: 结构非法")),
        )
        try:
            with pytest.raises(ProtocolError) as ei:
                model_switch.handle({"payload": {"model_name": "x"}}, b"", context)
            assert ei.value.code == errors.ERR_MODEL_SWITCH_FAILED
            assert "已回滚" in ei.value.message
        finally:
            context.worker.stop()

    def test_not_ready_rejected(self, ctx):
        ctx.state = ServiceState()  # starting
        with pytest.raises(ProtocolError) as ei:
            model_switch.handle({"payload": {"model_name": "x"}}, b"", ctx)
        assert ei.value.code == errors.ERR_NOT_READY


class TestWorkerTimeout:
    def test_timeout_raises(self):
        settings = Settings(infer_timeout_s=0.5)
        context = _make_ctx(settings, FakeModelManager(model=_FakeModel(delay=5)))
        try:
            from zen_vocotype_service.inference.worker import TaskTimeoutError

            with pytest.raises(TaskTimeoutError, match="超时"):
                context.worker.submit_recognize(PCM_1S)
        finally:
            context.worker.stop()
