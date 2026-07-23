# Zen_VocoType_Launcher

Zen_VocoType 启动器：按序拉起服务端与客户端、协议级就绪等待、单实例锁、失败清理。

**职责边界**（重写大纲 §3.1）：只做进程编排——拉起 Service → 拉起 Client →
就绪判定 → 通知完成 → **自身退出**（选型七方案 A：拉起确认后即退出，不做驻留监控）。
两端退出后独立存活；再次执行 Launcher 即幂等健康检查。

**T40 托盘模式**：默认以系统托盘形态运行（设置 + 观察窗口），编排成功后经
可配置观察窗口**自行退出**；失败时托盘停留不静默。托盘不是常驻守护——
进程生命周期与 CLI 一致（拉起两端后即退出），仅退出前多了可视化窗口期。

## 用法

```bash
# 托盘模式（默认）：系统托盘图标 + 右键菜单（设置/观察），成功后自行退出
python main.py

# 一次性 CLI 模式：不显示托盘，编排完成后即退出
python main.py --no-tray

# 开发模式：用当前 .venv 拉起两端源码（Socket/锁文件与正式版隔离）
python main.py --dev
```

无显示环境（无 `$DISPLAY`/`WAYLAND_DISPLAY`）自动回退一次性 CLI 并记 warning。

## 托盘菜单

```
Zen_VocoType Launcher v1.1
Service：●运行中   Client：○未运行      ◄── 状态行（实时刷新）
进度：将于 3 秒后启动服务端…            ◄── 进度行（倒计时/阶段文本）
─────────────────────────────
立即启动 / 重新检测状态
─────────────────────────────
服务端启动延迟（0 秒）…                 ◄── Launcher 启动后多少秒拉起服务端
客户端启动间隔（0 秒）…                 ◄── 服务端拉起后隔多少秒拉起客户端
成功后自动退出（8 秒）…                 ◄── 观察窗口（4~60，下限 4 秒保证图标可见）
─────────────────────────────
Service 位置：未设置（自动）… / 恢复 Service 自动解析
Client 位置：未设置（自动）… / 恢复 Client 自动解析
─────────────────────────────
退出启动器（不影响已启动组件）
```

- **设置即时生效（对下一次编排）+ 持久化**：写入用户配置文件
  `user_config.yaml`（🔴 不写包内 config.yaml——AppImage 只读）；
  检测到对应环境变量（`ZEN_VOCOTYPE_LAUNCHER_*`）时通知提醒其优先级更高
- **找不到组件不静默**：目标解析失败时状态行红字错误，「位置…」项直达补救
  （文件对话框选择 AppImage 或 onedir 内二进制，校验可执行位，非法拒绝落盘）
- **失败不自动退出**：编排失败托盘停留（状态行错误 + 通知），可调整设置后经
  「立即启动」重试；🔴 「退出启动器」不终止已启动的两端（选型七红线）

## 编排流程（T40 调整）

```
抢单实例锁 → 识别既有实例（幂等分支）→ 服务端启动延迟倒计时 → 拉起 Service
→ 客户端启动间隔倒计时 → 拉起 Client → 确认存活 → 两阶段就绪等待
（Socket 可连 → ready 轮询模型就绪，整体成败判定）
→ 通知「启动完成（含总耗时）」→ 释放锁退出 0
```

- **客户端拉起门控为固定间隔**（T40）：不再等模型 ready 才拉 Client——
  Client 懒连接（识别请求时才连 Socket，先拉起无害），就绪等待后移为两端
  拉起后的整体判定。间隔为 0 时两端背靠背拉起，启动更快（客户端初始化与
  模型加载并行）。模型加载慢的机器建议间隔给足（如 10~20 秒），否则间隔
  内立即识别会经 Client 既有「重连一次 + 托盘手动重试」兜底
- **幂等**：两端已有合法实例运行（锁文件 PID + `/proc/<pid>/exe` 精确匹配）时
  跳过拉起（也跳过对应延迟倒计时），仅确认就绪后退出 0
- **失败清理**：就绪超时/Client 拉起失败时逆序回收**本进程拉起的**子进程
  （SIGTERM → 宽限 → SIGKILL），🔴 用户自行启动的既有实例 Launcher 无权终止
