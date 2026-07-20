# Zen_VocoType_Client（客户端）

## 职责

提供用户交互界面，是用户直接操作 Zen_VocoType 的入口。

## 定位

- 面向用户的前端程序，负责界面展示与交互
- 由 `Zen_VocoType_Launcher` 启动器统一拉起，也可**独立启动运行**；服务端缺席时须明确提示「服务端未运行」，不得崩溃或静默失败
- 依赖 `Zen_VocoType_Service`（服务端）提供的主服务完成实际功能
- **独立性约束**：本组件为独立项目，不 import 其他两个组件目录下的任何代码；对外协作仅通过 Unix Socket 协议

## 与其他组件的关系

| 组件 | 关系 |
| --- | --- |
| Zen_VocoType_Launcher | 由启动器启动，与其余进程协调避免冲突 |
| Zen_VocoType_Service | 调用服务端能力，服务端是功能实现的后盾 |
