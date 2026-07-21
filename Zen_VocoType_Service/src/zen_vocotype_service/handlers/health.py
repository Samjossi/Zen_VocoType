"""``health`` 处理器：只答「进程活着、协议通」，不承诺可识别（协议 §3.1）。"""

from zen_vocotype_service import state as state_mod
from zen_vocotype_service.context import ServiceContext
from zen_vocotype_service.version import SERVICE_VERSION


def handle(header: dict, body: bytes, ctx: ServiceContext) -> dict:
    return {
        "status": ctx.state.status,
        "service_version": SERVICE_VERSION,
        "model_loaded": ctx.state.status == state_mod.STATUS_READY,
        "current_model": ctx.state.current_model,
    }
