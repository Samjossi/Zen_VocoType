# Zen_VocoType_Protocol

Zen_VocoType 通信协议契约库（第四组件，库—使用方关系）。

- **职责**：复合帧格式、action 常量、错误码、协议版本、Socket 路径等全局关键项的**唯一出处**
- **接入方式**：开发时 `uv pip install -e ./Zen_VocoType_Protocol`（editable install）；打包时随各端产物内嵌
- **红线**：Service / Client / Launcher 三组件文件夹之间禁止任何相对路径 import；禁止各端自行重复定义协议常量

协议语义详见 `文档/通信协议设计_v1.0.md`，常量以本库代码为单一出处。
协议版本号（`PROTOCOL_VERSION`，两位数 major.minor）的唯一真相为仓库根
`../versions.toml`，由 `../tools/sync_versions.py` 同步至 `version.py`，禁止手改。
