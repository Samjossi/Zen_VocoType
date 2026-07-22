# Zen_VocoType_Service（服务端）

FunASR 语音识别服务端：Unix Socket 复合帧协议、模型注册表驱动的加载/原子切换、
先监听后异步加载、单实例锁 + 确定性退出。

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

**右键菜单**（自上而下）：

```
Zen_VocoType_Service v1.0        ← 版本项（禁用）
版本: 1.0（开发版/打包版）        ← 禁用
─────────────────────────────
状态：就绪                        ← 禁用，轮询刷新
当前模型：paraformer-large        ← 禁用，轮询刷新
─────────────────────────────
切换模型 ►                       ← 注册表逐键列出，当前模型前缀 ✓
   ├── ✓ paraformer-large           （非就绪 / 切换中 / 仅 1 模型时禁用）
   └── sensevoice-small
─────────────────────────────
打开日志目录                      ← 打开 log_dir
退出服务                          ← 与 SIGTERM 同一退出序列
```

- 「切换模型」与客户端 `model_switch` 请求等效，统一经推理 worker 队列串行
  （与推理天然互斥，失败自动回滚）；托盘在后台线程提交，不阻塞界面
- 托盘相关配置：`tray_enabled`（默认 true）、`tray_poll_interval_ms`（默认 500）

## 配置（单一配置源）

`src/zen_vocotype_service/config.py` 的 `Settings` + `config.yaml` + 环境变量
（前缀 `ZEN_VOCOTYPE_SERVICE_`），优先级：显式入参 > 环境变量 > config.yaml > 默认值。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `socket_path` | 契约库 `paths.DEFAULT_SOCKET_PATH`（`$XDG_RUNTIME_DIR/zen_vocotype.sock`） | 仅允许覆盖，默认值勿照抄 |
| `models_dir` | 组件 `models/` | MODELSCOPE_CACHE 指向（入口第一行硬设置，顺序敏感） |
| `default_model` | `paraformer-large` | 必须存在于注册表，否则启动报错退出 |
| `log_dir` | 组件 `logs/` | loguru 轮转（10MB × 5） |
| `models` | 内置两条（见下） | 模型注册表（config.yaml 内嵌） |
| `infer_timeout_s` | 60 | 推理超时预算（依据见验收记录实测） |
| `queue_max_pending` | 4 | 推理队列积压阈值，超过拒绝新请求 |
| `max_connections` | 8 | 连接数上限（防御性） |
| `tray_enabled` | true | false 强制纯控制台模式（headless 自动降级见「系统托盘」） |
| `tray_poll_interval_ms` | 500 | 托盘状态轮询间隔（≥100） |

### 模型注册表写法

```yaml
models:
  paraformer-large:
    model_id: iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch
    vad_model_id: iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
    punc_model_id: iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch
  sensevoice-small:
    model_id: iic/SenseVoiceSmall
    vad_model_id: iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
  # 本地直载条目示例（model_id 与 local_path 二选一）：
  # my-local-model:
  #   local_path: /绝对路径/到/模型目录
default_model: paraformer-large
```

- `model_id`（缓存命中/在线下载）与 `local_path`（本地直载）每条目二选一
- 注册表条目支持挂附属 VAD / 标点模型
- 模型缓存布局即 modelscope 现行布局（`models/models/iic--<名>/snapshots/master`），
  缓存命中则离线加载，未命中在线下载（进度以日志呈现）

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
├── assets/                 # 托盘图标五档（icon*.png，复制自 GridChat_Service/asset）
│                           #   + 自检音频 selftest_16k.pcm（来源见 loader 注释）
├── logs/                   # 运行日志 + 阶段 1 实测数据（phase1_measurements.json）
├── models/                 # MODELSCOPE_CACHE（模型外置目录）
└── tests/                  # pytest（slow 标记为真实模型/进程级测试）
```

## 测试

```bash
# 快速测试（默认排除 slow）
cd Zen_VocoType_Service && ../../.venv/bin/python -m pytest tests -q
# 全量（含真实模型加载、E2E、冷启动实测、样本识别旁证）
../../.venv/bin/python -m pytest tests -q -m slow
```
