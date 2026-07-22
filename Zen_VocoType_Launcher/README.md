# Zen_VocoType_Launcher

Zen_VocoType 启动器：按序拉起服务端与客户端、协议级就绪等待、单实例锁、失败清理。

**职责边界**（重写大纲 §3.1）：只做进程编排——拉起 Service → 等待模型就绪 → 拉起
Client → 通知完成 → **自身退出**（选型七方案 A：拉起确认后即退出，不做驻留监控）。
两端退出后独立存活；再次执行 Launcher 即幂等健康检查。

## 用法

```bash
# 正式模式：拉起打包形态的两端（二进制解析见下「目标解析」）
python main.py

# 开发模式：用当前 .venv 拉起两端源码（Socket/锁文件与正式版隔离）
python main.py --dev
```

## 编排流程

```
抢单实例锁 → 识别既有实例（幂等分支）→ 拉起 Service → 两阶段就绪等待
（Socket 可连 → ready 轮询模型就绪）→ 拉起 Client → 确认存活
→ 通知「启动完成（含总耗时）」→ 释放锁退出 0
```

- **幂等**：两端已有合法实例运行（锁文件 PID + `/proc/<pid>/exe` 精确匹配）时
  跳过拉起，仅确认就绪后退出 0
- **失败清理**：就绪超时/Client 拉起失败时逆序回收**本进程拉起的**子进程
  （SIGTERM → 宽限 → SIGKILL），🔴 用户自行启动的既有实例 Launcher 无权终止
- **通知**：仅三类时机——正在启动 / 启动完成（含总耗时）/ 启动失败（含日志位置）；
  `notify-send` 缺席降级为仅日志（记 warning）

## 退出码

| 码 | 含义 | 排查 |
|:---:|:---|:---|
| 0 | 成功（含幂等命中并确认就绪） | — |
| 2 | 已在运行（Launcher 锁冲突）或既有实例就绪确认失败 | 检查运行中实例状态；实例异常时手动结束后重试 |
| 3 | 服务端拉起/就绪失败（含超时、进程等待期死亡、协议版本不符） | 看 `logs/child_service.log`；模型未下载时首启耗时长，可调大 `model_ready_timeout_s` |
| 4 | 客户端拉起失败（含拉起后立即退出） | 看 `logs/child_client.log`；无显示环境（$DISPLAY）时客户端无法启动 |
| 5 | 配置/路径错误（组件缺失、目标解析失败、Socket 被外部占用、配置非法） | 见下方「目标解析」与「故障排查」 |
| 6 | 内部错误（未预期异常兜底） | 看 `logs/launcher.log` |

## 配置（`config.yaml`，单一配置源）

优先级：环境变量（`ZEN_VOCOTYPE_LAUNCHER_*`）> `config.yaml` > 代码默认值。

| 配置项 | 默认 | 说明 |
|:---|:---|:---|
| `socket_path` | 契约库默认 | 正式模式 Socket 路径（🔴 默认值唯一出处在契约库，此处仅覆盖） |
| `dev_socket_path` | 契约库默认 | dev 模式 Socket 路径 |
| `socket_wait_timeout_s` | 15 | 阶段一：Socket 可连接等待上限（秒） |
| `model_ready_timeout_s` | 180 | 阶段二：模型就绪等待上限（秒；缓存模型 P99 实测 ≈8.5s，180s 覆盖首次下载） |
| `ready_poll_interval_ms` | 200 | ready 轮询间隔（毫秒） |
| `terminate_grace_seconds` | 5 | 进程组回收 SIGTERM→SIGKILL 宽限（秒） |
| `service_binary` / `client_binary` | 无 | 正式模式二进制显式绝对路径（默认按邻接约定自动解析） |
| `log_dir` | 组件根 `logs/` | 日志目录 |

## 目标解析（正式模式）

`service_binary`/`client_binary` 显式配置 → Launcher 自身同目录邻接约定：

```
任意目录/
├── Zen_VocoType_Launcher(.AppImage)
├── Zen_VocoType_Service(.AppImage)   ◄── 邻接自动发现
└── Zen_VocoType_Client(.AppImage)
```

两处都找不到 → 退出码 5 并提示缺失位置。🔴 全部路径基于程序自身位置解析，
无 cwd 相对路径。dev 模式解析仓库根 `.venv` 与两端 `main.py`（开发布局固定）。

## dev 模式隔离

| 项 | 正式模式 | dev 模式 |
|:---|:---|:---|
| 拉起目标 | 打包二进制 | `.venv/bin/python <组件>/main.py` |
| Socket | 契约库 `DEFAULT_SOCKET_PATH` | 契约库 `DEV_SOCKET_PATH` |
| Launcher 锁 | `LAUNCHER_LOCK_PATH` | `DEV_LAUNCHER_LOCK_PATH` |
| 子进程锁 | `SERVICE/CLIENT_LOCK_PATH` | `DEV_SERVICE/DEV_CLIENT_LOCK_PATH` |

两模式可并行运行互不干扰（dev 模式经环境变量向子进程注入 dev Socket 覆盖）。

## 故障排查

| 现象 | 处理 |
|:---|:---|
| 通知「已在运行」退出码 2 | 已有实例运行；确认托盘可用即无需再启动。实例异常时：`kill <锁文件内 PID>` 后重试（锁文件在 `$XDG_RUNTIME_DIR` 或 `~/.local/run`） |
| 退出码 3 且日志显示模型下载中 | 首次使用需下载模型（数百 MB），属正常；超时可调大 `model_ready_timeout_s` |
| 退出码 5「Socket 被外部占用」 | Socket 路径被非本组件进程占用；🔴 Launcher 不会 unlink 他人 Socket，请配置 `socket_path` 换路径 |
| 无桌面通知 | `notify-send` 缺席时已降级为仅日志（warning），行为不受影响 |

日志：`logs/launcher.log`（编排）、`logs/child_service.log` / `logs/child_client.log`
（两端子进程输出，每次拉起新开）。

## 测试

```bash
.venv/bin/python -m pytest Zen_VocoType_Launcher/tests/ -q
```
