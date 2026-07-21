# Zen_VocoType_Service（服务端）

## 职责

提供 Zen_VocoType 的核心主服务，负责后台业务逻辑与数据处理，为客户端提供能力支撑。

## 定位

- 作为后台服务进程运行，不直接与用户交互
- 由 `Zen_VocoType_Launcher` 启动器统一拉起，也可**独立启动运行**（自身功能完整，监听 Socket 等待连接）
- 通过约定的通信方式与 `Zen_VocoType_Client`（客户端）协作
- **独立性约束**：本组件为独立项目，不 import 其他两个组件目录下的任何代码；对外协作仅通过 Unix Socket 协议

## 与其他组件的关系

| 组件 | 关系 |
| --- | --- |
| Zen_VocoType_Launcher | 由启动器启动，与其余进程协调避免冲突 |
| Zen_VocoType_Client | 为客户端提供主服务，客户端依赖本服务运行 |

## 目录结构与配置（阶段 0 定稿）

```
Zen_VocoType_Service/
├── main.py                 # 入口（当前为骨架，实现属阶段 1）
├── pyproject.toml          # 独立项目元数据与依赖声明
├── config.yaml             # 本组件唯一配置文件
├── src/zen_vocotype_service/
│   └── config.py           # 唯一配置入口 Settings（pydantic-settings）
├── assets/                 # 托盘/通知图标（阶段 1 接入）
├── logs/                   # 运行日志
└── models/                 # 模型外置目录（不随二进制分发）
```

- **单一配置源**：`config.py` 的 `Settings` 类 + `config.yaml` + 环境变量（前缀 `ZEN_VOCOTYPE_SERVICE_`），优先级：显式入参 > 环境变量 > config.yaml > 代码默认值
- Socket 路径默认值唯一出处在契约库 `zen_vocotype_protocol.paths`，本组件仅允许覆盖
- 协议语义见 `文档/通信协议设计_v1.0.md`；协议常量禁止在本组件重复定义
