# Zen_VocoType_Service（服务端）

语音识别服务端（FunASR / Qwen3-ASR 双引擎）：Unix Socket 复合帧协议、
模型注册表驱动的加载/原子切换、先监听后异步加载、单实例锁 + 确定性退出。

## 启动与停服

```bash
# 开发态（项目根 .venv）
.venv/bin/python Zen_VocoType_Service/main.py
```

- **启动时序**：进程启动即监听 Socket（`health` 可答 `starting`），后台线程异步
  加载默认模型 + 试推理自检，完成后状态推进为 `ready`；加载失败为 `error`
  （`ready` 返回 3002 及真实原因）
- **停服**：向进程发 `SIGTERM`（Launcher 读锁文件内 PID 精确停服），服务端执行
  确定性退出序列：停止 accept → 停止推理 worker → 释放模型 → 删除 Socket 文件
  → 释放单实例锁，退出码 0
- **单实例**：启动时 `flock` 抢锁（契约库 `paths.SERVICE_LOCK_PATH`），已有实例
  运行则报错退出非零；锁内写有 PID；kill -9 后无 stale 锁可直接重启

## 系统托盘

有显示环境时（DISPLAY / WAYLAND_DISPLAY）启动后自动在系统托盘显示图标；
headless 服务器 / `tray_enabled: false` / 托盘不可用时自动降级为纯控制台模式，
不影响服务功能。

**图标状态色**（基础图标右下角色点，资产复制自 GridChat_Service/asset）：

| 色点 | 含义 |
| --- | --- |
| 橙 | 加载中…（模型未就绪）/ 切换中… |
| 绿 | 就绪 |
| 红 | 错误（状态行附原因） |

**右键菜单**（自上而下；版本号唯一真相为仓库根 `versions.toml`，由 `tools/sync_versions.py` 同步）：

```
Zen_VocoType_Service v1.0        ← 版本项（禁用）
版本: 1.0（开发版/打包版）        ← 禁用
─────────────────────────────
状态：就绪                        ← 禁用，轮询刷新
当前模型：fun-asr-nano           ← 禁用，轮询刷新
─────────────────────────────
切换模型 ►                       ← 注册表逐键列出，当前模型前缀 ✓
   ├── ✓ fun-asr-nano              （非就绪 / 切换中 / 仅 1 模型时禁用）
   ├── sensevoice-small
   └── qwen3-asr-1.7b
─────────────────────────────
打开日志目录                      ← 打开 log_dir
退出服务                          ← 与 SIGTERM 同一退出序列
```

- 「切换模型」与客户端 `model_switch` 请求等效，统一经推理 worker 队列串行
  （与推理天然互斥，失败自动回滚）；托盘在后台线程提交，不阻塞界面
- 托盘相关配置：`tray_enabled`（默认 true）、`tray_poll_interval_ms`（默认 500）

## 配置（单一配置源）

`src/zen_vocotype_service/config.py` 的 `Settings` + `config.yaml` + 用户配置文件 + 环境变量
（前缀 `ZEN_VOCOTYPE_SERVICE_`），优先级：显式入参 > 环境变量 > 用户配置文件 > config.yaml > 默认值。

