"""版本一致性：本地出处必须与仓库根 versions.toml 同步（防漂移红线）。

另含协议版本两段式解析与兼容性比对的红线用例。
仓库被单组件独立 clone（无 ../../versions.toml）时显式 skip，
避免阻塞组件独立开发。
"""

import tomllib
from pathlib import Path

import pytest
from zen_vocotype_protocol.version import (
    PROTOCOL_VERSION,
    is_compatible,
    parse_version,
)

VERSIONS_TOML = Path(__file__).resolve().parents[2] / "versions.toml"


def _root_version(component: str) -> str:
    if not VERSIONS_TOML.exists():
        pytest.skip(f"仓库根 versions.toml 不存在（组件独立克隆场景）: {VERSIONS_TOML}")
    with VERSIONS_TOML.open("rb") as f:
        return tomllib.load(f)["components"][component]


def test_protocol_version_matches_root() -> None:
    assert PROTOCOL_VERSION == _root_version("protocol")


def test_parse_version_two_part() -> None:
    assert parse_version("1.0") == (1, 0)
    assert parse_version("12.34") == (12, 34)


@pytest.mark.parametrize("bad", ["1.0.0", "1", "a.b", "1.0.0.0", "", "1..0"])
def test_parse_version_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_version(bad)


def test_is_compatible() -> None:
    assert is_compatible("1.0", "1.0")
    assert not is_compatible("1.0", "1.1")
    assert not is_compatible("1.0", "2.0")
