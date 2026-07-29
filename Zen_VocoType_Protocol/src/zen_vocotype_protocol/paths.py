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

#: 服务端单实例锁文件路径（flock + PID 记录，选型七）。
#: 🔴 必须落在用户私有运行目录（与 Socket 路径同约定，禁止 /tmp 可预测路径）；
#: Launcher（阶段 3）读本文件内 PID 发 SIGTERM 精确停服，路径只准引用本常量
SERVICE_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_service.lock")

#: 服务端 dev 模式单实例锁（与正式锁分离，阶段 3 验收标准 4「dev/正式互不干扰」前提：
#: dev Socket 已由 DEV_SOCKET_PATH 隔离，锁文件若共用则两端仍无法并行）
DEV_SERVICE_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_service_dev.lock")

#: 客户端单实例锁文件路径（阶段 3 T3.2 补齐，与 SERVICE_LOCK_PATH 同约定）；
#: Launcher 读本文件内 PID 做幂等识别（/proc/<pid>/exe 精确匹配）
CLIENT_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_client.lock")

#: 客户端 dev 模式单实例锁（与正式锁分离，理由同 DEV_SERVICE_LOCK_PATH）
DEV_CLIENT_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_client_dev.lock")

#: Launcher 自身单实例锁（flock + 元信息记录，阶段 3 选型三）；
#: 持锁期间写入 JSON 元信息（pid/mode/started_at），供第二次执行读取并提示
LAUNCHER_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_launcher.lock")

#: Launcher dev 模式单实例锁（与正式锁分离，dev/正式编排互不阻塞）
DEV_LAUNCHER_LOCK_PATH: str = str(DEFAULT_RUNTIME_DIR / "zen_vocotype_launcher_dev.lock")

# ---------------------------------------------------------------------------
# XDG 用户数据/状态目录（阶段 4 T4.1，旧事故「路径错位」整改落地）
# ---------------------------------------------------------------------------
# 背景：打包形态（AppImage）程序目录为只读挂载点，模型/日志默认值若仍落在
# 组件根目录内（``COMPONENT_ROOT / "models" | "logs"``）则运行必失败——
# 与旧 GridChat「随包模型从未生效」「日志写到 cwd 继承目录」同款根因。
# 因此运行时写入类路径默认值一律落 XDG 用户目录（选型九：模型 data、
# 日志 state、配置 config、运行 runtime 分层），唯一出处为本模块；
# 三组件配置仅允许覆盖（pydantic-settings 环境变量/config.yaml 覆盖链保持），
# 🔴 禁止程序目录内、🔴 禁止 cwd 相对、🔴 禁止系统临时目录。


def _default_data_dir() -> Path:
    """返回 XDG 数据目录：优先 ``$XDG_DATA_HOME``，回退 ``~/.local/share``。"""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data)
    return Path.home() / ".local" / "share"


def _default_state_dir() -> Path:
    """返回 XDG 状态目录：优先 ``$XDG_STATE_HOME``，回退 ``~/.local/state``。"""
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state)
    return Path.home() / ".local" / "state"


def ensure_user_dir(path: Path) -> Path:
    """创建用户目录（含父级）并校验可写，返回该路径。

    权限校验以同目录临时文件试写为准（🔴 非系统临时目录）；任何失败抛
    ``OSError``，由调用方转化为明确错误日志（🔴 禁止静默回退——旧事故
    「名义在线实为缓存」的教训）。

    :param path: 目标目录（应为 XDG 用户目录或其子目录）
    :raises OSError: 创建失败或目录不可写
    """
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".zen_vocotype_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        probe.unlink(missing_ok=True)
    return path


#: 默认模型根目录（modelscope 缓存，MODELSCOPE_CACHE 指向；XDG data 层——
#: 体积大、可重新下载、跨版本共享，语义同 ``~/.cache`` 但 modelscope 约定
#: 为数据目录，与选型九分层一致）
DEFAULT_MODELS_DIR: Path = _default_data_dir() / "zen_vocotype" / "models"

#: 默认日志目录（XDG state 层——持久但可再生）。
#: 三组件共享同一目录即可：日志文件名互不相同（service.log / client.log /
#: launcher.log，各组件 logging_setup 单一出处），无冲突故不再分子目录
DEFAULT_LOG_DIR: Path = _default_state_dir() / "zen_vocotype" / "logs"

#: 默认 audio_chunk 会话临时 WAV 目录（XDG data 层——体积大（2h ≈ 230MB）、
#: 会话结束即删、崩溃后可再生。🔴 禁止放 runtime tmpfs——tmpfs 即内存，
#: 长音频会话 WAV 会重现全量内存驻留问题；服务端配置仅允许覆盖）
DEFAULT_CHUNK_SESSION_DIR: Path = _default_data_dir() / "zen_vocotype" / "chunk_sessions"


def get_recordings_dir() -> Path:
    """返回默认录音/识别文本保存目录：``$XDG_DATA_HOME/zen_vocotype/recordings``。

    XDG data 层——用户内容数据（不可再生，语义同 models 目录）。
    客户端录音落盘默认值的**唯一出处**（T34）；组件配置仅允许覆盖，
    🔴 禁止各组件另立 XDG 解析逻辑。函数形式（非导入期冻结常量）：
    运行时解析，测试可经环境变量隔离。
    """
    return _default_data_dir() / "zen_vocotype" / "recordings"


def _default_config_dir() -> Path:
    """返回 XDG 配置目录：优先 ``$XDG_CONFIG_HOME``，回退 ``~/.config``。"""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config)
    return Path.home() / ".config"


#: 用户配置文件路径（XDG config 层，阶段 4 T4.1b）。
#: 配置链：组件默认值（契约库）→ 包内 config.yaml → 本文件 → 环境变量；
#: 读写行为逻辑唯一出处为契约库 ``user_config`` 模块；
#: 🔴 禁止写包内 config.yaml（AppImage 只读挂载——写包内即下一个路径失效事故）
DEFAULT_USER_CONFIG_PATH: Path = (
    _default_config_dir() / "zen_vocotype" / "user_config.yaml"
)

# ---------------------------------------------------------------------------
# 音频格式约定（recognize 请求的默认 PCM 参数，与选型 3 一致）
# ---------------------------------------------------------------------------

#: 采样率 Hz
DEFAULT_SAMPLE_RATE: int = 16000

#: 声道数
DEFAULT_CHANNELS: int = 1

#: 采样位宽（字节），16bit = 2
DEFAULT_SAMPLE_WIDTH: int = 2
