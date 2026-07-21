"""Zen_VocoType_Client 包（阶段 0 骨架）。

模块划分（实现属阶段 2，大纲 §5-10：拆为独立模块，禁止上帝类）：

- ``hotkey``     全局热键监听（pynput 后端；HotkeyBackend 抽象预留 evdev/Portal）
- ``recorder``   音频录制（sounddevice，实例生命周期内复用）
- ``transcribe`` Socket 客户端（长连接复用）与状态机
- ``output``     文字输出（剪贴板+Ctrl+V 改进版主路径，xdotool 降级）
- ``tray``       托盘与 UI（PySide6，含无托盘降级模式）
"""
