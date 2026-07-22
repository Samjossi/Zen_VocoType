> **状态**：已确认（决策点 2026-07-23 07:32 拍板：替代 / vendor / q8_0，待实施）
> **范围**：`Zen_VocoType_Service`（新引擎类型、推理子进程、模型下载、打包）
> **时间**：2026-07-23 07:26（设计，UTC+8）/ 2026-07-23 07:32（确认）
> **优先级**：高

# fun-asr-nano 提速修复（GGUF/llama.cpp 集成）实现计划

## 一、背景与目标

默认引擎 fun-asr-nano（PyTorch 路径）上屏手感慢（RTF 0.29–0.34，比
paraformer 慢约 10 倍），实测报告
[`2026-07-23-07_fun-asr-nano速度问题实测报告.md`](2026-07-23-07_fun-asr-nano速度问题实测报告.md)
结论：

- 流程无延迟，慢在 PyTorch 推理本身；核显加速硬件上不成立；
- **官方 GGUF/llama.cpp CPU 运行时实测快 2~4.4 倍（长音频 RTF 0.062），
  精度损失 <0.1% CER，子进程调用开销仅 ~0.1s（mmap 热页缓存）**。

**目标**：将 GGUF 运行时集成为服务端新引擎类型，使 fun-asr-nano 以
「说完即贴」的手感继续担任默认引擎，同时保留其新词覆盖优势。

## 二、方案选型（已定方向，细节待阶段 1 调研确认）

**新增 `engine_type = "funasr-gguf"`**：每次识别以**子进程**调用官方预编译
`llama-funasr-cli`（固定版本 v0.1.4），stdout 解析文本返回。

| 维度 | 决策 | 理由 |
|---|---|---|
| 调用形态 | 每请求一次子进程 | 实测进程开销 ~0.1s 可忽略；崩溃隔离（子进程挂不拖垮服务端）；无常驻进程管理负担；与 worker 单线程模型天然兼容 |
| 二进制分发 | **vendor 进仓库** `Zen_VocoType_Service/bin/llama-funasr-cli`（pin v0.1.4，linux-x64-avx2）✅ 已拍板 | 版本锁定、离线可用、打包确定性强；体积待查（预计 <50MB） |
| GGUF 权重分发 | **首启下载**到 models_dir 统一缓存（不进 git，encoder 470MB + q8_0 805MB + vad，合计 ~1.3GB） | 与现有「模型缓存统一下载」机制一致；支持手动放置（无网补救） |
| 量化档位 | **q8_0**（805MB，CER 8.30%，三档中保真最高）✅ 已拍板 | 体积换精度；速度同为官方 6.0× 实时量级 |
| 引擎关系 | GGUF 版**替代** PyTorch 版 fun-asr-nano（同名同模型，仅运行时不同）✅ 已拍板 | 避免注册表出现两个同模型条目混淆用户；PyTorch 版可随时经 extra_params 恢复 |

注册表终态（建议）：`fun-asr-nano`（GGUF 运行时，默认）/ `sensevoice-small`
/ `qwen3-asr-1.7b`，仍三款。

## 三、关键设计点

### 3.1 ModelEntry 适配（复用现有字段，不加新字段）

```yaml
fun-asr-nano:
  engine_type: funasr-gguf
  local_path: <models_dir>/gguf/fun-asr-nano   # 目录约定
  extra_params:
    encoder: funasr-encoder-f16.gguf            # 以下三项为目录内文件名，有默认值
    llm: qwen3-0.6b-q8_0.gguf                   # 已拍板 q8_0 档位（保真最高）
    vad: fsmn-vad.gguf
```

- `local_path` 指向 GGUF 目录（三文件齐全视为已缓存，缺一触发下载补齐）
- 权重源优先 ModelScope（与现有缓存/无网补救体系一致）；
  🔴 ModelScope 是否有 GGUF 镜像为阶段 1 首要调研项，无则用
  `huggingface_hub`（已是传递依赖）下载并落 models_dir 统一布局

### 3.2 音频输入：临时 WAV 桥接

CLI 只接受音频文件路径，`run_inference` 的 float32 数组需落临时 WAV：

- 位置：XDG runtime 层（与 Socket 同目录，`paths` 契约库衍生子目录），
  🔴 禁止系统 /tmp、禁止组件根（AppImage 只读）
- 生命周期：每请求独立文件、用完即删；服务启动时清空该子目录（防崩溃残留）
- 写入：标准库 `wave`（与客户端 recording_store 同法，16kHz/16bit/单声道
  冻结参数）

### 3.3 输出解析与结构校验

- 阶段 1 调研 CLI 是否有 `--json`/`--quiet` 等机器可读输出开关；
  有则用，无则按「剔除已知日志前缀行（`[`/`~`/`sched_` 等）后取剩余文本行」
  解析，🔴 解析规则配套单测固化
- 返回码非 0 → `RuntimeError`（stderr 尾部进 message）；空文本 →
  「返回结构非法」（沿用现有归一化语义）
- 子进程 timeout = `infer_timeout_s`（300s，GGUF RTF 0.062 下 10 分钟
  录音仅 ~37s，预算充裕）；🔴 禁止 shell=True，参数全 list

### 3.4 selftest 与加载语义

- `load_model`（funasr-gguf 分支）：校验二进制存在且可执行 → 校验/补齐
  三份权重 → 返回 LoadedModel（model 字段持有路径配置对象）
- `selftest`：复用统一入口，自检 PCM → 临时 WAV → 跑一次 CLI，
  顺带完成 mmap 页缓存预热（🔴 切换后首次识别不吃冷启动亏）

