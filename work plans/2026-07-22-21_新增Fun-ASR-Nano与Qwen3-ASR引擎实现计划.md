> **状态**：草稿
> **范围**：`Zen_VocoType_Service`（引擎注册表、加载器、推理 Worker）、`Zen_VocoType_Protocol`（视需要）
> **时间**：2026-07-22 21:00（设计，UTC+8）
> **优先级**：高

# 新增 Fun-ASR-Nano 与 Qwen3-ASR-1.7B 引擎实现计划

## 一、背景与目标

依据章程文档 [`work charter/语音识别引擎模型升级推荐_v1.0.md`](../work%20charter/语音识别引擎模型升级推荐_v1.0.md)，从 2025–2026 年新开源模型中选定两款，以 **增量方式** 加入现有引擎阵容：

| 注册名 | 模型 | 来源 | 定位 |
|---|---|---|---|
| `fun-asr-nano` | Fun-ASR-Nano-2512（0.8B） | 阿里通义实验室，2025-12 | 通用离线识别的新一代选项 |
| `qwen3-asr-1.7b` | Qwen3-ASR-1.7B | 阿里云 Qwen 团队，2026-01 | 高精度 / 热词 / 多方言场景选项 |

**增量原则的明确约束**：

- 🟢 现有三款引擎（`paraformer-large`、`sensevoice-small`、`seaco-paraformer`）**全部保留**，注册表只增不删。
- 🟢 `default_model` 保持 `paraformer-large` 不变，新引擎由用户通过托盘菜单 / `model_switch` 协议主动切换。
- 🟢 现有加载/切换/自检管线行为不变，新引擎接入后旧引擎回归测试必须全部通过。

## 二、现状架构结论（实施前提）

经代码调查确认：

- 引擎管理为「**单一 FunASR AutoModel 后端 + 注册表驱动**」，无 Provider 抽象层。注册表唯一出处为 [`Zen_VocoType_Service/src/zen_vocotype_service/config.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/config.py) 的 `DEFAULT_MODEL_REGISTRY`，查询层为 [`registry.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/models/registry.py)。
- 加载参数集中构造于 [`loader.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/models/loader.py) 的 `_build_automl_params`，固定 `device="cpu"`；推理调用集中在 [`worker.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/inference/worker.py) 的 `_do_recognize`，直接调用 `model.generate(...)` 并假定返回 `result[0]["text"]` 结构。
- 托盘切换菜单、模型清单、`model_info` / `model_switch` 协议均由注册表**动态生成**，新增引擎无需改动 UI 与协议层。
- 模型缓存统一由 `MODELSCOPE_CACHE` 环境变量指向 `settings.models_dir`，且必须在 funasr/modelscope 导入前硬设置（现有顺序敏感约束）。

**接入难度判定**：

| 引擎 | 判定 | 原因 |
|---|---|---|
| `fun-asr-nano` | 🟢 低 | FunASR 生态，走现有统一管线，注册表加一条即可 |
| `qwen3-asr-1.7b` | 🟡 中 | 非 FunASR 生态，官方加载方式为 `qwen-asr` 包（`Qwen3ASRModel.from_pretrained`，transformers / vLLM 双后端），需先补「引擎类型」分支 |

## 三、阶段划分与任务清单

### 阶段 1：`fun-asr-nano` 注册接入（预计 0.5 天）

