"""``model_info`` 处理器（协议 §3.4）：当前模型 + 注册表全量列表。

模型切换后的交叉验证手段；加载中（manager 未就位）也可查询注册表。
"""

from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.models.registry import list_models


def handle(header: dict, body: bytes, ctx: ServiceContext) -> dict:
    if ctx.model_manager is not None:
        return ctx.model_manager.model_info()
    # 启动早期 manager 未就位：返回注册表静态信息
    return {
        "current_model": None,
        "available_models": [
            {**item, "loaded": False} for item in list_models(ctx.settings)
        ],
    }
