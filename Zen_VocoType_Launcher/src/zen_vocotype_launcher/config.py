"""启动器配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（位置推算见契约库 ``settings.component_root``）
- 环境变量前缀：``ZEN_VOCOTYPE_LAUNCHER_``
- 配置源优先级与组件根推算的**行为逻辑单一出处**为契约库
  ``zen_vocotype_protocol.settings``，本文件仅声明字段与默认值
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖
"""

from pathlib import Path

from pydantic import Field

from zen_vocotype_protocol.paths import (
    DEFAULT_LOG_DIR,
    DEFAULT_SOCKET_PATH,
    DEV_SOCKET_PATH,
)
from zen_vocotype_protocol.settings import ComponentSettings, component_model_config, component_root

#: 组件根目录（基于本文件自身位置解析；打包形态限制见 component_root 文档）
COMPONENT_ROOT: Path = component_root(__file__)

#: 默认配置文件路径
CONFIG_FILE: Path = COMPONENT_ROOT / "config.yaml"

#: 环境变量名常量（T40 托盘设置项的环境变量覆盖警示用；
#: 🔴 与 Client T33/T35 同模式，禁止散落硬编码）
SERVICE_START_DELAY_ENV_VAR = "ZEN_VOCOTYPE_LAUNCHER_SERVICE_START_DELAY_S"
CLIENT_START_INTERVAL_ENV_VAR = "ZEN_VOCOTYPE_LAUNCHER_CLIENT_START_INTERVAL_S"
AUTO_EXIT_DELAY_ENV_VAR = "ZEN_VOCOTYPE_LAUNCHER_AUTO_EXIT_DELAY_S"
SERVICE_BINARY_ENV_VAR = "ZEN_VOCOTYPE_LAUNCHER_SERVICE_BINARY"
CLIENT_BINARY_ENV_VAR = "ZEN_VOCOTYPE_LAUNCHER_CLIENT_BINARY"


class Settings(ComponentSettings):
    """启动器全部配置项的唯一入口。

    数值型字段以 ``Field(gt=0)`` 启动校验——非法值在 ``Settings()`` 构造期
    即抛 ``ValidationError``（🔴 禁止运行期才暴露配置错误）；入口捕获后
    以退出码 5（配置/路径错误）终止。
    """

    model_config = component_model_config(__file__, "ZEN_VOCOTYPE_LAUNCHER_")

    socket_path: str = DEFAULT_SOCKET_PATH
    dev_socket_path: str = DEV_SOCKET_PATH
    #: 日志目录。默认 XDG 状态目录（契约库唯一出处）；🔴 禁止组件根目录内
    #: （AppImage 只读挂载点写入必失败，阶段 4 T4.1 整改）
    log_dir: Path = DEFAULT_LOG_DIR

    #: 阶段一等待上限（秒）：Socket 可连接。
    #: 依据：阶段 1 验收冷启动 Socket 可连 ≤5s 的 3 倍余量
    socket_wait_timeout_s: float = Field(default=15.0, gt=0)

    #: 阶段二等待上限（秒）：模型就绪（ready 接口确认）。
    #: 回填校准：阶段 3 dev P99 8.5s；阶段 4 打包 AppImage P99 12.6s（N=5，
    #: 缓存模型），首启下载实测 132s——180s 同时覆盖缓存与首启下载场景，保持
    model_ready_timeout_s: float = Field(default=180.0, gt=0)

    #: ready 轮询间隔（毫秒）。
    #: 依据：就绪信号精度要求低，200ms 兼顾响应速度与 Socket 压力
    ready_poll_interval_ms: int = Field(default=200, gt=0)

    #: 进程组两段式终止的 SIGTERM→SIGKILL 宽限（秒）
    terminate_grace_seconds: float = Field(default=5.0, gt=0)

    #: 服务端/客户端二进制显式路径（正式模式）。
    #: 默认 None = 按邻接目录约定自动解析（targets.py）；配置可显式覆盖
    service_binary: str | None = None
    client_binary: str | None = None

    #: 服务端启动延迟（秒）：Launcher 启动后多少秒才拉起服务端（T40）。
    #: 默认 0 = 立即拉起；倒计时期间托盘进度行按秒刷新（🔴 不做无声等待）
    service_start_delay_s: float = Field(default=0.0, ge=0, le=300)

    #: 客户端启动间隔（秒）：服务端拉起后隔多少秒再拉起客户端（T40）。
    #: 客户端拉起门控为固定间隔（Client 懒连接，先拉起无害）；就绪等待
    #: 保留为两端拉起后的整体成败判定，🔴 间隔不替代就绪判定
    client_start_interval_s: float = Field(default=0.0, ge=0, le=300)

    #: 成功后自动退出延迟（秒，T40 托盘模式专属）：编排成功后托盘的观察
    #: 停留窗口，0 = 立即退出；🔴 失败路径不自动退出（防静默失败）。
    #: CLI（--no-tray）模式无视本字段（CLI 本就跑完即退）
    auto_exit_delay_s: float = Field(default=5.0, ge=0, le=60)
