"""处理器共享上下文：集中持有跨处理器依赖，避免散置全局变量。"""

from zen_vocotype_service.config import Settings
from zen_vocotype_service.state import ServiceState


class ServiceContext:
    """服务端运行时上下文（设置 / 状态 / 模型管理器 / 推理 worker）。

    ``model_manager`` 与 ``worker`` 在异步加载阶段就位前为 ``None``，
    处理器必须显式判空并返回协议错误（🔴 禁止假装可用）。
    """

    def __init__(self, settings: Settings, state: ServiceState) -> None:
        self.settings: Settings = settings
        self.state: ServiceState = state
        self.model_manager = None  # models.manager.ModelManager，T1.4 接入
        self.worker = None  # inference.worker.InferenceWorker，T1.5 接入
