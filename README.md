# Zen_VocoType

Zen_VocoType 是一套 **Linux 桌面**的本地离线语音输入工具：在任意应用中
**按住热键说话，松开即自动识别并把文字粘贴到当前光标处**，随后自动恢复原剪贴板。
识别全程在本机完成，离线可用，语音数据不出本机。

## 项目简介

### 核心特性

- **即按即说**：默认热键 `<ctrl>+<alt>+y`，托盘可图形化改键，即时生效并持久化
- **纯本地离线识别**：默认引擎 fun-asr-nano（GGUF 量化模型约 1.2GB，首次启动自动下载，
  缓存命中后离线直载）；另内置 sensevoice-small（多语言 + 情感/事件识别）与
  qwen3-asr-1.7b（高精度多语言/方言，实验性），托盘菜单一键切换
- **三组件系统托盘**：状态色点一眼可读；切换模型、修改快捷键、设置模型目录、
  录音落盘开关等全部经托盘完成，无独立窗口
- **一键拉起全套**：双击 Launcher 即按序拉起 Service 与 Client——幂等（已运行则只做
  健康检查）、失败自动回收本进程拉起的子进程、成功后托盘观察窗口结束自退
- **XDG 分层数据布局**：模型 / 日志 / 用户配置各归其位（详见「数据与配置位置」），
  AppImage 只读挂载零写入
- **纯用户态安装**：桌面菜单入口与开机自启动均无需 root、不依赖 systemd

### 平台与运行形态

- 🔴 **仅支持 Linux**（全局热键、系统托盘、剪贴板操作均依赖 Linux 桌面环境；
  不支持 Windows / macOS）。全局热键基于 X11 实现，Wayland 会话下 v1 暂不支持热键
- 三种运行形态：**AppImage 单文件**（推荐分发形态）、**onedir 裸目录**（二级分发物）、
  **源码开发态**（项目 `.venv` 直跑，详见「开发态运行与测试」）

## 组件总览

仓库由四个组件构成：三个可运行进程 + 一个共享契约库。

- **Zen_VocoType_Service**：ASR 推理服务端。FunASR / Qwen3-ASR 引擎体系，
  Unix Socket 复合帧协议，模型注册表驱动的加载与原子切换（先备后切、失败自动回滚），
  单实例锁 + 确定性退出；支持 `audio_chunk` 流式通道，单段可识别 2 小时以上长音频。
- **Zen_VocoType_Client**：客户端。全局热键监听、录音（60 秒上限）、识别结果写剪贴板
  并模拟粘贴、随后恢复原剪贴板；录音与识别文本可自动落盘回溯；无窗口产品形态，
  全部交互为托盘图标 + 右键菜单 + 桌面通知。
- **Zen_VocoType_Launcher**：启动器。只做进程编排：按序拉起 Service → Client →
  协议级就绪等待 → 通知完成 → 自身退出；默认托盘模式提供启动延迟 / 组件位置 /
  开机自启动等设置；两端退出后独立存活，再次执行即幂等健康检查。
- **Zen_VocoType_Protocol**：三组件共享的协议契约库——复合帧格式、action 常量、
  错误码、协议版本、全局路径等关键项的**唯一出处**；开发时 editable install，
  打包时随各端产物内嵌。

**通信关系**：Client 与 Launcher 均经 **Unix Socket** 与 Service 通信，协议语义以
Protocol 契约库为唯一出处；🔴 三个组件目录之间禁止任何相互 import，对外协作仅经 Socket。

各组件的配置项、托盘菜单、协议行为、退出码等细节见各自 README（见文末「文档导航」）。

---

## 快速开始（打包产物）

### 产物形态与下载布局

三组件各自独立交付（AppImage 单文件 + onedir 裸目录二级分发物），
使用时**三份 AppImage 放同一目录**（邻接目录约定，位置任意）：

```
Zen_VocoType/                          # 用户自摆目录（位置任意）
├── Zen_VocoType_Launcher.AppImage     # 双击入口
├── Zen_VocoType_Service.AppImage
└── Zen_VocoType_Client.AppImage
```

双击 `Zen_VocoType_Launcher.AppImage` 即一次拉起全套（约 10 秒就绪）。
Launcher 查找顺序：同目录 AppImage → 同目录 onedir 目录 → 配置显式路径
→ `~/AppImages` 兜底。

构建本地产物（需 appimagetool 在 PATH）：