**用户配置文件**（阶段 4 T4.1b）：`$XDG_CONFIG_HOME/zen_vocotype/user_config.yaml`
（回退 `~/.config/...`，路径唯一出处为契约库 `paths.DEFAULT_USER_CONFIG_PATH`），
三组件共享单文件、各自仅拾取自身声明字段；仅承载覆盖项（如 `models_dir`），
由托盘「设置模型目录…」写入（原子写；损坏回退默认值 + warning，不静默不崩溃）。
🔴 打包形态（AppImage）包内 config.yaml 只读，运行时持久化只能落用户配置文件。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `socket_path` | 契约库 `paths.DEFAULT_SOCKET_PATH`（`$XDG_RUNTIME_DIR/zen_vocotype.sock`） | 仅允许覆盖，默认值勿照抄 |
| `models_dir` | 契约库 `paths.DEFAULT_MODELS_DIR`（`$XDG_DATA_HOME/zen_vocotype/models`，回退 `~/.local/share/...`） | MODELSCOPE_CACHE 指向（入口第一行硬设置，顺序敏感）；🔴 默认不落组件根（AppImage 只读挂载写失败，阶段 4 T4.1） |
| `default_model` | `fun-asr-nano` | 必须存在于注册表，否则启动报错退出 |
| `log_dir` | 契约库 `paths.DEFAULT_LOG_DIR`（`$XDG_STATE_HOME/zen_vocotype/logs`，回退 `~/.local/state/...`） | loguru 轮转（10MB × 5）；三组件共享目录、文件名区分 |
| `models` | 内置三条（见下） | 模型注册表（config.yaml 内嵌） |
| `infer_timeout_s` | 300 | 推理超时预算（按默认引擎 fun-asr-nano 分钟级长音频实测 RTF≈0.27 标定） |
| `queue_max_pending` | 4 | 推理队列积压阈值，超过拒绝新请求 |
| `max_connections` | 8 | 连接数上限（防御性） |
| `tray_enabled` | true | false 强制纯控制台模式（headless 自动降级见「系统托盘」） |
| `tray_poll_interval_ms` | 500 | 托盘状态轮询间隔（≥100） |

### 模型注册表写法

```yaml
models:
  fun-asr-nano:
    model_id: FunAudioLLM/Fun-ASR-Nano-2512
    vad_model_id: iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
    extra_params: {trust_remote_code: true, remote_code: ./model.py}
  sensevoice-small:
    model_id: iic/SenseVoiceSmall
    vad_model_id: iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
  # 本地直载条目示例（model_id 与 local_path 二选一）：
  # my-local-model:
  #   local_path: /绝对路径/到/模型目录
default_model: fun-asr-nano
```

- `model_id`（缓存命中/在线下载）与 `local_path`（本地直载）每条目二选一
- 注册表条目支持挂附属 VAD / 标点模型
- `engine_type`：`funasr`（默认，可省略）/ `qwen3-asr`；加载与推理分支的唯一依据
- `extra_params`：引擎特定加载附加参数（funasr 并入 `AutoModel()`，
  qwen3-asr 并入 `Qwen3ASRModel.from_pretrained()`）；
  例：Fun-ASR-Nano 需 `trust_remote_code: true` + `remote_code: ./model.py`

### 内置引擎一览

| 注册名 | 引擎 | 定位 | CPU 实测 RTF |
| --- | --- | --- | --- |
| `fun-asr-nano` | funasr | 通用离线识别（默认）：自带标点/热词/方言的新一代 LLM-ASR | ≈0.27–0.34 |
| `sensevoice-small` | funasr | 多语言 + 情感/事件识别 | 极快 |
| `qwen3-asr-1.7b` | qwen3-asr | 高精度多语言/方言 SOTA（⚠️ 慢于实时，短音频/实验性） | ≈1.2 |

- 模型缓存布局即 modelscope 现行布局（`models/models/iic--<名>/snapshots/master`），
  缓存命中则离线加载，未命中在线下载（进度以日志呈现）

### 自选模型目录（托盘「设置模型目录…」，T4.1b）

- 托盘菜单选定目录 → 校验（🔴 不存在 / 不可写 / AppImage 挂载点内三分支拒绝，
  不静默）→ 写用户配置文件 → **下次启动生效**（MODELSCOPE_CACHE 顺序红线，
  v1 不做运行期热切换）
- 菜单内禁用态行展示当前生效目录；启动日志输出 `models_dir` 生效值与来源层
- 切换后目录为空：首次使用走 `model_download` 通道自动下载，或手工放置既有
  modelscope 缓存（保持 `models/iic--<名>/...` 布局）；🔴 v1 不做旧目录模型
  迁移/复制，请手工搬移

