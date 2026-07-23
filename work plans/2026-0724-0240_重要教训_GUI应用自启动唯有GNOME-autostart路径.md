> **状态**：🔴 **重要常驻教训文档**——凡涉及「开机自启动」的讨论、选型、代码
> 改动，必须先读本文；结论不可回退
> **范围**：全项目（自启动机制选型）
> **时间**：2026-07-24 02:40（创建，实机验证定版）
> **关联**：《2026-0723-1510_启动器托盘不显示与自启动失效诊断报告》、
> 《2026-0723-1705_systemd自启动下启动器托盘失效诊断报告》、
> 《2026-0723-1720_headless健壮性与自启动支持修复计划》（T41/T42/T43）

# 🔴 重要教训：GUI/托盘应用的开机自启动，GNOME autostart 是唯一正确路径

## 0. 一句话教训

**带系统托盘的 GUI 应用，开机自启动只能走桌面环境 autostart 机制
（`~/.config/autostart/`）；systemd 用户服务在机制上必然失败**——它启动
早于图形会话环境注入，应用拿不到 `DISPLAY`/`WAYLAND_DISPLAY`，托盘类
应用全部失效。本项目用一整天、两次实机事故、三次重启验证换来这条结论。

## 1. 最终验证成功的配置（2026-07-24 实机定版）

```bash
# 1. 禁用并停止 systemd 服务（历史残留，必须清除）
systemctl --user disable zen_vocotype.service
systemctl --user stop zen_vocotype.service

# 2. 创建 GNOME autostart 桌面文件（Exec 路径逐字符核对实机文件名，
#    本机为小写 .appimage）
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/zen_vocotype.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Zen Vocotype
Exec=~/AppImages/zen_vocotype_launcher.appimage
Icon=zen_vocotype
Comment=Zen Vocotype Launcher
X-GNOME-Autostart-enabled=true
EOF
```

> 产品化等价物：`tools/install_desktop.py --dir <目录> --autostart`（T43），
> 卸载经 `tools/uninstall_desktop.py` 对称删除。手写条目与产品化条目二选一，
> 🔴 不得并存多个 zen 相关 autostart 条目（双启动教训，见 §4 第三幕）。

## 2. 验证证据（2026-07-24 02:32 重启，launcher.log 逐行）

| 时刻 | 日志事实 | 含义 |
|---|---|---|
| 02:32:41 | `Launcher 启动（模式：prod，托盘）` | autostart 触发，**单次**启动 |
| —（无） | **无**「托盘模式不可用…回退一次性 CLI」 | DISPLAY 已就绪，托盘模式正常进入（systemd 时代此 warning 必现） |
| 02:32:46 / 02:32:54 | 拉起 service（4329）/ client（4525） | 按用户配置的 4s/8s 延迟编排 |
| 02:32:54 | 阶段二通过：模型已就绪（fun-asr-nano） | 就绪判定一次通过 |
| —（无） | **无**「客户端拉起后存活确认期内死亡」 | 客户端首启即活（T42 窗口零检出） |
| 02:33:04 | `编排完成（总耗时 22.0s）` → 两端幂等命中 | 全链路成功 |
| 02:34:03 | 强制退出兜底触发，Launcher 退出 | 设计行为（两端不受影响） |

用户视角：托盘图标出现、**零桌面通知**（通知是 CLI 回退路径专属，本次
一条都没有——与诊断报告 §2 的预期校准完全一致）。

## 3. 技术原理：为什么 systemd 必败、autostart 必胜

两条自启动通道的本质差异在**环境变量的注入时序**：

| 环境变量 | 用途 | systemd 用户服务启动时 | GNOME autostart 执行时 |
|---|---|---|---|
| `DISPLAY` / `WAYLAND_DISPLAY` | X11/Wayland 连接（**托盘、Qt、pynput 的命脉**） | ❌ 尚未注入（gnome-session 之后才经 `systemctl --user import-environment` 导入） | ✅ 已就绪 |
| `DBUS_SESSION_BUS_ADDRESS` | 桌面通知（notify-send） | ✅ 有（用户总线随 systemd 用户管理器启动） | ✅ 有 |

由此推导出的全部事故现象都是**机制性必然**：

- systemd 路径下 Launcher 的 `display_available()` 探测必败 → headless
  保护正确触发 → 回退 CLI → **托盘从未创建**；
- 但 notify-send 能工作（D-Bus 在）→ 用户看到「启动成功」通知却找不到
  托盘——**「通知能弹、托盘没有」的诡异现象，根源就是两行环境变量的
  注入时差**；
