"""T4.1b 单元测试：用户配置文件层与配置链优先级（阶段 4 选型十）。

覆盖：读写回环、损坏回退 + warning（不静默不崩溃）、原子写无残留、
配置链优先级（用户配置 > 包内 yaml、环境变量 > 用户配置）、
跨组件键过滤（共享单文件下他组件键不干扰）。
"""

import warnings

import pytest
from pydantic_settings import SettingsConfigDict

from zen_vocotype_protocol import paths
from zen_vocotype_protocol.settings import ComponentSettings
from zen_vocotype_protocol.user_config import (
    load_user_config,
    set_user_config_value,
    write_user_config,
)


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    """默认用户配置路径指向临时目录（动态解析，monkeypatch 即生效）。"""
    path = tmp_path / "zen_vocotype" / "user_config.yaml"
    monkeypatch.setattr(paths, "DEFAULT_USER_CONFIG_PATH", path)
    return path


class TestReadWrite:
    def test_missing_file_returns_empty(self, cfg_path):
        assert load_user_config() == {}

    def test_roundtrip(self, cfg_path):
        write_user_config({"models_dir": "/data/models", "x": 1})
        assert load_user_config() == {"models_dir": "/data/models", "x": 1}

    def test_set_value_merges_existing(self, cfg_path):
        set_user_config_value("a", 1)
        set_user_config_value("b", "two")
        assert load_user_config() == {"a": 1, "b": "two"}

    def test_corrupt_file_falls_back_with_warning(self, cfg_path):
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("key: [unclosed\n  - {bad", encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert load_user_config() == {}
        assert any("损坏" in str(w.message) for w in caught)

    def test_non_mapping_top_level_falls_back_with_warning(self, cfg_path):
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert load_user_config() == {}
        assert any("非映射" in str(w.message) for w in caught)

    def test_atomic_write_leaves_no_temp_residue(self, cfg_path):
        write_user_config({"k": "v"})
        siblings = [p.name for p in cfg_path.parent.iterdir()]
        assert siblings == [cfg_path.name]


class _DemoSettings(ComponentSettings):
    """配置链测试用最小 Settings（字段：models_dir / level）。"""

    model_config = SettingsConfigDict(env_prefix="ZEN_DEMO_")

    models_dir: str = "code-default"
    level: int = 0


class _DemoWithYaml(ComponentSettings):
    """带包内 yaml 的配置链测试 Settings（yaml_file 由测试注入）。"""

    models_dir: str = "code-default"
    level: int = 0


class TestConfigChain:
    def test_user_config_beats_package_yaml(self, cfg_path, tmp_path, monkeypatch):
        pkg_yaml = tmp_path / "pkg.yaml"
        pkg_yaml.write_text("models_dir: /pkg/dir\nlevel: 1\n", encoding="utf-8")
        set_user_config_value("models_dir", "/user/dir")
        monkeypatch.setattr(
            _DemoWithYaml,
            "model_config",
            SettingsConfigDict(yaml_file=str(pkg_yaml)),
        )
        s = _DemoWithYaml()
        assert s.models_dir == "/user/dir"  # 用户配置 > 包内 yaml
        assert s.level == 1  # 用户配置未覆盖的项仍取包内 yaml

    def test_env_beats_user_config(self, cfg_path, monkeypatch):
        set_user_config_value("models_dir", "/user/dir")
        monkeypatch.setenv("ZEN_DEMO_MODELS_DIR", "/env/dir")
        s = _DemoSettings()
        assert s.models_dir == "/env/dir"  # 环境变量 > 用户配置

    def test_package_yaml_only_when_no_user_config(self, cfg_path, tmp_path, monkeypatch):
        pkg_yaml = tmp_path / "pkg.yaml"
        pkg_yaml.write_text("models_dir: /pkg/dir\n", encoding="utf-8")
        monkeypatch.setattr(
            _DemoWithYaml,
            "model_config",
            SettingsConfigDict(yaml_file=str(pkg_yaml)),
        )
        assert _DemoWithYaml().models_dir == "/pkg/dir"

    def test_foreign_component_keys_ignored(self, cfg_path):
        """共享单文件下他组件键（未声明字段）自动忽略，不报错。"""
        set_user_config_value("some_other_component_key", 123)
        set_user_config_value("models_dir", "/user/dir")
        s = _DemoSettings()
        assert s.models_dir == "/user/dir"
        assert not hasattr(s, "some_other_component_key")
