"""引擎类型分支单元测试（新增 Fun-ASR-Nano / Qwen3-ASR 增量引擎）。

覆盖：
- ``ModelEntry.engine_type`` 默认值与合法性（旧配置零感知）
- ``_build_automl_params`` 合并 extra_params（Fun-ASR-Nano 的 trust_remote_code）
- ``load_model`` 按 engine_type 路由（qwen3-asr 不经 FunASR AutoModel）
- ``_load_qwen3_asr``：model_id 经 modelscope 下载、local_path 直载、失败包装
- ``run_inference`` 双引擎分支与返回结构归一化
- ``selftest`` 双引擎假模型通路（真实自检 PCM 资产）
"""

import types

import pytest

from zen_vocotype_service.config import ModelEntry
from zen_vocotype_service.models.loader import (
    LoadedModel,
    ModelLoadError,
    _build_automl_params,
    _load_qwen3_asr,
    load_model,
    pcm_to_float_array,
    run_inference,
    selftest,
)


class TestModelEntryEngineType:
    def test_default_engine_type_is_funasr(self):
        """旧配置不含 engine_type 字段 → 默认 funasr（零感知）。"""
        entry = ModelEntry(**{"model_id": "iic/X"})
        assert entry.engine_type == "funasr"
        assert entry.extra_params == {}

    def test_qwen3_asr_engine_type_accepted(self):
        entry = ModelEntry(model_id="Qwen/Qwen3-ASR-1.7B", engine_type="qwen3-asr")
        assert entry.engine_type == "qwen3-asr"

    def test_unknown_engine_type_rejected(self):
        with pytest.raises(ValueError):
            ModelEntry(model_id="iic/X", engine_type="whisper")


class TestBuildAutomlParams:
    def test_extra_params_merged(self):
        entry = ModelEntry(
            model_id="FunAudioLLM/Fun-ASR-Nano-2512",
            extra_params={"trust_remote_code": True, "remote_code": "./model.py"},
        )
        params = _build_automl_params(entry)
        assert params["trust_remote_code"] is True
        assert params["remote_code"] == "./model.py"
        # 既有固定参数不受 extra_params 影响
        assert params["device"] == "cpu"
        assert params["disable_update"] is True

    def test_no_extra_params_by_default(self):
        params = _build_automl_params(ModelEntry(model_id="iic/X"))
        assert "trust_remote_code" not in params


class _FakeQwen3ASRModel:
    """假 Qwen3ASRModel：transcribe 返回合法结构。"""

    def __init__(self, text="你好", fail=False):
        self._text = text
        self._fail = fail

    def transcribe(self, audio, **kwargs):
        if self._fail:
            raise RuntimeError("模拟推理崩溃")
        arr, sr = audio
        assert sr == 16000
        return [types.SimpleNamespace(text=self._text, language="Chinese")]


@pytest.fixture()
def fake_qwen_asr_module(monkeypatch):
    """拦截 qwen_asr.Qwen3ASRModel.from_pretrained，记录调用参数。"""
    import qwen_asr

    calls: dict = {}

    def fake_from_pretrained(path, **kwargs):
        calls["path"] = path
        calls["kwargs"] = kwargs
        return _FakeQwen3ASRModel()

    monkeypatch.setattr(
        qwen_asr.Qwen3ASRModel, "from_pretrained", staticmethod(fake_from_pretrained)
    )
    return calls


