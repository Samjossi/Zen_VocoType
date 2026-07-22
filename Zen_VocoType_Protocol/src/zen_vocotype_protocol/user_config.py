"""用户配置文件层（阶段 4 T4.1b，行为逻辑唯一出处）。

配置链（优先级低 → 高）：组件默认值（契约库 ``paths``/各组件 ``config.py``）
→ 包内 ``config.yaml`` → **用户配置文件** → 环境变量。

设计约束：

- 用户配置文件固定为 ``paths.DEFAULT_USER_CONFIG_PATH``
  （``$XDG_CONFIG_HOME/zen_vocotype/user_config.yaml``），三组件共享单文件，
  各组件 ``Settings`` 仅拾取自身已声明字段（他组件键自动忽略）
- 🔴 禁止写包内 ``config.yaml``——AppImage 只读挂载点，写包内配置即
  下一个「路径失效事故」
- 写入一律原子写（同目录临时文件 + ``os.replace``，🔴 禁止系统临时目录），
  防半截文件
- 文件损坏/解析失败时回退默认值（视为空）并以 ``warnings`` 记 warning
  （🔴 禁止静默丢弃、🔴 禁止崩溃）；契约库零三方日志依赖，warning 走
  stderr，由各组件入口在日志就绪后转述（见 Service ``main.py``）
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import yaml

from . import paths


def _resolve_path(path: Path | None) -> Path:
    """未显式传参时动态解析默认路径（测试可 monkeypatch ``paths`` 常量）。"""
    return path if path is not None else paths.DEFAULT_USER_CONFIG_PATH


def load_user_config(path: Path | None = None) -> dict:
    """读取用户配置文件为 dict；缺失返回空，损坏回退空并记 warning。

    :param path: 配置文件路径（None = 契约库默认路径）
    :return: 顶层映射（非映射内容视为损坏）
    """
    cfg_path = _resolve_path(path)
    if not cfg_path.exists():
        return {}
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        warnings.warn(f"用户配置文件损坏，回退默认值（{cfg_path}）：{exc}")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        warnings.warn(f"用户配置文件顶层非映射，回退默认值（{cfg_path}）")
        return {}
    return data


def write_user_config(data: dict, path: Path | None = None) -> Path:
    """原子写入用户配置文件（同目录临时文件 + ``os.replace``）。

    :param data: 完整配置映射（调用方负责合并既有内容）
    :param path: 配置文件路径（None = 契约库默认路径）
    :return: 实际写入路径
    """
    cfg_path = _resolve_path(path)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cfg_path.with_name(cfg_path.name + ".tmp")
    tmp_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp_path, cfg_path)
    return cfg_path


def set_user_config_value(key: str, value, path: Path | None = None) -> Path:
    """合并写入单个配置项（读-改-写，原子落盘）。

    既有文件损坏时视为空重新起步（warning 由 ``load_user_config`` 发出，
    🔴 不静默）；仅承载覆盖项，不做全量配置导出（阶段 4 边界 9）。

    :return: 实际写入路径
    """
    data = load_user_config(path)
    data[key] = value
    return write_user_config(data, path)


__all__ = ["load_user_config", "set_user_config_value", "write_user_config"]
