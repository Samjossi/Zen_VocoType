# Zen_VocoType_Client（客户端）

语音转文字客户端：按住全局热键说话 → 服务端识别 → 文字粘贴到光标处并恢复原剪贴板。
无窗口产品形态：全部交互为托盘图标 + 右键菜单 + 桌面通知。

## 启动

```bash
# 开发环境（项目根 .venv）
.venv/bin/python Zen_VocoType_Client/main.py

# 打包产物（onedir）
./zen_vocotype_client/zen_vocotype_client
```

退出码：`0` 正常；`2` 配置校验失败；`3` 录音设备不可用；`4` 全局热键启动失败。

开发自查模式：`python main.py --screenshot <输出目录>` —— 托盘图标/右键菜单/
状态色截图自检产物落盘（🔴 仅开发用途）。

## 使用

1. 启动后托盘出现麦克风图标，右下角色点表示状态（见下表）
2. 按住 `<ctrl>+<alt>+y` 说话，松开即识别并粘贴到当前光标处
3. 录音上限 60 秒，到达自动停止并识别（通知提示）
4. 粘贴完成后约 200ms 自动恢复你原来的剪贴板内容；若此间你复制了新内容，
   恢复自动放弃（不会覆盖你的新内容）

托盘右键菜单：`Zen_VocoType v0.2`（版本标识，版本号唯一真相为仓库根 versions.toml）/ 状态行 / 快捷键展示行 / 修改快捷键… / 保存录音（勾选项）/ 选择保存路径… / 打开保存文件夹 / 重试连接服务端 / 退出。

### 录音与识别文本保存（托盘菜单，默认开启）

每次录音的音频与识别结果自动落盘，便于回溯核对：

- 录音保存为 `<保存目录>/YYYYMMDD_HHMMSS.wav`（16kHz/16bit/单声道，
  60 秒约 1.9MB）；识别完成保存同基名 `.txt`（utf-8）
- 识别失败时 wav 保留、不写 txt——txt 缺失即该次识别失败的可观测信号
- 保存失败（磁盘满、目录不可写等）仅通知告警，**不影响**识别-粘贴主流程
- 默认保存目录：`$XDG_DATA_HOME/zen_vocotype/recordings`
  （回退 `~/.local/share/zen_vocotype/recordings`），长期使用可自行清理

托盘菜单操作（即时生效并持久化到用户配置文件，重启仍生效）：

- 「保存录音」勾选项：开关落盘总开关；关闭期间录音/识别照常，仅不写文件
- 「选择保存路径…」：图形化选目录（校验可写后才生效）
- 「打开保存文件夹」：在文件管理器中打开当前保存目录

### 修改快捷键（托盘菜单，即时生效 + 持久化）

1. 托盘右键 →「修改快捷键…」，弹出捕获对话框
2. **直接按下**目标组合键（如 `Ctrl+Alt+K`），对话框实时回显；
   不支持的按键（媒体键等白名单外按键）会被拒绝并提示
3. 「确定」即换即用（不重启客户端）；「恢复默认」一键回到 `<ctrl>+<alt>+y`

- 新快捷键写入用户配置文件 `$XDG_CONFIG_HOME/zen_vocotype/user_config.yaml`
  （优先级高于包内 `config.yaml`），重启后仍然生效；🔴 不会写包内
  `config.yaml`（AppImage 只读挂载点）
- 若设置了环境变量 `ZEN_VOCOTYPE_CLIENT_HOTKEY`，其优先级高于用户配置文件，
  重启后将以环境变量为准（切换成功时通知会如实提醒）
- 录音/识别进行中不可改键（避免切换窗口丢失松开事件）；落盘失败则本次
  修改整体不生效，运行态快捷键保持不变

### 托盘状态色

| 色点 | 含义 |
|:---|:---|
| 灰 | 服务端未连接（未运行或连接中断） |
| 橙 | 服务端在线，模型加载中 |
| 绿 | 就绪，可使用 |
| 蓝 | 录音中 |
| 青 | 识别中 |
| 红 | 错误（如协议版本不兼容） |

