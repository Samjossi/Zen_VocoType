# Zen_VocoType_Launcher（启动器）

## 职责

统一启动 `Zen_VocoType_Service`（服务端）与 `Zen_VocoType_Client`（客户端），并确保两个进程之间不会发生冲突。

## 定位

- 作为整个应用的启动入口，自身可**独立运行**
- 负责按正确的顺序拉起服务端与客户端
- 协调两个进程的生命周期，处理端口占用、重复启动等冲突场景；某端缺失时报错并给出明确提示
- **独立性约束**：本组件为独立项目，不 import 其他两个组件目录下的任何代码；仅以子进程方式拉起两端，不参与其内部逻辑

## 与其他组件的关系

| 组件 | 关系 |
| --- | --- |
| Zen_VocoType_Service | 由本启动器拉起，先于客户端就绪 |
| Zen_VocoType_Client | 由本启动器拉起，依赖服务端已启动 |

## 目录结构与配置（阶段 0 定稿）

```
Zen_VocoType_Launcher/
├── main.py                 # 入口（当前为骨架，实现属阶段 3）
├── pyproject.toml          # 独立项目元数据与依赖声明
├── config.yaml             # 本组件唯一配置文件
├── src/zen_vocotype_launcher/
│   └── config.py           # 唯一配置入口 Settings（pydantic-settings）
├── assets/                 # 通知/桌面图标（阶段 3 接入）
└── logs/                   # 运行日志
```

- **单一配置源**：`config.py` 的 `Settings` 类 + `config.yaml` + 环境变量（前缀 `ZEN_VOCOTYPE_LAUNCHER_`）
- `--dev` 开发模式使用独立 Socket 路径（默认值唯一出处在契约库 `zen_vocotype_protocol.paths.DEV_SOCKET_PATH`，位于用户私有运行目录，与正式版隔离）
- 就绪等待走协议级 `ready` 接口轮询，🔴 禁止固定 sleep 充当同步手段
- 协议语义见 `文档/通信协议设计_v1.0.md`；协议常量禁止在本组件重复定义
