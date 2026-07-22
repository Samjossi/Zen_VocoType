"""版本一致性：本地出处必须与仓库根 versions.toml 同步（防漂移红线）。

仓库被单组件独立 clone（无 ../../versions.toml）时显式 skip，
避免阻塞组件独立开发。
"""

import tomllib
from pathlib import Path

import pytest

VERSIONS_TOML = Path(__file__).resolve().parents[2] / "versions.toml"


def _root_version(component: str) -> str:
    if not VERSIONS_TOML.exists():
        pytest.skip(f"仓库根 versions.toml 不存在（组件独立克隆场景）: {VERSIONS_TOML}")
    with VERSIONS_TOML.open("rb") as f:
        return tomllib.load(f)["components"][component]


def test_client_version_matches_root() -> None:
    from zen_vocotype_client import __version__

    assert __version__ == _root_version("client")