## 配置（`config.yaml`，本组件唯一配置源）

优先级：环境变量（`ZEN_VOCOTYPE_CLIENT_*`）> 用户配置文件 > `config.yaml` > 代码默认值。
（用户配置文件：`$XDG_CONFIG_HOME/zen_vocotype/user_config.yaml`，三组件共享，阶段 4 T4.1b 新增层）

| 配置项 | 默认 | 说明 |
|:---|:---|:---|
| `socket_path` | 契约库默认值（用户私有运行目录） | 服务端 Socket 路径；🔴 勿照抄默认值到 config.yaml |
| `hotkey` | `<ctrl>+<alt>+y` | pynput 组合键表达式；非法表达式启动即报错（退出码 2） |
| `paste_restore_delay_ms` | `200` | 粘贴后恢复原剪贴板的保守延迟（缩短剪贴板占用窗口；个别应用读取偏慢时可调大） |
| `max_record_seconds` | `60` | 最大录音时长（对齐协议体上限留余量） |
| `input_device` | `null`（系统默认） | 录音设备（sounddevice 设备名或索引） |
| `notify_dedup_seconds` | `5` | 同类错误通知去重窗口 |
| `enable_sound_notify` | `false` | 通知声音辅助 |
| `loading_poll_interval_ms` | `3000` | 服务端模型加载中（橙灯）的 health 轮询间隔；就绪后托盘自动转绿 |
| `loading_poll_max_count` | `120` | 加载中轮询上限（默认约 6 分钟）；达上限停止并通知，需手动重试 |
| `log_dir` | 契约库 `paths.DEFAULT_LOG_DIR`（`$XDG_STATE_HOME/zen_vocotype/logs`，回退 `~/.local/state/...`） | 日志目录（loguru 双 sink，10MB×5 轮转；阶段 4 T4.1 迁 XDG） |
| `save_recordings` | `true` | 录音/识别文本落盘总开关（T34）；托盘「保存录音」勾选项实时切换 |
| `recordings_dir` | 契约库 `paths.get_recordings_dir()`（`$XDG_DATA_HOME/zen_vocotype/recordings`，回退 `~/.local/share/...`） | 录音保存目录（🔴 必须绝对路径）；保存开启时启动校验目录可创建且可写，失败退出码 2 |

热键表达式写法：`<ctrl>+<alt>+o`、`<ctrl>+<shift>+a`、`<f9>` 等 pynput 组合键
语法；修饰键支持 `<ctrl>/<alt>/<shift>/<cmd>`，必须恰好含一个非修饰主键。

## 故障排查