### 3.5 打包

- spec 收编 `bin/llama-funasr-cli`（datas + 可执行权限；AppImage 同）；
  附 license 文件（llama.cpp MIT / FunASR Apache-2.0，vendor 义务）
- 空 models_dir 首启验收：GGUF 三文件自动下载 + 自检 + 识别全链路
- 🔴 通用 x64 包（保守指令集）与 x64-avx2 包的选择：本产品目标机即本机
  （Zen 5 支持 AVX2/AVX512），vendor avx2 版；通用版链接记录于 README 备用

## 四、阶段划分与任务清单

### 阶段 1：调研确认（预计 1 小时）

| # | 任务 | 验收点 |
|---|---|---|
| 1.1 | ModelScope 是否有 Fun-ASR-Nano-GGUF / fsmn-vad-GGUF 镜像 | 确定权重下载源与缓存布局 |
| 1.2 | CLI 参数与输出格式核查（`--help`、有无 json/quiet、退出码语义） | 解析规则定稿 |
| 1.3 | 二进制体积、license 文件、官方校验和（如有） | vendor 清单定稿 |

### 阶段 2：引擎集成（预计 3 小时）

| # | 任务 | 涉及文件 |
|---|---|---|
| 2.1 | vendor 二进制 + license 入 `Zen_VocoType_Service/bin/` | 仓库 |
| 2.2 | `ModelEntry.engine_type` 增加 `"funasr-gguf"` 取值；loader 新分支（二进制/权重校验、缺失下载） | `config.py`、`loader.py` |
| 2.3 | `run_inference` 新分支：临时 WAV → 子进程 → 解析 → 归一化返回 | `loader.py` |
| 2.4 | selftest 适配（复用统一入口） | `loader.py` |
| 2.5 | 单测：参数构造、输出解析（含异常行/空输出/非零返回码）、临时文件清理、旧引擎回归 | `tests/test_engine_branch.py` 等 |

### 阶段 3：注册表与默认迁移（预计 1 小时）

| # | 任务 | 验收点 |
|---|---|---|
| 3.1 | `fun-asr-nano` 条目切换为 funasr-gguf 配置（PyTorch 配置以注释/文档留存恢复方法） | 注册表仍三款 |
| 3.2 | 真机验证：切换 → 识别 → RTF 复核（对标实测报告数据） | 手感达标 |
| 3.3 | description 更新（速度定位修正：GGUF RTF 实测值） | 表述准确 |

### 阶段 4：打包与端到端验收（预计 2 小时）

| # | 任务 | 验收点 |
|---|---|---|
| 4.1 | spec 收编二进制，重打包 | 产物内二进制可执行 |
| 4.2 | 打包 E2E（9 段真实录音）+ 三引擎切换矩阵 | 全过，GGUF RTF 复核 |
| 4.3 | 空 models_dir 首启下载验收 | GGUF 权重自动下载可用 |
| 4.4 | 文档：README（引擎一览/离线放置）、常用命令（如涉及） | 文档与代码一致 |

## 五、风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| CLI 输出格式无版本化保证，解析脆弱 | 🟡 | pin v0.1.4 + 解析规则单测固化 + 升级运行时版本时重跑全量验收 |
| ModelScope 无 GGUF 镜像，HF 源在国内不稳定 | 🟡 | 下载逻辑双源兜底（modelscope 优先，HF 回退）；离线手动放置文档化 |
| 预编译二进制指令集不兼容他机 | 🟢 | 目标机即本机（AVX2 可用）；通用 x64 版备用链接入 README |
| 子进程 stderr 噪声淹没日志 | 🟢 | stderr 仅在失败时截取尾部进异常；成功路径丢弃 |
| 临时 WAV 泄漏堆积 | 🟢 | 用完即删 + 启动清目录双重保险 |

## 六、决策点（2026-07-23 07:32 已拍板）

1. ✅ 引擎关系：GGUF 版**替代** PyTorch 版 fun-asr-nano（注册表仍三款，
   不出现两个同模型条目）。
2. ✅ 二进制分发：**vendor 进 git**（版本锁定 / 离线确定性强）。
3. ✅ 量化档位：**q8_0**（805MB，CER 8.30%，保真最高档位）。

### 档位验证（2026-07-23 07:36 实施前补测，q8_0 决策复核成立）

应谨慎要求，实施前以 `参考代码/示例语音和文字` 9 段真实样本 + 165.7s
拼接长音频对 q4_k_m / q8_0 双档对比：

| 场景 | q4_k_m | q8_0 | 结论 |
|---|---|---|---|
| 9 段短语音合计 55.2s（逐段含进程开销） | 6.51s（RTF 0.118） | 6.40s（RTF 0.116） | **手感无差别**（~0.5s/次固定开销掩盖解码差异） |
| 长音频 165.7s 稳态 | 10.89s（RTF 0.066） | 13.45s（RTF 0.081） | q8_0 慢 ~24%，仍比 PyTorch（RTF 0.273）快 3.4 倍 |
| 精度（对照参考文本） | 9 段输出**逐字一致** | 同左 | 档位不产生识别差异 |

**实施基线**：q8_0 目标手感 = 短语音 RTF≈0.12（含进程开销）、
长音频 RTF≈0.08；阶段 3.2/4.2 的 RTF 复核以此为准。


## 七、工作量预估

阶段 1（1h）+ 阶段 2（3h）+ 阶段 3（1h）+ 阶段 4（2h）≈ **1 天**。

---

*创建于 2026-07-23 07:26 (UTC+8)*
