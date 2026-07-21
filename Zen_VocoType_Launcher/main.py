"""Zen_VocoType_Launcher 入口（骨架）。

🔴 本文件为阶段 0 骨架，业务实现属阶段 3（按序拉起、就绪等待、单实例锁、
失败清理、--dev 模式、notify-send 通知）。
"""

import sys


def main() -> int:
    """入口函数；骨架阶段明确返回非零并说明原因（🔴 禁止静默/假成功）。"""
    print(
        "Zen_VocoType_Launcher 骨架：业务实现属阶段 3，当前不可启动。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
