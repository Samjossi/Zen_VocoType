"""引擎类型分支单元测试（新增 Fun-ASR-Nano / Qwen3-ASR 增量引擎）。

覆盖：
- ``ModelEntry.engine_type`` 默认值与合法性（旧配置零感知）
- ``_build_automl_params`` 合并 extra_params（Fun-ASR-Nano 的 trust_remote_code）
- ``load_model`` 按 engine_type 路由（qwen3-asr 不经 FunASR AutoModel）
- ``_load_qwen3_asr``：model_id 经 modelscope 下载、local_path 直载、失败包装
- ``run_inference`` 双引擎分支与返回结构归一化
- ``selftest`` 双引擎假模型通路（真实自检 PCM 资产）
"""

import subprocess
import types
from pathlib import Path

import pytest

from zen_vocotype_service.config import ModelEntry
from zen_vocotype_service.models import loader as loader_mod
from zen_vocotype_service.models.loader import (
    GgufRuntime,
    LoadedModel,
    ModelLoadError,
    _build_automl_params,
    _load_funasr_gguf,
    _load_qwen3_asr,
    _run_funasr_gguf,
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

    def test_funasr_gguf_engine_type_accepted(self):
        entry = ModelEntry(model_id="FunAudioLLM/Fun-ASR-Nano-GGUF",
                           engine_type="funasr-gguf")
        assert entry.engine_type == "funasr-gguf"

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

    def test_funasr_branch_sensevoice_meta_tags_filtered(self):
        """SenseVoice 原始元标签（语种/情感/事件/ITN）经官方后处理过滤。"""
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        loaded = LoadedModel(
            "fake",
            entry,
            _FakeFunASRModel(text="<|zh|><|NEUTRAL|><|Speech|><|woitn|>你好世界"),
        )
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome["text"] == "你好世界"
        assert "<|" not in outcome["text"]

    def test_funasr_branch_emotion_event_to_emoji(self):
        """情感/事件标签转 emoji（sensevoice 差异化能力的可见呈现）。"""
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        loaded = LoadedModel(
            "fake",
            entry,
            _FakeFunASRModel(text="<|zh|><|HAPPY|><|woitn|>太好了"),
        )
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome["text"] == "太好了😊"

    def test_funasr_branch_clean_text_untouched(self):
        """干净文本（fun-asr-nano 等）不触发后处理，原样透传。"""
        entry = ModelEntry(model_id="iic/X")
        loaded = LoadedModel("fake", entry, _FakeFunASRModel(text="正常文本。"))
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome["text"] == "正常文本。"

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


class TestFunasrGgufLoad:
    """funasr-gguf 加载分支：二进制/权重校验与下载路径。"""

    @pytest.fixture()
    def gguf_files(self, tmp_path, monkeypatch):
        """造齐三份假权重 + 假 CLI，返回 (weights_dir, cli_path)。"""
        weights = tmp_path / "weights"
        weights.mkdir()
        for fn in ("funasr-encoder-f16.gguf", "qwen3-0.6b-q8_0.gguf", "fsmn-vad.gguf"):
            (weights / fn).write_bytes(b"fake-gguf")
        cli = tmp_path / "llama-funasr-cli"
        cli.write_text("#!/bin/true")
        monkeypatch.setattr(loader_mod, "_gguf_cli_path", lambda: cli)
        monkeypatch.setattr(loader_mod, "_gguf_tmp_dir", lambda: tmp_path / "gguf_tmp")
        return weights, cli

    def test_model_id_downloads_encoder_llm_vad(self, monkeypatch, gguf_files):
        weights, _ = gguf_files
        import modelscope

        calls = []

        def fake_download(repo, allow_patterns=None):
            calls.append((repo, allow_patterns))
            return str(weights)

        monkeypatch.setattr(modelscope, "snapshot_download", fake_download)
        entry = ModelEntry(
            model_id="FunAudioLLM/Fun-ASR-Nano-GGUF",
            engine_type="funasr-gguf",
            extra_params={"vad_repo": "FunAudioLLM/fsmn-vad-GGUF"},
        )
        loaded = _load_funasr_gguf("fun-asr-nano", entry)
        assert isinstance(loaded.model, GgufRuntime)
        # 🔴 encoder/llm 按文件名精准下载（不拉全部量化档），VAD 走独立仓库
        assert calls[0] == (
            "FunAudioLLM/Fun-ASR-Nano-GGUF",
            ["funasr-encoder-f16.gguf", "qwen3-0.6b-q8_0.gguf"],
        )
        assert calls[1][0] == "FunAudioLLM/fsmn-vad-GGUF"

    def test_local_path_skips_download(self, monkeypatch, gguf_files):
        weights, _ = gguf_files
        import modelscope

        def _forbidden(*a, **k):
            raise AssertionError("local_path 条目不应调用 snapshot_download")

        monkeypatch.setattr(modelscope, "snapshot_download", _forbidden)
        entry = ModelEntry(local_path=weights, engine_type="funasr-gguf")
        loaded = _load_funasr_gguf("fun-asr-nano", entry)
        assert loaded.model.encoder == weights / "funasr-encoder-f16.gguf"

    def test_missing_weight_raises(self, gguf_files):
        weights, _ = gguf_files
        (weights / "qwen3-0.6b-q8_0.gguf").unlink()
        entry = ModelEntry(local_path=weights, engine_type="funasr-gguf")
        with pytest.raises(ModelLoadError, match="GGUF 权重缺失"):
            _load_funasr_gguf("fun-asr-nano", entry)

    def test_missing_cli_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            loader_mod, "_gguf_cli_path", lambda: tmp_path / "no-such-cli"
        )
        entry = ModelEntry(local_path=tmp_path, engine_type="funasr-gguf")
        with pytest.raises(ModelLoadError, match="二进制缺失"):
            _load_funasr_gguf("fun-asr-nano", entry)


