"""模型注册表访问与加载来源解析（选型二/五）。

注册表数据唯一出处为 ``Settings.models``（config.yaml 内嵌）；
本模块只提供查询与来源解析，不持有第二份注册表数据。
"""

from zen_vocotype_service.config import ModelEntry, Settings


class ModelNotRegisteredError(Exception):
    """请求的模型不在注册表中。"""


def get_entry(settings: Settings, model_name: str) -> ModelEntry:
    """取注册表条目；不存在即显式报错（调用方映射协议 3001）。"""
    try:
        return settings.models[model_name]
    except KeyError:
        raise ModelNotRegisteredError(
            f"模型 {model_name!r} 不在注册表（已注册: {sorted(settings.models)}）"
        ) from None


def list_models(settings: Settings) -> list[dict]:
    """注册表全量列表（model_info 响应用，含加载来源）。"""
    return [
        {"name": name, "source": entry.source}
        for name, entry in settings.models.items()
    ]
