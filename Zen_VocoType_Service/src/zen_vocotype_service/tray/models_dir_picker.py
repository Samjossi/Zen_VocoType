"""托盘「设置模型目录…」：目录校验 + 用户配置持久化（阶段 4 T4.1b）。

生效语义 v1：**保存后重启生效**——``MODELSCOPE_CACHE`` 在 main.py 首行
硬设置（顺序红线，T1.4 测试固化），运行期改目录无法对已加载模型生效；
运行期热切（重设 env + 模型重载）属 v2 增强，本阶段不做。

🔴 持久化仅写用户配置文件（XDG config 目录），禁止写包内 config.yaml
（AppImage 只读挂载）；🔴 v1 不做旧目录模型迁移/复制（README 文档化
手工步骤）；切换后目录为空走既有 ``model_download`` 通道或手工放置。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from zen_vocotype_protocol.paths import ensure_user_dir
from zen_vocotype_protocol.user_config import set_user_config_value


def validate_models_dir(path: Path) -> str | None:
    """校验自选模型目录；返回 ``None`` 表示可用，否则返回拒绝原因。

    拒绝三分支（验收标准 7，🔴 校验失败不保存且不静默）：

    - 目录不存在（GUI 选择器保证存在，本分支主要防手工改配置/竞态删除）
    - 目录不可写（不可写则模型无法下载，属不可用——拒绝并说明；
      可写性以同目录临时文件试写为准，🔴 非系统临时目录）
    - AppImage 挂载点内（只读挂载，路径重启后亦失效，🔴 必须拒绝）
    """
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return f"目录不存在：{resolved}"
    if not resolved.is_dir():
        return f"所选路径不是目录：{resolved}"
    # AppImage 运行时挂载点形如 /tmp/.mount_<名>XXXXXX 或
    # $XDG_RUNTIME_DIR/.mount_<名>XXXXXX（FUSE 只读，重启后路径漂移）
    if any(part.startswith(".mount_") for part in resolved.parts):
        return f"目录位于 AppImage 挂载点内（只读且重启后失效）：{resolved}"
    try:
        ensure_user_dir(resolved)
    except OSError as exc:
        return f"目录不可写（{exc}）：{resolved}"
    return None


def pick_and_persist_models_dir(current: Path) -> None:
    """GUI 选目录 → 校验 → 写用户配置 → 提示重启生效（全程日志，零静默）。

    :param current: 当前生效目录（对话框初始位置）
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    chosen = QFileDialog.getExistingDirectory(
        None, "选择模型目录", str(current)
    )
    if not chosen:  # 用户取消：正常路径，不记日志噪音
        return
    reason = validate_models_dir(Path(chosen))
    if reason is not None:
        logger.warning("设置模型目录被拒绝（未保存）：{}", reason)
        QMessageBox.warning(
            None, "设置模型目录", f"所选目录不可用，未保存。\n\n{reason}"
        )
        return
    cfg_path = set_user_config_value("models_dir", chosen)
    logger.info(
        "模型目录已写入用户配置（{}）：{}——将于下次启动生效", cfg_path, chosen
    )
    QMessageBox.information(
        None,
        "设置模型目录",
        f"模型目录已保存：\n{chosen}\n\n将于下次启动生效。\n"
        "（目录为空时首次使用将自动下载模型，或手工放置既有模型缓存）",
    )


__all__ = ["pick_and_persist_models_dir", "validate_models_dir"]