- **通知**：仅三类时机——正在启动 / 启动完成（含总耗时）/ 启动失败（含日志位置）；
  `notify-send` 缺席降级为仅日志（记 warning）；托盘模式另有状态行/进度行
  与托盘气泡通知

## 退出码

| 码 | 含义 | 排查 |
|:---:|:---|:---|
| 0 | 成功（含幂等命中并确认就绪） | — |
| 2 | 已在运行（Launcher 锁冲突）或既有实例就绪确认失败 | 检查运行中实例状态；实例异常时手动结束后重试 |
| 3 | 服务端拉起/就绪失败（含超时、进程等待期死亡、协议版本不符） | 看 `log_dir`/child_service.log；模型未下载时首启耗时长，可调大 `model_ready_timeout_s` |
| 4 | 客户端拉起失败（含拉起后立即退出） | 看 `log_dir`/child_client.log；无显示环境（$DISPLAY）时客户端无法启动 |
| 5 | 配置/路径错误（组件缺失、目标解析失败、Socket 被外部占用、配置非法） | 托盘模式状态行直达「位置…」补救；或见下方「目标解析」与「故障排查」 |
| 6 | 内部错误（未预期异常兜底） | 看 `log_dir`/launcher.log |

## 配置（`config.yaml`，单一配置源）

优先级：环境变量（`ZEN_VOCOTYPE_LAUNCHER_*`）> 用户配置文件 > `config.yaml` > 代码默认值。
（用户配置文件：`$XDG_CONFIG_HOME/zen_vocotype/user_config.yaml`，三组件共享，阶段 4 T4.1b 新增层）

| 配置项 | 默认 | 说明 |
|:---|:---|:---|
| `socket_path` | 契约库默认 | 正式模式 Socket 路径（🔴 默认值唯一出处在契约库，此处仅覆盖） |
| `dev_socket_path` | 契约库默认 | dev 模式 Socket 路径 |
| `socket_wait_timeout_s` | 15 | 阶段一：Socket 可连接等待上限（秒） |
| `model_ready_timeout_s` | 180 | 阶段二：模型就绪等待上限（秒；缓存模型 P99 实测 ≈8.5s，180s 覆盖首次下载） |
| `ready_poll_interval_ms` | 200 | ready 轮询间隔（毫秒） |
| `terminate_grace_seconds` | 5 | 进程组回收 SIGTERM→SIGKILL 宽限（秒） |
| `service_binary` / `client_binary` | 无 | 正式模式二进制显式绝对路径（默认按邻接约定自动解析；托盘「位置…」项可图形化设置） |
| `service_start_delay_s` | 0 | T40：Launcher 启动后多少秒拉起服务端（0~300；托盘菜单可设） |
| `client_start_interval_s` | 0 | T40：服务端拉起后隔多少秒拉起客户端（0~300；托盘菜单可设） |
| `auto_exit_delay_s` | 8 | T40：编排成功后托盘观察窗口秒数（4~60；仅托盘模式有效；🔴 失败路径不自动退出；下限 4 秒——过短会让进程在 GNOME 托盘图标异步注册完成前退出，造成「无托盘」错觉） |
| `client_settle_timeout_s` | 10 | T42：本进程拉起客户端后的存活确认窗口秒数（0~120，0=关闭）；窗口内进程死亡判定退出码 4（修复「AppImage 引导数秒后崩溃仍报启动完成」误报）；🔴 既有实例幂等命中不适用（零附加等待） |
| `log_dir` | 契约库 `paths.DEFAULT_LOG_DIR`（`$XDG_STATE_HOME/zen_vocotype/logs`，回退 `~/.local/state/...`） | 日志目录（阶段 4 T4.1 迁 XDG） |

## 目标解析（正式模式）

查找顺序：`service_binary`/`client_binary` 显式配置 → Launcher 自身同目录
邻接约定 → **`~/AppImages` 兜底**（T40 用户约定目录，官方命名
`Zen_VocoType_*.AppImage` 与小写 `zen_vocotype_*.appimage` 均可识别）：

```
任意目录/（邻接约定，优先级最高）
├── Zen_VocoType_Launcher(.AppImage)
├── Zen_VocoType_Service(.AppImage)   ◄── 邻接自动发现
└── Zen_VocoType_Client(.AppImage)

~/AppImages/（兜底目录，邻接缺失时）
├── zen_vocotype_service.appimage     ◄── 兜底自动发现（小写命名亦可）
└── zen_vocotype_client.appimage
```

