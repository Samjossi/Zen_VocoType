"""三组件配置公共基类（行为逻辑单一出处）。

三组件文件夹之间禁止互相 import（大纲原则 7），本模块是配置行为逻辑的
唯一共享位置：固定的配置源优先级与组件根目录推算。各组件 ``Settings``
继承 ``ComponentSettings``，仅声明 ``env_prefix``、配置字段与默认值。

🔴 禁止三组件各自重复实现 ``settings_customise_sources``——旧 GridChat
两端各定义一份导致漂移为反面案例。
"""

from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def component_root(anchor_file: str) -> Path:
    """由组件包内文件位置推算组件根目录（``src/<包>/<文件>.py`` → 组件根）。

    ⚠️ 已知限制：本推算仅对**源码布局**成立。组件包被 pip 安装或经
    PyInstaller 打包后 ``__file__`` 指向 site-packages / ``_MEIPASS``
    内部，组件根定位将失效——打包形态下必须改经「程序自身目录」
    helper（兼容 ``_MEIPASS``）解析，属阶段 1 resource_locator 职责。

    :param anchor_file: 调用方传入本模块文件的 ``__file__``
    """
    return Path(anchor_file).resolve().parents[2]


def component_model_config(anchor_file: str, env_prefix: str) -> SettingsConfigDict:
    """生成组件 ``Settings.model_config``：固定 YAML 配置文件位置与环境变量前缀。

    配置文件固定为组件根目录 ``config.yaml``（基于程序自身目录解析，🔴 禁止 cwd）。
    """
    return SettingsConfigDict(
        env_prefix=env_prefix,
        yaml_file=component_root(anchor_file) / "config.yaml",
    )


class ComponentSettings(BaseSettings):
    """三组件配置基类：固定配置源优先级（单一出处）。

    优先级：显式入参 > 环境变量 > config.yaml > 代码默认值（dotenv 有意弃用）。
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
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
