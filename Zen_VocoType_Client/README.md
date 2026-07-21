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

## 目录结构与配置（阶段 0 定稿）

```
Zen_VocoType_Client/
├── main.py                 # 入口（当前为骨架，实现属阶段 2）
├── pyproject.toml          # 独立项目元数据与依赖声明
├── config.yaml             # 本组件唯一配置文件
├── src/zen_vocotype_client/
│   ├── config.py           # 唯一配置入口 Settings（pydantic-settings）
│   ├── hotkey/             # 全局热键（pynput；HotkeyBackend 抽象预留 evdev/Portal）
│   ├── recorder/           # 录音（sounddevice，实例复用）
│   ├── transcribe/         # Socket 客户端与状态机
│   ├── output/             # 文字输出（剪贴板+Ctrl+V 主路径，xdotool 降级）
│   └── tray/               # 托盘与 UI（PySide6，含无托盘降级模式）
├── assets/                 # 托盘图标（阶段 2 接入）
└── logs/                   # 运行日志
```

- **单一配置源**：`config.py` 的 `Settings` 类 + `config.yaml` + 环境变量（前缀 `ZEN_VOCOTYPE_CLIENT_`）
- 技术栈（选型定稿）：PySide6 / pynput / sounddevice / 剪贴板+Ctrl+V 改进版
- 协议语义见 `文档/通信协议设计_v1.0.md`；协议常量禁止在本组件重复定义