全部找不到 → 退出码 5 并提示已搜索位置（托盘模式：状态行红字错误 +
「位置…」项图形化补救）。🔴 全部路径基于程序自身位置解析，无 cwd 相对路径。
dev 模式解析仓库根 `.venv` 与两端 `main.py`（开发布局固定）。

## dev 模式隔离

| 项 | 正式模式 | dev 模式 |
|:---|:---|:---|
| 拉起目标 | 打包二进制 | `.venv/bin/python <组件>/main.py` |
| Socket | 契约库 `DEFAULT_SOCKET_PATH` | 契约库 `DEV_SOCKET_PATH` |
| Launcher 锁 | `LAUNCHER_LOCK_PATH` | `DEV_LAUNCHER_LOCK_PATH` |
| 子进程锁 | `SERVICE/CLIENT_LOCK_PATH` | `DEV_SERVICE/DEV_CLIENT_LOCK_PATH` |

两模式可并行运行互不干扰（dev 模式经环境变量向子进程注入 dev Socket 覆盖）。
dev 模式维持一次性 CLI（T40 托盘设置项对其不生效，双延迟强制为 0）。

## 打包产物（阶段 4 / T40 修订）

onedir 与 AppImage 双形态经 `tools/build.py --component launcher [--appimage]` 构建
（详见仓库根 README「打包产物使用说明」节）。本组件要点：

- Qt 仅托盘（PySide6，T40 引入），ML 栈 spec 全排除（产物约 100~130MiB）
- AppImage 形态邻接解析经 `APPIMAGE` 环境变量定位（挂载点内路径无邻接意义）；
  既有实例身份识别经 `/proc/<pid>/environ` 的 `APPIMAGE` 精确比对
- 托盘相关 import 全部延迟到托盘分支内——`--version` 冒烟探针零 Qt 触达
- 冷启动耗时结构化字段 `启动耗时 T1_socket_connect_s=/T2_model_ready_s=/T_total_s=`
  写入 launcher.log（阶段 4 选型七口径）

## 故障排查

| 现象 | 处理 |
|:---|:---|
| 托盘未出现 | 无显示环境时已自动回退一次性 CLI（日志有 warning）；桌面托盘服务异常时同样回退，功能不受影响 |
| 托盘状态行「✗ 目标解析失败」 | 组件二进制不在邻接目录：经菜单「Service/Client 位置…」选择二进制（持久化），或把三个 AppImage 放回同一目录 |
| 通知「已在运行」退出码 2 | 已有实例运行；确认托盘可用即无需再启动。实例异常时：`kill <锁文件内 PID>` 后重试（锁文件在 `$XDG_RUNTIME_DIR` 或 `~/.local/run`） |
| 退出码 3 且日志显示模型下载中 | 首次使用需下载模型（数百 MB），属正常；超时可调大 `model_ready_timeout_s` |
| 退出码 4「客户端拉起后退出」 | 客户端在存活确认窗口（`client_settle_timeout_s`，默认 10 秒）内死亡：典型为无显示环境下 Qt 崩溃——查 `child_client.log` 尾部确认（T42 前此场景误报「启动完成」） |
| 拉起后很快识别报「服务端未就绪」 | 模型仍在加载：调大 `client_start_interval_s`（如 10~20 秒），或在 Client 托盘「重试连接服务端」 |
| 托盘「拉起来就消失」 | 非崩溃：`auto_exit_delay_s` 观察窗口结束自动退出（默认 8 秒）；失败时托盘会停留，对比可感知。两端已在运行时编排幂等秒完，窗口从成功时刻起算 |
| 退出码 5「Socket 被外部占用」 | Socket 路径被非本组件进程占用；🔴 Launcher 不会 unlink 他人 Socket，请配置 `socket_path` 换路径 |
| 无桌面通知 | `notify-send` 缺席时已降级为仅日志（warning），行为不受影响 |

日志：`log_dir`/launcher.log（编排）、`log_dir`/child_service.log / `log_dir`/child_client.log（默认 `$XDG_STATE_HOME/zen_vocotype/logs`）
（两端子进程输出，每次拉起新开）。

## 测试

```bash
.venv/bin/python -m pytest Zen_VocoType_Launcher/tests/ -q
```
