# vendored 运行时：llama-funasr-cli

- **来源**: FunAudioLLM/Fun-ASR GitHub Release `runtime-llamacpp-v0.1.4`
  （`funasr-llamacpp-linux-x64-avx2.tar.gz` 中的 `llama-funasr-cli`）
- **版本**: v0.1.4（2026-06-29，FunASR llama.cpp 运行时）
- **SHA256**: `9fe9610588e5d0554bec2422fb917c4d7a81269b0fffb079757cde45a1e16c52`
- **用途**: funasr-gguf 引擎的推理子进程（GGUF/llama.cpp CPU 运行时，
  见 `work plans/2026-0723-0726_fun-asr-nano提速修复（GGUF集成）实现计划.md`）
- **许可**: `LICENSE.llama_cpp`（MIT）+ `LICENSE.FunASR`（Apache-2.0）
- **指令集**: linux-x64-avx2 构建（目标机 Zen 5 可用；通用 x64 保守版见
  同 Release `funasr-llamacpp-linux-x64.tar.gz`，他机分发时替换）
- **升级纪律**: 更换二进制必须重跑服务端全量测试 + 打包 E2E
  （CLI 输出格式无版本化保证，解析规则见 `models/loader.py`）
