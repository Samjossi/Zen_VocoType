"""三组件配置公共基类（行为逻辑单一出处）。

三组件文件夹之间禁止互相 import（大纲原则 7），本模块是配置行为逻辑的
唯一共享位置：固定的配置源优先级与组件根目录推算。各组件 ``Settings``
继承 ``ComponentSettings``，仅声明 ``env_prefix``、配置字段与默认值。

🔴 禁止三组件各自重复实现 ``settings_customise_sources``——旧 GridChat
两端各定义一份导致漂移为反面案例。
"""

import sys
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .user_config import load_user_config


def component_root(anchor_file: str) -> Path:
    """由组件包内文件位置推算组件根目录（``src/<包>/<文件>.py`` → 组件根）。

    ⚠️ 本推算仅对**源码布局**成立；PyInstaller 打包形态下 ``__file__``
    指向 ``_MEIPASS`` 内部虚拟路径，组件根定位不适用——打包形态的配置
    文件定位见 ``component_model_config`` 的 ``_MEIPASS`` 分支
    （阶段 4 T4.2 落地）。

    :param anchor_file: 调用方传入本模块文件的 ``__file__``
    """
    return Path(anchor_file).resolve().parents[2]


def component_model_config(anchor_file: str, env_prefix: str) -> SettingsConfigDict:
    """生成组件 ``Settings.model_config``：固定 YAML 配置文件位置与环境变量前缀。

    配置文件双环境解析（基于程序自身目录，🔴 禁止 cwd）：

    - 源码布局：组件根目录 ``config.yaml``
    - PyInstaller 打包：``_MEIPASS/config.yaml``（onedir 即 ``_internal/`` 根，
      由 spec ``datas`` 收编——包内默认配置只读随包，运行时持久化走用户
      配置文件层，见 ``user_config``）
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        yaml_path = Path(meipass) / "config.yaml"
    else:
        yaml_path = component_root(anchor_file) / "config.yaml"
    return SettingsConfigDict(
        env_prefix=env_prefix,
        yaml_file=yaml_path,
    )


class _UserConfigSettingsSource(PydanticBaseSettingsSource):
    """用户配置文件源（阶段 4 T4.1b）：XDG config 目录共享单文件。

    仅拾取本组件 ``Settings`` 已声明字段（他组件键自动忽略）；文件缺失/
    损坏语义由契约库 ``user_config.load_user_config`` 保证（回退空 +
    warning，🔴 不静默、不崩溃）。
    """

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._values = {
            key: value
            for key, value in load_user_config().items()
            if key in settings_cls.model_fields
        }

    def get_field_value(self, field, field_name: str):  # noqa: ANN001
        return self._values.get(field_name), field_name, False

    def __call__(self) -> dict:
        return dict(self._values)


class ComponentSettings(BaseSettings):
    """三组件配置基类：固定配置源优先级（单一出处）。

    优先级：显式入参 > 环境变量 > 用户配置文件 > 包内 config.yaml >
    代码默认值（dotenv 有意弃用）。用户配置文件层见契约库 ``user_config``
    （阶段 4 T4.1b；AppImage 只读挂载下运行时持久化的唯一合法落点）。
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _UserConfigSettingsSource(settings_cls),
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