class TestRunFunasrGguf:
    """funasr-gguf 推理分支：子进程调用、输出解析、临时文件清理。"""

    @pytest.fixture()
    def runtime(self, tmp_path, monkeypatch):
        monkeypatch.setattr(loader_mod, "_gguf_tmp_dir", lambda: tmp_path)
        return GgufRuntime(
            cli=tmp_path / "cli",
            encoder=tmp_path / "enc.gguf",
            llm=tmp_path / "llm.gguf",
            vad=tmp_path / "vad.gguf",
        )

    def _fake_run(self, monkeypatch, stdout="识别文本。", returncode=0, stderr=""):
        seen = {}

        def fake_run(args, capture_output, text, timeout):
            seen["args"] = args
            seen["timeout"] = timeout
            return subprocess.CompletedProcess(args, returncode, stdout, stderr)

        monkeypatch.setattr(loader_mod.subprocess, "run", fake_run)
        return seen

    def test_success_returns_stdout_text(self, monkeypatch, runtime, tmp_path):
        seen = self._fake_run(monkeypatch, stdout="你好世界。\n")
        outcome = _run_funasr_gguf(runtime, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome == {"text": "你好世界。", "confidence": None}
        args = seen["args"]
        assert args[1:3] == ["--enc", str(runtime.encoder)]
        assert args[3:5] == ["-m", str(runtime.llm)]
        assert args[5:7] == ["--vad", str(runtime.vad)]
        # 临时 WAV 用完即删
        assert not Path(args[7]).exists()
        assert list(tmp_path.glob("*.wav")) == []

    def test_empty_text_allowed(self, monkeypatch, runtime):
        """静默音频返回空文本属合法结果（与既有引擎语义一致）。"""
        self._fake_run(monkeypatch, stdout="")
        outcome = _run_funasr_gguf(runtime, pcm_to_float_array(b"\x00\x00" * 160))
        assert outcome["text"] == ""

    def test_nonzero_rc_raises_with_stderr_tail(self, monkeypatch, runtime):
        self._fake_run(
            monkeypatch, returncode=1, stdout="",
            stderr="log1\naudio: failed to open/decode\nfailed to read audio",
        )
        with pytest.raises(RuntimeError, match="rc=1.*failed to read audio"):
            _run_funasr_gguf(runtime, pcm_to_float_array(b"\x01\x00" * 160))

    def test_timeout_raises(self, monkeypatch, runtime):
        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="cli", timeout=300)

        monkeypatch.setattr(loader_mod.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="GGUF CLI 超时"):
            _run_funasr_gguf(runtime, pcm_to_float_array(b"\x01\x00" * 160))

    def test_run_inference_routes_to_gguf(self, monkeypatch, runtime):
        self._fake_run(monkeypatch, stdout="路由正确")
        entry = ModelEntry(local_path="/x", engine_type="funasr-gguf")
        loaded = LoadedModel("fake-gguf", entry, runtime)
        outcome = run_inference(loaded, pcm_to_float_array(b"\x01\x00" * 160))
        assert outcome["text"] == "路由正确"
