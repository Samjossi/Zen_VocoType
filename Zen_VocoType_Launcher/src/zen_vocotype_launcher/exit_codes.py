"""Launcher 退出码枚举（选型六：语义化退出码 + 单一出口）。

🔴 禁止散落的 ``sys.exit`` 字面量：所有失败分支经 ``main() -> int``
单一出口返回，README 故障排查节与本枚举一一对应。
"""

from enum import IntEnum


class ExitCode(IntEnum):
    #: 成功拉起全套（或幂等命中并确认就绪）
    SUCCESS = 0

    #: 已在运行（Launcher 锁冲突）或既有实例异常（幂等命中后就绪等待失败）
    ALREADY_RUNNING = 2

    #: 服务端拉起失败 / 就绪等待失败（含超时、进程等待期死亡、协议版本不符）
    SERVICE_FAILED = 3

    #: 客户端拉起失败（含拉起后立即退出）
    CLIENT_FAILED = 4

    #: 配置/路径错误：组件缺失、目标解析失败、Socket 被外部占用、配置非法
    CONFIG_ERROR = 5

    #: 内部错误（未预期异常兜底）
    INTERNAL_ERROR = 6