| # | 任务 | 涉及文件 | 验收点 |
|---|---|---|---|
| 1.1 | 调研确认 ModelScope 模型 ID（预期 `FunAudioLLM/Fun-ASR-Nano-2512` 或 `iic/...`，以官方仓库为准），确认是否自带 VAD/标点、是否需要额外 AutoModel 参数 | —（调研） | 确认可用 `model_id` 与必需参数清单 |
| 1.2 | 在 `DEFAULT_MODEL_REGISTRY` 新增 `fun-asr-nano` 条目（`model_id` + `description`，VAD/标点视 1.1 结论） | [`config.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/config.py) | 注册表含 4 款引擎 |
| 1.3 | 手动验证：启动服务端 → 托盘切换到 `fun-asr-nano` → 识别测试音频 | — | 切换成功，识别结果正常，`model_info` 返回 4 款 |
| 1.4 | 更新受影响的测试（硬编码三模型名的断言等） | [`tests/`](../../Zen_VocoType_Service/tests/) | 测试全绿 |

⚠️ 若 1.1 发现 Fun-ASR-Nano 的 AutoModel 参数或 `generate` 返回结构与现有三模型不兼容，则该引擎也纳入阶段 2 的分支范围，不再享受「一行注册」待遇。

### 阶段 2：引擎类型分支（Provider 轻量化）（预计 1 天）

引入**最小化**的引擎类型区分，不做完整插件框架：

| # | 任务 | 涉及文件 | 验收点 |
|---|---|---|---|
| 2.1 | `ModelEntry` 新增 `engine_type` 字段，取值 `"funasr"` / `"qwen3-asr"`，默认 `"funasr"`（现有条目与用户旧配置零感知） | [`config.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/config.py) | 旧配置不加该字段可正常加载 |
| 2.2 | `loader.load_model` 按 `engine_type` 分支：funasr 走现有路径；qwen3-asr 走新增 `_load_qwen3_asr`（延迟导入 `qwen_asr`，同 funasr 的延迟导入策略） | [`loader.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/models/loader.py) | funasr 路径行为不变 |
| 2.3 | `worker._do_recognize` 按 `engine_type` 分支推理，返回结构统一归一化为 `{"text", "confidence", "duration_ms"}`（confidence 不可得时为 `None`，禁止编造，沿用现有约定） | [`worker.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/inference/worker.py) | 两后端返回结构一致 |
| 2.4 | 自检 `selftest` 适配 qwen3-asr 推理签名 | [`loader.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/models/loader.py) | 两后端自检均通过 |
| 2.5 | 单元测试：分支路由、旧引擎回归 | [`tests/`](../../Zen_VocoType_Service/tests/) | 测试全绿 |

设计红线：

- ❌ 不引入抽象基类 / 插件注册框架 —— 两个 `if` 分支足够，与现有「单一后端 + 注册表」的克制风格一致。
- ❌ 不改 `ModelManager.switch` 的原子切换语义（先备后切、失败回滚），新旧引擎切换走同一条管线。
- ❌ 不改协议层与客户端。

### 阶段 3：`qwen3-asr-1.7b` 接入（预计 1.5 天）

| # | 任务 | 涉及文件 | 验收点 |
|---|---|---|---|
| 3.1 | 新增依赖 `qwen-asr`（transformers 后端，❌ 不引入 vLLM —— 本项目为 CPU 桌面端场景），更新 [`pyproject.toml`](../../Zen_VocoType_Service/pyproject.toml) 与 `uv.lock` | `pyproject.toml` / `uv.lock` | `.venv` 安装成功 |
| 3.2 | 实现 `_load_qwen3_asr`：`Qwen3ASRModel.from_pretrained(...)`，CPU + 默认 dtype；模型路径解析兼容 `MODELSCOPE_CACHE` 布局（`Qwen/Qwen3-ASR-1.7B`）并支持 `local_path` 直载 | [`loader.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/models/loader.py) | 模型加载成功 |
| 3.3 | 音频输入适配：现有 PCM float32 数组 → `qwen-asr` 接受的 `(np.ndarray, sr)` 输入形式 | [`worker.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/inference/worker.py) | 推理返回文本 |
| 3.4 | 注册表新增 `qwen3-asr-1.7b` 条目（`engine_type: qwen3-asr`） | [`config.py`](../../Zen_VocoType_Service/src/zen_vocotype_service/config.py) | 注册表含 5 款引擎 |
| 3.5 | 手动验证：切换 → 识别 → 切回旧引擎，全链路回归 | — | 五款引擎互切无异常 |

⚠️ **CPU 性能风险（重点验证项）**：Qwen3-ASR-1.7B 为 1.7B 参数 LLM 架构模型，官方演示均为 GPU。阶段 3.5 必须实测 CPU 实时率（RTF）；若 RTF 高到不可用（例如 > 2），则保留接入代码但在 `description` 中标注「CPU 下仅适合短音频 / 实验性」，是否默认展示给用户另行决策。后续可考虑追加 `device` 配置项开放 GPU，但**不在本计划范围内**。

### 阶段 4：打包与端到端验收（预计 0.5 天）

| # | 任务 | 验收点 |
|---|---|---|
| 4.1 | PyInstaller 打包验证：`qwen-asr` / `transformers` 的 hidden imports 与资源收集，打包体积评估 | 打包产物可启动，体积增量记录在案 |
| 4.2 | 空模型目录首启下载验收（沿用 `tools/e2e_packaged.py` 思路，覆盖两款新引擎） | 新引擎可自动下载并完成自检 |
| 4.3 | 五引擎 × 切换 × 识别 的端到端矩阵回归 | 全部通过 |
| 4.4 | 文档更新：README 引擎清单、[`常用命令.md`](../../常用命令.md) 如有相关命令 | 文档与代码一致 |

## 四、检查点与依赖关系

```
阶段 1（fun-asr-nano）──┐
                        ├──> 阶段 4（打包与验收）
阶段 2（引擎分支）──> 阶段 3（qwen3-asr）──┘
```

- 阶段 1 与阶段 2 **可并行**（fun-asr-nano 不依赖分支改造）。
- 阶段 3 依赖阶段 2 完成。
- 检查点：每阶段结束跑全量测试 + 手动切换验证，任何旧引擎回归失败 🔴 阻断进入下一阶段。

## 五、风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| Fun-ASR-Nano 的 modelscope ID / AutoModel 参数与预期不符 | 🟡 | 阶段 1.1 先调研后动手；不兼容则并入阶段 2 分支处理 |
| Qwen3-ASR-1.7B CPU 推理过慢 | 🟡 | 阶段 3.5 实测 RTF 硬性验收；不达标则标注实验性，不阻塞合入 |
| `qwen-asr` 依赖链（transformers 等）膨胀打包体积 | 🟡 | 阶段 4.1 体积评估；必要时将 qwen3-asr 依赖设为可选 extras |
| 依赖版本冲突（funasr 与 qwen-asr 共用 torch/transformers） | 🟡 | 阶段 3.1 安装后立即跑全量测试 |
| 新模型改变 `MODELSCOPE_CACHE` 目录布局假设 | 🟢 | `local_path` 兜底，托盘「设置模型目录…」机制不变 |

## 六、工作量汇总

| 阶段 | 内容 | 预计 |
|---|---|---|
| 1 | fun-asr-nano 注册接入 | 0.5 天 |
| 2 | 引擎类型分支 | 1 天 |
| 3 | qwen3-asr-1.7b 接入 | 1.5 天 |
| 4 | 打包与端到端验收 | 0.5 天 |
| **合计** | | **3.5 天** |

---

*创建于 2026-07-22 21:00 (UTC+8)*
