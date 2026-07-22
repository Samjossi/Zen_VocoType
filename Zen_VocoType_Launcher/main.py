"""Zen_VocoType_Launcher 入口。

用法：

- ``python main.py``         正式模式：拉起打包形态的服务端与客户端
- ``python main.py --dev``   开发模式：用当前 ``.venv`` 拉起两端源码
  （Socket/锁文件与正式版隔离，契约库 ``DEV_SOCKET_PATH`` 唯一出处）

退出码（``exit_codes.ExitCode``，README 故障排查节一一对应）：
0 成功（含幂等命中）；2 已在运行或既有实例异常；3 服务端拉起/就绪失败；
4 客户端拉起失败；5 配置/路径错误；6 内部错误。
"""

import argparse
import sys

from pydantic import ValidationError


def main() -> int:
    """入口函数（单一出口，🔴 禁止散落 sys.exit 字面量）。"""
    parser = argparse.ArgumentParser(
        prog="zen-vocotype-launcher",
        description="Zen_VocoType 启动器：拉起服务端与客户端并等待就绪",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="开发模式：用 .venv 拉起两端源码（Socket/锁与正式版隔离）",
    )
    args = parser.parse_args()

    from loguru import logger

    from zen_vocotype_launcher.config import Settings
    from zen_vocotype_launcher.exit_codes import ExitCode
    from zen_vocotype_launcher.locks import lock_path_for
    from zen_vocotype_launcher.logging_setup import setup_logging
    from zen_vocotype_launcher.orchestrator import OrchestratorDeps, run
    from zen_vocotype_launcher.targets import TargetResolutionError, build_plan

    try:
        settings = Settings()
    except ValidationError as exc:
        # 配置非法时 log_dir 未知，无法初始化文件 sink；
        # 经 loguru 默认 stderr sink 报错（🔴 全组件禁 print）
        from loguru import logger

        logger.error("配置校验失败：{}", exc)
        return int(ExitCode.CONFIG_ERROR)

    log_file = setup_logging(settings.log_dir)
    logger.info("Launcher 启动（模式：{}）", "dev" if args.dev else "prod")

    try:
        plan = build_plan(settings, dev_mode=args.dev)
    except TargetResolutionError as exc:
        logger.error("目标解析失败：{}", exc)
        return int(ExitCode.CONFIG_ERROR)

    try:
        code = run(
            plan,
            settings,
            lock_path_for(args.dev),
            deps=OrchestratorDeps(log_file=log_file),
        )
    except Exception as exc:  # 未预期异常兜底：🔴 禁止静默成功
        logger.exception("Launcher 内部错误")
        return int(ExitCode.INTERNAL_ERROR)
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
