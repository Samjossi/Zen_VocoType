"""客户端配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（路径基于程序自身目录解析，🔴 禁止 cwd）
- 环境变量前缀：``ZEN_VOCOTYPE_CLIENT_``
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖
"""

from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
from zen_vocotype_protocol.paths import DEFAULT_SOCKET_PATH

#: 组件根目录（基于本文件自身位置解析：src/zen_vocotype_client/config.py → 组件根）
COMPONENT_ROOT: Path = Path(__file__).resolve().parents[2]

#: 默认配置文件路径
CONFIG_FILE: Path = COMPONENT_ROOT / "config.yaml"


class Settings(BaseSettings):
    """客户端全部配置项的唯一入口。"""

    model_config = SettingsConfigDict(
        env_prefix="ZEN_VOCOTYPE_CLIENT_",
        yaml_file=CONFIG_FILE,
    )

    socket_path: str = DEFAULT_SOCKET_PATH
    hotkey: str = "<ctrl>+`"
    paste_restore_delay_ms: int = 500
    log_dir: Path = COMPONENT_ROOT / "logs"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """优先级：显式入参 > 环境变量 > config.yaml > 代码默认值。"""
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
