"""启动器配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（位置推算见契约库 ``settings.component_root``）
- 环境变量前缀：``ZEN_VOCOTYPE_LAUNCHER_``
- 配置源优先级与组件根推算的**行为逻辑单一出处**为契约库
  ``zen_vocotype_protocol.settings``，本文件仅声明字段与默认值
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖
"""

from pathlib import Path

from zen_vocotype_protocol.paths import DEFAULT_SOCKET_PATH, DEV_SOCKET_PATH
from zen_vocotype_protocol.settings import ComponentSettings, component_model_config, component_root

#: 组件根目录（基于本文件自身位置解析；打包形态限制见 component_root 文档）
COMPONENT_ROOT: Path = component_root(__file__)

#: 默认配置文件路径
CONFIG_FILE: Path = COMPONENT_ROOT / "config.yaml"


class Settings(ComponentSettings):
    """启动器全部配置项的唯一入口。"""

    model_config = component_model_config(__file__, "ZEN_VOCOTYPE_LAUNCHER_")

    socket_path: str = DEFAULT_SOCKET_PATH
    dev_socket_path: str = DEV_SOCKET_PATH
    log_dir: Path = COMPONENT_ROOT / "logs"