## 协议行为摘要

协议语义定稿见 `文档/通信协议设计_v1.0.md`；常量唯一出处为契约库
`zen_vocotype_protocol`（action / 错误码 / 帧格式 / 路径），本组件不重复定义。

- 已实现 action：`health` / `ready` / `recognize` / `model_info` / `model_switch`
- `audio_chunk`（预留未实现）：入站校验放行后返回 **1005**；未知 action 返回 **1002**
- 错误码 13 个全部按冻结表使用，未新增；关键映射：推理超时 → 4002（message 注明
  `timeout`）、队列满/切换中收到 recognize → 2002（注明 `model_switching`）、
  目标未注册 → 3001、加载失败 → 3002、切换自检失败回滚 → 3003
- `model_switch` 为原子切换（先备后切 + 试推理自检，失败回滚旧模型不受影响），
  成功后以 `model_info` 交叉验证
- Socket 本地访问控制（协议 §7.1 强制项）：bind 前校验非符号链接且属主自身 →
  bind 后显式 `chmod 0600` → accept 时 `SO_PEERCRED` 校验同 UID，违规返回 1006

## 目录结构

```
Zen_VocoType_Service/
├── main.py                 # 入口：MODELSCOPE_CACHE 第一行 → 锁 → 先监听 → 异步加载
├── pyproject.toml          # 项目元数据与依赖
├── config.yaml             # 本组件唯一配置文件
├── src/zen_vocotype_service/
│   ├── config.py           # Settings + 模型注册表 + 启动校验
│   ├── logging_setup.py    # loguru 双 sink 封装（禁跨组件 import）
│   ├── instance_lock.py    # flock 单实例锁 + PID
│   ├── server.py           # Socket 监听 + §7.1 访问控制
│   ├── connection.py       # 每连接收发循环 + 入站校验 + 分发
│   ├── state.py            # 线程安全服务状态（starting/ready/error）
│   ├── context.py          # 处理器共享上下文
│   ├── protocol_io.py      # 响应构建 + ProtocolError
│   ├── handlers/           # health/ready/recognize/model_info/model_switch
│   ├── models/             # registry / loader（含自检）/ manager（原子切换）
│   ├── inference/          # 单 worker 推理队列
│   └── tray/               # 系统托盘（icon_loader 双环境解析 + ServiceTray）
├── assets/                 # 托盘图标四档（icon_{32,64,128,256}.png，复制自 GridChat_Service/asset）
│                           #   + 自检音频 selftest_16k.pcm（来源见 loader 注释）
├── logs/                   # 历史运行日志 + 阶段 1 实测数据（phase1_measurements.json）；
│                           #   ⚠️ 新日志默认落 XDG 状态目录（见配置表 log_dir）
├── models/                 # 历史模型缓存；⚠️ MODELSCOPE_CACHE 默认已迁 XDG 数据目录
└── tests/                  # pytest（slow 标记为真实模型/进程级测试）
```

## 打包产物（阶段 4）

onedir 与 AppImage 双形态经 `tools/build.py --component service [--appimage]` 构建
（详见仓库根 README「打包产物使用说明」节）。本组件要点：

- torch/FunASR/modelscope 经 spec `collect_all` 显式收编（延迟 import 静态分析不可见）
- 模型默认 XDG 数据目录（`~/.local/share/zen_vocotype/models`），🔴 不随包；
  离线放置步骤与托盘「设置模型目录…」见根 README
- 打包形态日志默认 XDG 状态目录；不可写时降级 stderr + warning（不崩溃）

## 测试

```bash
# 快速测试（默认排除 slow）
cd Zen_VocoType_Service && ../../.venv/bin/python -m pytest tests -q
# 全量（含真实模型加载、E2E、冷启动实测、样本识别旁证）
../../.venv/bin/python -m pytest tests -q -m slow
```
