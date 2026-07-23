"""客户端配置（单一配置源，选型 7 定稿）。

每组件恰好一个 ``Settings`` 类 + 一个 ``config.yaml`` + 环境变量覆盖：

- 配置文件：组件根目录 ``config.yaml``（位置推算见契约库 ``settings.component_root``）
- 环境变量前缀：``ZEN_VOCOTYPE_CLIENT_``
- 配置源优先级与组件根推算的**行为逻辑单一出处**为契约库
  ``zen_vocotype_protocol.settings``，本文件仅声明字段与默认值
- Socket 路径默认值唯一出处为契约库 ``zen_vocotype_protocol.paths``，此处仅允许覆盖

启动校验（🔴 禁止运行期才暴露配置错误，阶段 2 选型三/五）：
hotkey 表达式合法性、延迟/上限数值范围在 ``validate_startup`` 集中执行。
"""

from pathlib import Path

from pydantic import Field, field_validator

from zen_vocotype_protocol.paths import (
    DEFAULT_LOG_DIR,
    DEFAULT_SOCKET_PATH,
    ensure_user_dir,
    get_recordings_dir,
)
from zen_vocotype_protocol.settings import ComponentSettings, component_model_config, component_root

#: 组件根目录（基于本文件自身位置解析；打包形态限制见 component_root 文档）
COMPONENT_ROOT: Path = component_root(__file__)

#: 默认配置文件路径
CONFIG_FILE: Path = COMPONENT_ROOT / "config.yaml"

#: hotkey 字段对应的环境变量名（env 前缀 + 字段名的推导结果，与上方
#: component_model_config 声明共置一处；app.py 覆盖检测与提示文案共用此常量，
#: 🔴 禁止在其他位置硬编码第二份）
HOTKEY_ENV_VAR = "ZEN_VOCOTYPE_CLIENT_HOTKEY"

#: paste_restore_delay_ms 字段对应的环境变量名（与 HOTKEY_ENV_VAR 同模式；
#: app.py 覆盖检测与提示文案共用此常量，🔴 禁止在其他位置硬编码第二份）
RESTORE_DELAY_ENV_VAR = "ZEN_VOCOTYPE_CLIENT_PASTE_RESTORE_DELAY_MS"


class Settings(ComponentSettings):
    """客户端全部配置项的唯一入口。"""

    model_config = component_model_config(__file__, "ZEN_VOCOTYPE_CLIENT_")

    socket_path: str = DEFAULT_SOCKET_PATH

    #: 全局热键（pynput 组合键表达式，按住说话、松开识别）；启动时经 pynput 解析校验。
    #: 默认 <ctrl>+<alt>+y；
    #: 🔴 与旧版 GridChat <ctrl>+` 及本机已占用的 <ctrl>+<alt>+v / <ctrl>+<alt>+t 明确区分
    hotkey: str = "<ctrl>+<alt>+y"

    #: 剪贴板恢复延迟（毫秒）：粘贴发出后恢复原剪贴板的保守延迟。
    #: 依据：默认 200ms（缩短剪贴板被占用窗口；恢复前有剪贴板指纹校验兜底——选型八，
    #: 个别应用读取偏慢导致粘贴到旧内容时可调大）。命名常量 + 可配置（C2）。
    paste_restore_delay_ms: int = Field(default=200, ge=0)

    #: 最大录音时长（秒）。依据：对齐协议体上限 MAX_BODY_BYTES（约 10 分钟 PCM）留足余量；
    #: 到达上限自动停止并进入识别流程 + 通知（选型四）
    max_record_seconds: int = Field(default=60, gt=0)

    #: 录音输入设备（sounddevice 设备名或索引）；None = 系统默认输入设备。
    #: 启动时探测并日志记录实际设备，设备缺席启动即明确报错（选型四）
    input_device: str | int | None = None

    #: 同类错误通知去重窗口（秒），🔴 禁止通知轰炸（选型九）
    notify_dedup_seconds: float = Field(default=5.0, ge=0.0)

    #: 声音辅助提示开关（选型九可选通道，默认关闭）
    enable_sound_notify: bool = False

    #: 服务端模型加载中（LOADING 态）的 health 轮询间隔（毫秒）。
    #: 依据：本地 Socket 往返毫秒级，3s 间隔对服务端无压力且用户可感知的等待可接受
    loading_poll_interval_ms: int = Field(default=3000, ge=500)

    #: LOADING 态轮询最大次数（默认 120 次 × 3s ≈ 6 分钟，覆盖大模型冷启动加载）。
    #: 达上限停止轮询并提示手动重试——落实「禁止后台无限重试」红线（选型二）
    loading_poll_max_count: int = Field(default=120, ge=1)

    #: 日志目录。默认 XDG 状态目录（契约库唯一出处）；🔴 禁止组件根目录内
    #: （AppImage 只读挂载点写入必失败，阶段 4 T4.1 整改）
    log_dir: Path = DEFAULT_LOG_DIR

    #: 录音/识别文本落盘总开关（T34）：开启后每次录音保存 WAV +
    #: 识别完成保存同基名 TXT；托盘菜单「保存录音」勾选项实时切换并持久化
    save_recordings: bool = True

    #: 录音保存目录。默认 XDG data 下 zen_vocotype/recordings
    #: （契约库 ``paths.get_recordings_dir`` 唯一出处）；
    #: 🔴 必须绝对路径（对齐「路径类配置写绝对路径」红线）
    recordings_dir: Path = Field(default_factory=get_recordings_dir)

    @field_validator("recordings_dir")
    @classmethod
    def _recordings_dir_must_be_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError(f"recordings_dir 必须为绝对路径：{value}")
        return value


def validate_startup(settings: Settings) -> None:
    """启动期配置校验：任何非法配置在此显式失败（🔴 禁止运行期才暴露）。

    :raises ValueError: hotkey 表达式无法被 pynput 解析，或录音保存开启但
        目录不可创建/不可写时
    """
    # pynput 组合键表达式解析试跑（Hotkey 解析逻辑单一出处在 hotkey 模块，
    # 此处仅复用其解析器避免两处漂移——但 hotkey 模块属客户端内部，允许 import）
    from .hotkey.combo import parse_hotkey  # 延迟 import：配置层不反向依赖热键后端

    parse_hotkey(settings.hotkey)  # 非法表达式抛 ValueError，由入口转化非零退出

    # 录音保存开启时探测目录可创建且可写（同目录临时文件试写，🔴 非系统临时目录）；
    # 失败抛 ValueError 由入口转化退出码 2——🔴 禁止默认目录不可写仍静默启动
    if settings.save_recordings:
        try:
            ensure_user_dir(settings.recordings_dir)
        except OSError as exc:
            raise ValueError(
                f"录音保存目录不可创建或不可写：{settings.recordings_dir}（{exc}）"
            ) from exc
