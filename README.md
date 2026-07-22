# Zen_VocoType

Linux 桌面语音输入三件套：按住热键说话，松开自动识别并粘贴到当前输入位置。

- **Zen_VocoType_Service**：ASR 推理服务（FunASR/modelscope，Unix Socket 协议）
- **Zen_VocoType_Client**：客户端（全局热键 / 录音 / 剪贴板输出 / 托盘）
- **Zen_VocoType_Launcher**：启动器（双击一次拉起全套，幂等、崩溃回收）
- **Zen_VocoType_Protocol**：三组件共享的协议契约库（帧格式 / action / 错误码 / 路径唯一出处）

开发期运行与测试命令见 [`常用命令.md`](常用命令.md)；各组件细节见各自 README。

---

## 打包产物使用说明（阶段 4）

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
Launcher 查找顺序：同目录 AppImage → 同目录 onedir 目录 → 配置显式路径。

构建本地产物（需 appimagetool，见下）：

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

### 数据与配置位置（XDG 分层，AppImage 只读挂载零写入）

| 类别 | 默认位置 | 说明 |
|:---|:---|:---|
| 模型缓存 | `~/.local/share/zen_vocotype/models` | MODELSCOPE_CACHE；可用托盘「设置模型目录…」自选（重启生效） |
| 日志 | `~/.local/state/zen_vocotype/logs` | service/client/launcher.log 轮转 |
| 用户配置 | `~/.config/zen_vocotype/user_config.yaml` | 托盘写入的覆盖项（如 models_dir） |
| Socket/锁 | `$XDG_RUNTIME_DIR` | 回退 `~/.local/run` |

配置链：组件默认值 → 包内 `config.yaml` → 用户配置文件 → 环境变量
（前缀 `ZEN_VOCOTYPE_<组件>_`，如 `ZEN_VOCOTYPE_SERVICE_MODELS_DIR`）。

### 离线模型手工放置（无网环境补救）

1. 在有网机器下载模型（首次启动会自动下载，或经 modelscope 网页/CLI）
2. 将缓存目录按原布局复制到目标机 `~/.local/share/zen_vocotype/models/`：
   布局形如 `models/<组织>--<模型名>/snapshots/...`（默认 fun-asr-nano
   家族共约 2.0GB：Fun-ASR-Nano-2512 + fsmn-vad）
3. 校验：启动 Service，日志出现「服务就绪（ready）」即缓存命中直载成功；
   缓存未命中会自动尝试在线下载（离线环境将报加载失败，日志有明确原因）

也可用 Service 托盘「设置模型目录…」指向已含模型缓存的任意目录
（如外置盘），保存后重启生效；v1 不做旧目录模型迁移，请手工搬移。

### 故障排查

- FUSE 缺失无法运行 AppImage：`./Zen_VocoType_Launcher.AppImage --appimage-extract-and-run`
  兜底，或安装 fuse（`sudo apt install fuse3`）；onedir 裸产物为二级分发物可直接用
- 日志不可写：组件自动降级 stderr 输出并记 warning，不崩溃
- 托盘图标缺失：记 warning 降级显示（不静默），请检查产物完整性