| 现象 | 含义与处置 |
|:---|:---|
| 通知「服务端未运行」 | 服务端进程未启动或 Socket 路径不一致。启动 Zen_VocoType_Service 后，托盘菜单「重试连接服务端」 |
| 通知「协议版本不兼容」 | 客户端与服务端协议版本 MAJOR.MINOR 不一致。两端更新到匹配版本（🔴 本客户端不做静默兼容） |
| 通知「模型切换中，请稍候」 | 服务端正在切换模型（错误码 2002），数秒后重试 |
| 通知「服务端正在加载模型」 | 服务端在线但模型未就绪（错误码 2001），等托盘转绿 |
| 托盘橙灯「连接中…」 | 服务端在线、模型加载中。客户端每 3s 自动轮询（`loading_poll_interval_ms`），模型就绪后自动转绿，无需操作；超过 `loading_poll_max_count` 次（默认约 6 分钟）未就绪则停止轮询并通知，此时从托盘菜单「重试连接服务端」 |
| 通知「服务端模型加载等待超时」 | 轮询达上限仍未就绪（大模型冷启动过慢或服务端异常）。确认服务端日志后，托盘菜单「重试连接服务端」 |
| 热键无响应 | ① 表达式非法会在启动时报错退出（查 `log_dir`/client.log）；② 组合被其他应用占用——托盘菜单「修改快捷键…」或改 `hotkey` 配置；🔴 勿用 `<ctrl>+``（与旧版 GridChat 冲突）、`<ctrl>+<alt>+v` 或 `<ctrl>+<alt>+t`（本机已占用） |
| 退出码 3 | 无可用录音输入设备或设备不支持 16kHz/16bit/单声道；查 `input_device` 配置 |
| 退出码 4 | pynput 无法连接 X11 显示（Wayland 环境 v1 不支持热键，后端抽象已预留 evdev/Portal） |
| 粘贴后剪贴板未恢复 | 你在恢复延迟窗口内复制了新内容（恢复自动放弃，属竞态保护的正常行为）；或目标应用读取超 200ms——调大 `paste_restore_delay_ms` |
| 剪贴板富内容（图片等）丢失 | v1 仅备份恢复**文本域**剪贴板内容（已知限制） |
| 通知「录音保存失败」/「识别文本保存失败」 | 保存目录不可写或磁盘满；识别-粘贴主流程不受影响。经托盘「选择保存路径…」换可写目录，或清理磁盘；wav 已落盘的不会回滚 |
| 通知「所选保存路径不可写」 | 「选择保存路径…」选中的目录无写权限，本次修改未生效；换目录重试 |
| 退出码 2（提及录音保存目录） | `save_recordings` 开启但 `recordings_dir` 不可创建/不可写，或 `recordings_dir` 为相对路径（🔴 必须绝对路径）；修正配置后重启 |

## 无托盘降级模式

系统托盘不可用（最小化 WM 等）时自动降级：通知改走 `notify-send` 并记 warning
日志；托盘菜单不可用（退出请发 SIGTERM）。降级路径全程有日志，无静默降级。

## 打包产物（阶段 4）

onedir 与 AppImage 双形态经 `tools/build.py --component client [--appimage]` 构建
（详见仓库根 README「打包产物使用说明」节）。本组件要点：

- spec 排除 torch/FunASR 系（推理全在 Service，经 Socket）
- 托盘图标随包（`_MEIPASS/assets`），缺失记 warning 降级（不静默）
- `--screenshot <目录>` 截图自检在打包形态同样可用

## 架构（阶段 2 实现）

```
main.py                 入口：日志→配置校验→装配→事件循环
src/zen_vocotype_client/
├── config.py           唯一配置入口 Settings（pydantic-settings）
├── app.py              装配层：状态机 + 四线程事件源接线
├── state_machine.py    状态机（枚举+集中转移表，非法转移抛异常）
├── logging_setup.py    loguru 双 sink（控制台+轮转文件）
├── hotkey/             热键：combo 解析 / backend 抽象 / pynput 实现
├── recorder/           录音：InputStream 回调+队列，60s 上限，流实例复用
├── transcribe/         网络：协议客户端（复合帧+版本握手）+ QThread worker
├── output/             输出：QClipboard（xclip 降级）/ pynput 粘贴（xdotool 降级）/ 指纹恢复
├── storage/            落盘：录音 WAV + 识别文本 TXT（纯逻辑，零 Qt 依赖）
└── tray/               托盘：状态色 / 版本菜单 / 通知去重 / 热键捕获对话框 / 截图自检
tests/                  单元 + 集成 + 端到端测试（模拟服务端桩 + 真实服务端）
```

线程模型：Qt 主线程唯一持有状态机；网络 worker 在 QThread；pynput/sounddevice
回调线程只发信号/写队列（红线，评审检查单落实）。

- **独立性约束**：不 import 其他两个组件目录下的任何代码；对外协作仅经 Unix Socket
- 协议语义见 `文档/通信协议设计_v1.0.md`；协议常量以契约库 `Zen_VocoType_Protocol` 为唯一出处
