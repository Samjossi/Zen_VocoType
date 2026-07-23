"""Zen_VocoType_Launcher 入口。

用法：

- ``python main.py``            托盘模式：系统托盘图标 + 右键菜单（设置/观察），
  编排成功后自行退出；无显示环境自动回退一次性 CLI（T40）
- ``python main.py --no-tray``  一次性 CLI：拉起打包形态的服务端与客户端后退出
- ``python main.py --dev``      开发模式：用当前 ``.venv`` 拉起两端源码
  （Socket/锁文件与正式版隔离，契约库 ``DEV_SOCKET_PATH`` 唯一出处）

退出码（``exit_codes.ExitCode``，README 故障排查节一一对应）：
0 成功（含幂等命中）；2 已在运行或既有实例异常；3 服务端拉起/就绪失败；
4 客户端拉起失败；5 配置/路径错误；6 内部错误。
"""

import argparse
import sys

from pydantic import ValidationError


def _run_cli(settings, *, dev_mode: bool, log_file) -> int:
    """一次性 CLI 路径（--dev / --no-tray / 托盘回退共用）：编排后即退出。"""
    from loguru import logger

    from zen_vocotype_launcher.exit_codes import ExitCode
    from zen_vocotype_launcher.locks import lock_path_for
    from zen_vocotype_launcher.orchestrator import OrchestratorDeps, run
    from zen_vocotype_launcher.targets import TargetResolutionError, build_plan

    try:
        plan = build_plan(settings, dev_mode=dev_mode)
    except TargetResolutionError as exc:
        logger.error("目标解析失败：{}", exc)
        return int(ExitCode.CONFIG_ERROR)

    try:
        code = run(
            plan,
            settings,
            lock_path_for(dev_mode),
            deps=OrchestratorDeps(log_file=log_file),
        )
    except Exception:  # 未预期异常兜底：🔴 禁止静默成功
        logger.exception("Launcher 内部错误")
        return int(ExitCode.INTERNAL_ERROR)
    return int(code)


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
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="一次性 CLI 模式：不显示系统托盘，编排完成后即退出",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本并退出（构建冒烟探针，阶段 4 T4.2）",
    )
    args = parser.parse_args()

    # --version 须在配置/日志初始化前可答、零写盘
    # （🔴 托盘相关 import 一律延迟到托盘分支内，本探针不得触达 PySide6）
    if args.version:
        from loguru import logger

        from zen_vocotype_launcher.version import LAUNCHER_VERSION

        logger.info("Zen_VocoType_Launcher v{}", LAUNCHER_VERSION)
        return 0

    from loguru import logger

    from zen_vocotype_launcher.config import Settings
    from zen_vocotype_launcher.exit_codes import ExitCode
    from zen_vocotype_launcher.logging_setup import setup_logging

    try:
        settings = Settings()
    except ValidationError as exc:
        # 配置非法时 log_dir 未知，无法初始化文件 sink；
        # 经 loguru 默认 stderr sink 报错（🔴 全组件禁 print）
        from loguru import logger

        logger.error("配置校验失败：{}", exc)
        return int(ExitCode.CONFIG_ERROR)

    log_file = setup_logging(settings.log_dir)

    # dev 模式维持一次性 CLI（T40 边界：托盘设置项对 dev 不生效）
    if args.dev:
        logger.info("Launcher 启动（模式：dev）")
        return _run_cli(settings, dev_mode=True, log_file=log_file)

    if args.no_tray:
        logger.info("Launcher 启动（模式：prod，一次性 CLI）")
        return _run_cli(settings, dev_mode=False, log_file=log_file)

    # 默认托盘模式；无显示环境/创建失败回退一次性 CLI（🔴 禁止静默回退）
    logger.info("Launcher 启动（模式：prod，托盘）")
    try:
        from zen_vocotype_launcher.app import run_tray_mode

        return run_tray_mode(settings, log_file)
    except Exception as exc:
        # TrayUnavailableError（无显示/创建失败）与未预期异常均回退；
        # 回退后走 CLI 完整编排，功能不受影响（仅无托盘可视化）
        logger.warning("托盘模式不可用（{}），回退一次性 CLI 模式", exc)
        return _run_cli(settings, dev_mode=False, log_file=log_file)


if __name__ == "__main__":
    sys.exit(main())
