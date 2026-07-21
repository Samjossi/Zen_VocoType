"""全局关键路径默认值（唯一出处）。

重写大纲 §5-9：Socket 路径全局唯一出处；各组件配置仅允许覆盖，不允许另立源头。
路径解析一律基于程序自身目录或显式配置，🔴 禁止 cwd 相对路径。

Socket 路径安全约定（v1 强制项，服务端 bind 时必须执行）：

1. 默认路径位于**用户私有运行目录**（``$XDG_RUNTIME_DIR``，回退 ``~/.local/run``），
   🔴 禁止默认使用全局可写的 ``/tmp``——可预测的 /tmp 路径在多用户系统上可被
   抢先 bind 冒充服务端（语音 PCM 泄露/结果伪造），/tmp 仅允许显式配置覆盖
2. bind 前校验目标路径非符号链接、属主为自身
3. bind 后显式 ``chmod 0600``
4. 以 ``SO_PEERCRED`` 校验对端 UID，仅允许同 UID 连接（违规响应
   ``errors.ERR_UNAUTHORIZED_PEER``）
"""

import os
from pathlib import Path


def _default_runtime_dir() -> Path:
    """返回用户私有运行目录：优先 ``$XDG_RUNTIME_DIR``，回退 ``~/.local/run``。"""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime)
    return Path.home() / ".local" / "run"


#: 用户私有运行目录（导入期解析一次）
DEFAULT_RUNTIME_DIR: Path = _default_runtime_dir()

#: 生产环境默认 Socket 路径（组件配置可覆盖，但默认值只在此定义一次）
DEFAULT_SOCKET_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype.sock")

#: 开发调试专用 Socket 路径（Launcher --dev 模式使用，与正式版隔离；
#: 继承旧 start_dev_debug.sh 的隔离思路）
DEV_SOCKET_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_dev.sock")

# ---------------------------------------------------------------------------
# 音频格式约定（recognize 请求的默认 PCM 参数，与选型 3 一致）
# ---------------------------------------------------------------------------

#: 采样率 Hz
DEFAULT_SAMPLE_RATE: int = 16000

#: 声道数
DEFAULT_CHANNELS: int = 1

#: 采样位宽（字节），16bit = 2
DEFAULT_SAMPLE_WIDTH: int = 2
