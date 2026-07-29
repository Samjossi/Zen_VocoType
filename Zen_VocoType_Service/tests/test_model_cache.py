"""模型缓存探测单元测试（模型缺失与下载提醒计划 T1/D1）。

分支覆盖：local_path 恒 True；model_id 命中/缺失；GGUF 双仓（权重仓 +
VAD 仓任一缺失即 False，含 vad_repo 覆盖）；vad/punc 附属模型缺失即 False；
探测 OSError 按未缓存处理且记 warning（🔴 禁止静默）。
"""

import pytest

from zen_vocotype_service.config import ModelEntry
from zen_vocotype_service.models.cache import is_model_cached
from zen_vocotype_service.models.loader import GGUF_DEFAULT_VAD_REPO


def _mkdir_cache(models_dir, model_id: str) -> None:
    (models_dir / "models" / model_id.replace("/", "--")).mkdir(parents=True)


class TestLocalPath:
    def test_local_path_always_cached(self, tmp_path):
        """local_path 条目本地直载，恒视为已缓存（不触发下载提醒）。"""
        entry = ModelEntry(local_path=tmp_path / "whatever")
        assert is_model_cached(entry, tmp_path) is True


class TestModelId:
    def test_uncached(self, tmp_path):
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        assert is_model_cached(entry, tmp_path) is False

    def test_cached(self, tmp_path):
        entry = ModelEntry(model_id="iic/SenseVoiceSmall")
        _mkdir_cache(tmp_path, "iic/SenseVoiceSmall")
        assert is_model_cached(entry, tmp_path) is True

    def test_cache_layout_uses_double_dash(self, tmp_path):
        """缓存目录布局 <models_dir>/models/<org>--<name>（与 modelscope 一致）。"""
        entry = ModelEntry(model_id="org/name")
        # 错误布局（org/name 两级目录）不得命中
        (tmp_path / "models" / "org" / "name").mkdir(parents=True)
        assert is_model_cached(entry, tmp_path) is False


class TestGgufDoubleRepo:
    @pytest.fixture()
    def entry(self) -> ModelEntry:
        return ModelEntry(
            model_id="FunAudioLLM/Fun-ASR-Nano-GGUF",
            engine_type="funasr-gguf",
        )

    def test_weights_only_missing_vad_is_uncached(self, tmp_path, entry):
        """🔴 只命中权重仓不算已缓存（VAD 共享仓仍会触发下载）。"""
        _mkdir_cache(tmp_path, "FunAudioLLM/Fun-ASR-Nano-GGUF")
        assert is_model_cached(entry, tmp_path) is False

    def test_both_repos_cached(self, tmp_path, entry):
        _mkdir_cache(tmp_path, "FunAudioLLM/Fun-ASR-Nano-GGUF")
        _mkdir_cache(tmp_path, GGUF_DEFAULT_VAD_REPO)
        assert is_model_cached(entry, tmp_path) is True

    def test_vad_repo_override_respected(self, tmp_path):
        """extra_params.vad_repo 覆盖默认 VAD 仓时按覆盖值探测。"""
        entry = ModelEntry(
            model_id="FunAudioLLM/Fun-ASR-Nano-GGUF",
            engine_type="funasr-gguf",
            extra_params={"vad_repo": "Custom/Vad-Repo"},
        )
        _mkdir_cache(tmp_path, "FunAudioLLM/Fun-ASR-Nano-GGUF")
        _mkdir_cache(tmp_path, GGUF_DEFAULT_VAD_REPO)  # 默认仓命中不算数
        assert is_model_cached(entry, tmp_path) is False
        _mkdir_cache(tmp_path, "Custom/Vad-Repo")
        assert is_model_cached(entry, tmp_path) is True


class TestAuxModels:
    def test_missing_vad_aux_is_uncached(self, tmp_path):
        """vad_model_id 附属模型未缓存 → 整体未缓存（附属模型也触发下载）。"""
        entry = ModelEntry(
            model_id="iic/SenseVoiceSmall",
            vad_model_id="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        )
        _mkdir_cache(tmp_path, "iic/SenseVoiceSmall")
        assert is_model_cached(entry, tmp_path) is False

    def test_missing_punc_aux_is_uncached(self, tmp_path):
        entry = ModelEntry(
            model_id="iic/Paraformer",
            punc_model_id="iic/punc_ct-transformer",
        )
        _mkdir_cache(tmp_path, "iic/Paraformer")
        assert is_model_cached(entry, tmp_path) is False

    def test_all_aux_cached(self, tmp_path):
        entry = ModelEntry(
            model_id="iic/Paraformer",
            vad_model_id="iic/vad",
            punc_model_id="iic/punc",
        )
        for mid in ("iic/Paraformer", "iic/vad", "iic/punc"):
            _mkdir_cache(tmp_path, mid)
        assert is_model_cached(entry, tmp_path) is True


class TestProbeFailure:
    def test_oserror_treated_as_uncached_with_warning(self, tmp_path, monkeypatch):
        """探测 OSError（如权限）按未缓存处理，🔴 必须记 warning 不静默。"""
        from zen_vocotype_service.logging_setup import logger

        entry = ModelEntry(model_id="iic/SenseVoiceSmall")

        def _raise(self, **kwargs):
            raise OSError("权限拒绝")

        monkeypatch.setattr("pathlib.Path.is_dir", _raise)
        # loguru 不走 stdlib logging，caplog 捕获不到；挂临时 sink 断言
        messages: list[str] = []
        handler_id = logger.add(lambda m: messages.append(str(m)), level="WARNING")
        try:
            assert is_model_cached(entry, tmp_path) is False
        finally:
            logger.remove(handler_id)
        assert any("缓存探测失败" in m for m in messages)
