# Zen_VocoType

Linux 桌面语音输入三件套：按住热键说话，松开自动识别并粘贴到当前输入位置。

- **Zen_VocoType_Service**：ASR 推理服务（FunASR/modelscope，Unix Socket 协议）
- **Zen_VocoType_Client**：客户端（全局热键 / 录音 / 剪贴板输出 / 托盘）
- **Zen_VocoType_Launcher**：启动器（双击一次拉起全套，幂等、崩溃回收）
- **Zen_VocoType_Protocol**：三组件共享的协议契约库（帧格式 / action / 错误码 / 路径唯一出处）

开发期运行与测试命令见 `常用命令.md`；各组件细节见各自 README。

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

### 开机自启动（T43，GNOME autostart）

```bash
# 安装桌面入口的同时安装自启动条目（幂等）
.venv/bin/python tools/install_desktop.py --dir <AppImage 摆放目录> --autostart
```

- 条目写入 `~/.config/autostart/zen-vocotype.desktop`，桌面环境在**图形会话
  就绪后**执行（DISPLAY 齐备，托盘正常）；卸载时与菜单条目一并删除
- 🔴 请勿改用 systemd 用户服务做自启动：systemd 服务早于图形会话环境
  注入启动，Launcher 检测不到 DISPLAY 会回退一次性 CLI（托盘全灭）——
  2026-07-23 实机事故，详见 `work plans/2026-0723-1705_systemd自启动下启动器托盘失效诊断报告.md`
- 若曾手工配置 systemd 自启动服务，先 `systemctl --user disable --now <单元>`
  再装本条目（防双启动）
- 预期效果：登录后 Launcher 托盘**短暂出现**（编排成功按 `exit_after_success_s`
  自退，默认 60 秒），常驻托盘为 Service/Client 图标；成功路径**无桌面通知**
- 移动/改名 AppImage 后需重跑安装脚本（条目 Exec 按安装时路径渲染）

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
   布局形如 `models/<组织>--<模型名>/snapshots/master/<文件>`（默认 fun-asr-nano
   为 GGUF 权重，共约 1.2GB：`FunAudioLLM--Fun-ASR-Nano-GGUF` 下
   `funasr-encoder-f16.gguf` + `qwen3-0.6b-q8_0.gguf`，
   外加 `FunAudioLLM--fsmn-vad-GGUF` 下 `fsmn-vad.gguf`）
3. 校验：启动 Service，日志出现「服务就绪（ready）」即缓存命中直载成功；
   缓存未命中会自动尝试在线下载（离线环境将报加载失败，日志有明确原因）

也可用 Service 托盘「设置模型目录…」指向已含模型缓存的任意目录
（如外置盘），保存后重启生效；v1 不做旧目录模型迁移，请手工搬移。

### 故障排查

- FUSE 缺失无法运行 AppImage：`./Zen_VocoType_Launcher.AppImage --appimage-extract-and-run`
  兜底，或安装 fuse（`sudo apt install fuse3`）；onedir 裸产物为二级分发物可直接用
- 日志不可写：组件自动降级 stderr 输出并记 warning，不崩溃
- 托盘图标缺失：记 warning 降级显示（不静默），请检查产物完整性