class TestLoadModelRouting:
    def test_qwen3_asr_model_id_downloads_via_modelscope(
        self, monkeypatch, fake_qwen_asr_module
    ):
        import modelscope

        monkeypatch.setattr(
            modelscope, "snapshot_download", lambda mid: f"/cache/{mid}"
        )
        entry = ModelEntry(model_id="Qwen/Qwen3-ASR-1.7B", engine_type="qwen3-asr")
        loaded = load_model("qwen3-asr-1.7b", entry)
        assert fake_qwen_asr_module["path"] == "/cache/Qwen/Qwen3-ASR-1.7B"
        assert fake_qwen_asr_module["kwargs"]["device_map"] == "cpu"
        assert isinstance(loaded.model, _FakeQwen3ASRModel)

    def test_qwen3_asr_local_path_skips_download(
        self, monkeypatch, tmp_path, fake_qwen_asr_module
    ):
        import modelscope

        def _forbidden(mid):  # local_path 条目🔴禁止触发下载
            raise AssertionError("local_path 条目不应调用 snapshot_download")

        monkeypatch.setattr(modelscope, "snapshot_download", _forbidden)
        entry = ModelEntry(local_path=tmp_path, engine_type="qwen3-asr")
        load_model("qwen3-asr-1.7b", entry)
        assert fake_qwen_asr_module["path"] == str(tmp_path)

    def test_qwen3_asr_extra_params_forwarded(self, monkeypatch, tmp_path,
                                               fake_qwen_asr_module):
        entry = ModelEntry(
            local_path=tmp_path,
            engine_type="qwen3-asr",
            extra_params={"max_new_tokens": 256},
        )
        load_model("qwen3-asr-1.7b", entry)
        assert fake_qwen_asr_module["kwargs"]["max_new_tokens"] == 256

    def test_qwen3_asr_load_failure_wrapped(self, monkeypatch):
        import modelscope

        def _boom(mid):
            raise OSError("磁盘满")

        monkeypatch.setattr(modelscope, "snapshot_download", _boom)
        entry = ModelEntry(model_id="Qwen/Qwen3-ASR-1.7B", engine_type="qwen3-asr")
        with pytest.raises(ModelLoadError, match="磁盘满"):
            _load_qwen3_asr("qwen3-asr-1.7b", entry)


class _FakeFunASRModel:
    """假 FunASR AutoModel：generate 返回合法结构。"""

    def __init__(self, text="你好", confidence=None):
        self._text = text
        self._confidence = confidence

    def generate(self, **kwargs):
        item = {"text": self._text}
        if self._confidence is not None:
            item["confidence"] = self._confidence
        return [item]


class TestRunInference:
    def test_funasr_branch_returns_text_and_confidence(self):
        entry = ModelEntry(model_id="iic/X")
        loaded = LoadedModel("fake", entry, _FakeFunASRModel(confidence=0.9))
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome == {"text": "你好", "confidence": 0.9}

    def test_funasr_branch_confidence_none_when_absent(self):
        entry = ModelEntry(model_id="iic/X")
        loaded = LoadedModel("fake", entry, _FakeFunASRModel())
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome["confidence"] is None

    def test_qwen3_asr_branch_routes_to_transcribe(self):
        entry = ModelEntry(model_id="Qwen/X", engine_type="qwen3-asr")
        loaded = LoadedModel("fake", entry, _FakeQwen3ASRModel(text="世界"))
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        # qwen3-asr 无置信度概念 → None（🔴 禁止编造）
        assert outcome == {"text": "世界", "confidence": None}

    def test_funasr_branch_bad_structure_raises(self):
        class _Bad:
            def generate(self, **kwargs):
                return {"unexpected": True}

        loaded = LoadedModel("fake", ModelEntry(model_id="iic/X"), _Bad())
        with pytest.raises(RuntimeError, match="返回结构非法"):
            run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))

    def test_qwen3_asr_branch_empty_result_raises(self):
        class _Empty:
            def transcribe(self, audio, **kwargs):
                return []

        entry = ModelEntry(model_id="Qwen/X", engine_type="qwen3-asr")
        loaded = LoadedModel("fake", entry, _Empty())
        with pytest.raises(RuntimeError, match="返回结构非法"):
            run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))


class TestSelftestBothEngines:
    """selftest 走真实 PCM 资产 + run_inference 分支（不触网络）。"""

    def test_funasr_engine_selftest_passes(self):
        loaded = LoadedModel(
            "fake-funasr", ModelEntry(model_id="iic/X"), _FakeFunASRModel()
        )
        selftest(loaded)  # 不抛异常即通过

    def test_qwen3_asr_engine_selftest_passes(self):
        entry = ModelEntry(model_id="Qwen/X", engine_type="qwen3-asr")
        loaded = LoadedModel("fake-qwen", entry, _FakeQwen3ASRModel())
        selftest(loaded)

    def test_qwen3_asr_engine_selftest_failure_wrapped(self):
        entry = ModelEntry(model_id="Qwen/X", engine_type="qwen3-asr")
        loaded = LoadedModel("fake-qwen", entry, _FakeQwen3ASRModel(fail=True))
        with pytest.raises(ModelLoadError, match="试推理自检失败"):
            selftest(loaded)