- 被拉起的 Client 继承同样的无 DISPLAY 环境 → pynput import 连不上 X
  （`DisplayNameError('')`）/ Qt 硬崩 → 秒崩后被旧版 Launcher 误报成功。

autostart 由 gnome-session 在图形会话建立后执行，环境天然齐备——
**这不是某个实现的巧合，而是桌面环境的设计契约**。

## 4. 完整事故史（四幕，教训的全部成本）

| 幕 | 时间 | 事件 | 教训 |
|---|---|---|---|
| 一 | 2026-07-23 16:55 | systemd 用户服务自启动：两条「启动成功」通知、零托盘 | systemd 用户服务不能用于 GUI/托盘应用自启动（本文核心） |
| 二 | 2026-07-23 18:30 | 迁 autostart 后首次重启：Launcher 托盘模式正常，但客户端首启崩于 pynput 连不上 X（开机早期 X 未完全就绪），T42 存活确认如实报败，手动「立即启动」即恢复 | ① T42 把旧版的「谎报成功」变成了「如实报败」，修复价值实证；② 开机早期存在 X 就绪竞态（§6 残留项） |
| 三 | 2026-07-24 02:14 | 历史残留未清理干净：一次开机出现**两个** Launcher（一个无 DISPLAY 回退 CLI、一个正常托盘），客户端再崩一次 | 🔴 自启动入口必须唯一：迁移方案时先把旧机制（systemd 服务、多余条目）清除干净，再验证；「边留旧边试新」必然制造混乱现场 |
| 四 | 2026-07-24 02:32 | 清理后重启：**单次** autostart 启动、托盘模式、22 秒编排成功、零通知 | §1 配置定版验证通过 |

## 5. 防线固化清单（结论已写入代码/文档的位置）

| 位置 | 内容 |
|---|---|
| `tools/desktop_entry.py` docstring | 🔴 禁止改用/加回 systemd 用户服务（附事故日期与报告指引） |
| `tools/install_desktop.py` docstring | `--autostart` 用法 + systemd 警示 + 双启动防范（先 disable 旧服务） |
| 根 `README.md`「开机自启动」节 | 正确路径、预期效果（Launcher 托盘短暂、常驻托盘为 Service/Client、成功零通知）、Exec 路径注意事项 |
| Launcher `app.py` / `main.py` | headless 探测与 CLI 回退（机制保护，非缺陷——systemd 场景下是它防止了 Qt 硬崩） |
| Client `main.py`（T41） | DISPLAY 探测 + 退出码 6（headless 确定性报错，禁止 Qt 裸崩） |
| Launcher `orchestrator.py`（T42） | Client 存活确认窗口（秒崩不再被误报为成功） |

## 6. 红线与已知边界

### 🔴 红线（不可回退）

1. 自启动机制只有 autostart 一条路径；任何「systemd 用户服务更方便管理」
   的提议一律拒绝（机制性必败，无补丁可救——显示号硬编码在多会话/
   Wayland 下脆弱，时序 hack 不可靠）；
2. 迁移/变更自启动配置时，**先彻底清除旧机制再验证**（第三幕教训）；
3. autostart 条目全机唯一（zen 相关条目仅一个），Exec 路径逐字符核对
   实机文件名（大小写敏感，失效时桌面环境**静默失败**）。

### 已知边界（未关闭，非阻塞）

- **开机早期 X 就绪竞态**（第二幕）：autostart 保证 DISPLAY 存在，但不
  保证 X 服务已开始接受连接；重负载开机时客户端首启仍可能崩一次
  （T42 会如实报败，托盘点「立即启动」即恢复）。本次 02:32 验证未复发。
  如复发频繁可立项：Client 启动期 X 连接有限重试，或 autostart 条目加
  `X-GNOME-Autostart-Delay=<秒>`。
- **T43 产品化安装未实机执行**：当前 autostart 条目为手写（§1）；
  `install_desktop.py --autostart` 有 8 项单测覆盖，首次实机使用时可
  对照本条目内容核验。

## 7. 本文档的使用方式

- 任何涉及自启动的新计划/评审，将本文列入「关联」并逐条核对 §6 红线；
- 新人/新会话问起「为什么不用 systemd 做自启动」，直接指向本文 §3；
- 若未来桌面环境变更（如迁移 Wayland-only、更换 DE），需重验 §3 的
  时序结论并重写本文。
