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
