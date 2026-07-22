"""拉起目标解析（选型九：正式/dev 三处差异替换的唯一位置）。

模式差异仅限「目标命令 + Socket 路径 + 锁文件」三处，编排流程零分支：

- dev：自定位解析仓库根与 ``.venv``，以 ``python main.py`` 绝对路径拉起
  两端源码；Socket = 契约库 ``DEV_SOCKET_PATH``（唯一出处）；锁 = dev 锁；
  向子进程注入 Socket 覆盖环境变量（pydantic-settings 环境变量机制）
- 正式：配置显式路径（``service_binary``/``client_binary``）→ Launcher
  自身同目录邻接约定（AppImage/onedir）；缺失明确报错

🔴 全部路径基于程序自身位置解析，禁止 cwd 相对解析。
"""

import os
import sys
from pathlib import Path

from zen_vocotype_protocol.paths import (
    CLIENT_LOCK_PATH,
    DEV_CLIENT_LOCK_PATH,
    DEV_SERVICE_LOCK_PATH,
    DEV_SOCKET_PATH,
    SERVICE_LOCK_PATH,
)

from zen_vocotype_launcher.config import Settings
from zen_vocotype_launcher.orchestrator import ComponentTarget, LaunchPlan


class TargetResolutionError(Exception):
    """目标解析失败（组件缺失/仓库布局不符；退出码 5）。"""


def _repo_root() -> Path:
    """由本文件位置解析仓库根（``Zen_VocoType_Launcher/src/<pkg>/targets.py``
    → 上四级）。开发目录布局固定，打包形态下 dev 模式不可用（正式模式
    不走本函数）。"""
    return Path(__file__).resolve().parents[3]


def _child_env(socket_path: str) -> dict[str, str]:
    """dev 子进程环境：注入 Socket 路径覆盖（两端各自的环境变量前缀）。"""
    env = dict(os.environ)
    env["ZEN_VOCOTYPE_SERVICE_SOCKET_PATH"] = socket_path
    env["ZEN_VOCOTYPE_CLIENT_SOCKET_PATH"] = socket_path
    return env


def _dev_target(
    *,
    name: str,
    component_dir: Path,
    python: Path,
    lock_path: str,
    log_path: Path,
    env: dict[str, str],
) -> ComponentTarget:
    main_py = component_dir / "main.py"
    if not main_py.is_file():
        raise TargetResolutionError(f"{name} 入口缺失：{main_py}")
    return ComponentTarget(
        name=name,
        argv=[str(python), str(main_py)],
        lock_path=lock_path,
        log_path=log_path,
        # dev 模式 exe 为解释器，辅以 cmdline 主脚本路径校验（discovery 选型四）
        expected_exe=os.path.realpath(python),
        expected_cmdline_fragment=str(main_py),
        env=env,
    )


def _build_dev_plan(settings: Settings) -> LaunchPlan:
    repo = _repo_root()
    python = repo / ".venv" / "bin" / "python"
    if not python.is_file():
        raise TargetResolutionError(f"dev 模式 .venv 缺失：{python}")
    socket_path = settings.dev_socket_path
    env = _child_env(socket_path)
    log_dir = Path(settings.log_dir)
    return LaunchPlan(
        mode="dev",
        socket_path=socket_path,
        service=_dev_target(
            name="service",
            component_dir=repo / "Zen_VocoType_Service",
            python=python,
            lock_path=DEV_SERVICE_LOCK_PATH,
            log_path=log_dir / "child_service.log",
            env=env,
        ),
        client=_dev_target(
            name="client",
            component_dir=repo / "Zen_VocoType_Client",
            python=python,
            lock_path=DEV_CLIENT_LOCK_PATH,
            log_path=log_dir / "child_client.log",
            env=env,
        ),
    )


def _launcher_dir() -> Path:
    """Launcher 自身所在目录（邻接约定的基准）。

    双形态解析（阶段 4 T4.4 联调落地）：

    - AppImage：``sys.argv[0]`` 指向 FUSE 挂载点内的载荷二进制，挂载点
      无邻接意义且只读——须以 AppImage runtime 注入的 ``APPIMAGE`` 环境
      变量取 .AppImage 文件自身所在目录
    - onedir / 源码：``sys.argv[0]`` 解析
    """
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return Path(appimage).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def _resolve_prod_binary(
    explicit: str | None, *, name: str, sibling_names: tuple[str, ...]
) -> Path:
    """正式模式二进制解析：配置显式路径 → 邻接目录约定。"""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_absolute():
            raise TargetResolutionError(f"{name}_binary 必须为绝对路径：{explicit}")
        if not path.exists():
            raise TargetResolutionError(f"{name} 显式配置的二进制不存在：{path}")
        return path
    base = _launcher_dir()
    for sibling in sibling_names:
        candidate = base / sibling
        if candidate.exists():
            return candidate
    raise TargetResolutionError(
        f"未找到 {name} 二进制：配置未显式指定，且邻接目录 {base} 无 {sibling_names}"
    )


def _prod_target(
    *,
    name: str,
    binary: Path,
    lock_path: str,
    log_path: Path,
) -> ComponentTarget:
    return ComponentTarget(
        name=name,
        argv=[str(binary)],
        lock_path=lock_path,
        log_path=log_path,
        expected_exe=str(binary),
    )


def _build_prod_plan(settings: Settings) -> LaunchPlan:
    # 邻接约定回填真产物（阶段 4 T4.4）：AppImage 单文件优先，onedir 目录
    # 形式为「目录名/同名二进制」（tools/build.py 产物布局）
    service_bin = _resolve_prod_binary(
        settings.service_binary,
        name="service",
        sibling_names=(
            "Zen_VocoType_Service.AppImage",
            "zen_vocotype_service/zen_vocotype_service",
        ),
    )
    client_bin = _resolve_prod_binary(
        settings.client_binary,
        name="client",
        sibling_names=(
            "Zen_VocoType_Client.AppImage",
            "zen_vocotype_client/zen_vocotype_client",
        ),
    )
    log_dir = Path(settings.log_dir)
    return LaunchPlan(
        mode="prod",
        socket_path=settings.socket_path,
        service=_prod_target(
            name="service",
            binary=service_bin,
            lock_path=SERVICE_LOCK_PATH,
            log_path=log_dir / "child_service.log",
        ),
        client=_prod_target(
            name="client",
            binary=client_bin,
            lock_path=CLIENT_LOCK_PATH,
            log_path=log_dir / "child_client.log",
        ),
    )


def build_plan(settings: Settings, *, dev_mode: bool) -> LaunchPlan:
    """按模式构建编排计划（三处差异替换的唯一入口）。"""
    if dev_mode:
        return _build_dev_plan(settings)
    return _build_prod_plan(settings)