```bash
.venv/bin/python tools/build.py --component all --appimage   # 产物在 dist/
```

### 桌面入口安装/卸载（应用菜单图标 + 双击启动）

```bash
# 安装（幂等，纯用户态无需 root；--dir 为 AppImage 摆放目录）
.venv/bin/python tools/install_desktop.py --dir dist

# 卸载（幂等）
.venv/bin/python tools/uninstall_desktop.py
```

安装内容：`~/.local/share/applications/zen-vocotype.desktop` +
hicolor 四档图标 `~/.local/share/icons/hicolor/<32,64,128,256>x*/apps/zen-vocotype.png`。

### 开机自启动（GNOME autostart）

```bash
# 安装桌面入口的同时安装自启动条目（幂等）
.venv/bin/python tools/install_desktop.py --dir <AppImage 摆放目录> --autostart
```

也可在 Launcher 托盘勾选「登录后自动启动启动器」热切换（即时生效并持久化）。

- 条目写入 `~/.config/autostart/zen-vocotype.desktop`，桌面环境在**图形会话
  就绪后**执行（DISPLAY 齐备，托盘正常）；卸载时与菜单条目一并删除
- 🔴 请勿改用 systemd 用户服务做自启动：systemd 服务早于图形会话环境
  注入启动，Launcher 检测不到 DISPLAY 会回退一次性 CLI（托盘全灭）——
  2026-07-23 实机事故，详见 `2026-0723-1705_systemd自启动下启动器托盘失效诊断报告.md`
- 若曾手工配置 systemd 自启动服务，先 `systemctl --user disable --now <单元>`
  再装本条目（防双启动）
- 预期效果：登录后 Launcher 托盘**短暂出现**（编排成功按 `exit_after_success_s`
  自退，默认 60 秒），常驻托盘为 Service/Client 图标；成功路径**无桌面通知**
- 移动/改名 AppImage 后需重跑安装脚本（条目 Exec 按安装时路径渲染）

### 数据与配置位置（XDG 分层，AppImage 只读挂载零写入）

| 类别 | 默认位置 | 说明 |
|:---|:---|:---|
| 模型缓存 | `~/.local/share/zen_vocotype/models` | MODELSCOPE_CACHE；可用托盘「设置模型目录…」自选（重启生效） |
| 日志 | `~/.local/state/zen_vocotype/logs` | service/client/launcher.log 轮转 |
| 用户配置 | `~/.config/zen_vocotype/user_config.yaml` | 托盘写入的覆盖项（如 models_dir），三组件共享单文件 |
| 录音/识别文本 | `~/.local/share/zen_vocotype/recordings` | 每次录音 wav + 识别结果 txt（托盘可开关、可换目录） |
| Socket/锁 | `$XDG_RUNTIME_DIR` | 回退 `~/.local/run` |

### 离线模型手工放置（无网环境补救）

1. 在有网机器下载模型（首次启动会自动下载，或经 modelscope 网页/CLI）
2. 将缓存目录按原布局复制到目标机 `~/.local/share/zen_vocotype/models/`：
   布局形如 `models/<组织>--<模型名>/snapshots/master/<文件>`（默认 fun-asr-nano
   为 GGUF 权重，共约 1.2GB：`FunAudioLLM--Fun-ASR-Nano-GGUF` 下
   `funasr-encoder-f16.gguf` + `qwen3-0.6b-q8_0.gguf`，
   外加 `FunAudioLLM--fsmn-vad-GGUF` 下 `fsmn-vad.gguf`）
3. 校验：启动 Service，日志出现「服务就绪（ready）」即缓存命中直载成功；
   缓存未命中会自动尝试在线下载（离线环境将报加载失败，日志有明确原因）

也可用 Service 托盘「设置模型目录…」指向已含模型缓存的任意目录
（如外置盘），保存后重启生效；v1 不做旧目录模型迁移，请手工搬移。

### 故障排查

- 切换/首载未缓存模型时托盘状态行显示「下载中…（模型名）」并弹气泡提醒，图标橙色：
  属正常在线下载（ModelScope），大模型可达数 GB、耗时数分钟到数十分钟，请耐心等待，
  完成后自动转绿「就绪」；断网等失败时状态行转红并含原因（v1.2 起）
- FUSE 缺失无法运行 AppImage：`./Zen_VocoType_Launcher.AppImage --appimage-extract-and-run`
  兜底，或安装 fuse（`sudo apt install fuse3`）；onedir 裸产物为二级分发物可直接用
- 日志不可写：组件自动降级 stderr 输出并记 warning，不崩溃
- 托盘图标缺失：记 warning 降级显示（不静默），请检查产物完整性
- 各组件专属故障（热键无响应、退出码、连接问题等）见对应组件 README 的「故障排查」节

---

## 开发态运行与测试

全部命令在项目根目录执行，统一使用项目 `.venv`（`uv run` 自动命中）：

```bash
# 开发模式一键拉起两端源码（Socket/锁文件与正式版隔离，可并行互不影响）
uv run python Zen_VocoType_Launcher/main.py --dev

# 或分别启动（另开终端）
uv run python Zen_VocoType_Service/main.py
uv run python Zen_VocoType_Client/main.py
```

正常使用时：托盘图标变绿后，**按住 `<ctrl>+<alt>+y` 说话，松开自动识别并粘贴**。

```bash
# 测试（quick 档位，默认排除 slow）
.venv/bin/python -m pytest Zen_VocoType_Service/tests -q
.venv/bin/python -m pytest Zen_VocoType_Client/tests -q
.venv/bin/python -m pytest Zen_VocoType_Launcher/tests -q
```

更多命令（单独打包、配置修改示例等）见 `常用命令.md`。

## 配置体系

每个组件以各自的 `config.yaml` 为包内唯一配置文件，配置链优先级：

```
显式入参 > 环境变量 > 用户配置文件 > 包内 config.yaml > 代码默认值
```

- 环境变量前缀 `ZEN_VOCOTYPE_<组件>_`（如 `ZEN_VOCOTYPE_SERVICE_MODELS_DIR`、
  `ZEN_VOCOTYPE_CLIENT_HOTKEY`）
- 用户配置文件 `~/.config/zen_vocotype/user_config.yaml` 三组件共享单文件、
  各自仅拾取自身声明字段；仅承载托盘写入的覆盖项（热键、模型目录、启动延迟等）
- 🔴 打包形态（AppImage）包内 `config.yaml` 只读，运行时持久化只能落用户配置文件

各组件完整配置项表见各自 README 的「配置」节；数据/日志/模型位置见上文
「数据与配置位置」表。

## 仓库结构

```
Zen_VocoType/
├── Zen_VocoType_Service/    # ASR 推理服务端（含 models/、bin/ vendor 二进制）
├── Zen_VocoType_Client/     # 客户端（热键/录音/剪贴板输出/托盘）
├── Zen_VocoType_Launcher/   # 启动器（进程编排/托盘/自启动）
├── Zen_VocoType_Protocol/   # 协议契约库（帧格式/action/错误码/路径唯一出处）
├── tools/                   # 工程脚本（打包 build.py、桌面入口、版本同步等）
├── dist/                    # 打包产物输出目录
├── 文档/                    # 项目文档（八个分类子目录，见下）
├── 协议/                    # 工作协议与规范文档
├── 参考代码/                # 重写参考的上游代码与示例
├── work charter/            # 工作章程
├── work plans/              # 工作计划
├── versions.toml            # 版本号唯一真相（由 tools/sync_versions.py 同步）
├── 常用命令.md              # 开发期运行/打包命令速查
└── 文档编写规范.md           # 文档命名/引用/时区规范
```

## 文档导航

- 组件细节：`Zen_VocoType_Service/README.md`、`Zen_VocoType_Client/README.md`、
  `Zen_VocoType_Launcher/README.md`、`Zen_VocoType_Protocol/README.md`
- 日常开发：`常用命令.md`（运行/打包/配置速查）、`AGENTS.md`（仓库级工作约束）、
  `文档编写规范.md`（文档命名与引用规范）
- `文档/` 子目录：
  - `文档/修改记录/`：历次功能实施记录与修复计划（历史快照，含通信协议设计定稿）
  - `文档/选型记录/`：技术选型与方案对比
  - `文档/理论依据/`：重大技术决策的背景与理由
  - `文档/审计报告/`：审计与数据校验报告
  - `文档/验收记录/`：阶段验收记录
  - `文档/GUI显示详细说明/`：GUI 显示项详细说明
  - `文档/提示语句/`：AI 交互提示词模板
  - `文档/封存文档/`：已完成或不再活跃的历史文档归档
- `work charter/`（工作章程）与 `work plans/`（工作计划）：任务的目标、边界与验收标准
