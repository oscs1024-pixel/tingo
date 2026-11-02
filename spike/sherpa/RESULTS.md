# 可行性验证：做成 .dmg 用哪套识别引擎

目标：去掉 PyTorch（打包 1.5～2 GB、启动加载 10 秒以上、常驻 2.5 GB），识别效果尽量不降。
机器：M1 Pro 16 GB，纯 CPU，4 线程。脚本：`bench.py`、`to_sherpa.py`。

## 测试素材

- **测试会议**：52 秒两人对话（macOS 语音合成），有原文，算错字率。
- **热词句**：10 秒，含"青鸾计划、钟离岚、Kafka、Flink、鲲鹏"这类易错专名（虚构）。
- **3 分钟片段**：测速度、加载时间、内存。
- **真实会议 10 分钟**：一场真实会议的录音片段，没有原文，以现在的 FunASR 输出为参照算差异。

## 结果

| 引擎 | 测试会议错字率 | 热词句（不给热词 → 给热词） | 速度 | 加载 | 内存 | 真实会议与现在差异 |
|---|---|---|---|---|---|---|
| 现在：FunASR SeACo-Paraformer（PyTorch） | **4.6%** | 6.7% → 4.4% | 2.5x | 23.7s | 2.5 GB | — |
| FunASR 官方 ONNX：Paraformer-large int8（funasr-onnx 运行） | 7.1% | 6.7%（不支持） | 63x | 1.0s | 0.7 GB | **3.4%** |
| FunASR 官方 ONNX：Contextual Paraformer（带热词） | 8.1% | 8.9% → 4.4% | 37x | 1.8s | 1.3 GB | 3.5% |
| **同一个 Paraformer-large，用 sherpa-onnx 运行** | 6.6% | — | **71x** | **0.9s** | **0.9 GB** | 4.3% |
| sherpa Paraformer-zh int8 2025-10-07 | 8.6% | 24.4%（不支持） | 70x | 0.9s | 0.9 GB | 8.3% |
| sherpa SenseVoice int8 | 9.1% | 11.1%（不支持） | 60x | 0.5s | 1.0 GB | 7.9% |
| Fun-ASR-Nano int8（LLM 型） | 8.1% | 4.4% → **2.2%** | 11x | 2.9s | 2.0 GB | 5.5% |
| Qwen3-ASR 0.6B int8（LLM 型） | 23.4% | 8.9% → 4.4% | 10x | 1.5s | 2.1 GB | — |

准确率差距的来源：同为 PyTorch，SeACo-Paraformer 4.6%、普通 Paraformer-large 5.6%；后者的官方 int8 ONNX 7.1%。
模型不同和 int8 量化大约各占一半。测试会议只有约 250 字，1% ≈ 2～3 个字，真实会议的差异更有参考价值。

自己从 SeACo-Paraformer 导出 ONNX 失败：PyTorch 2.15 默认的新导出器不认 FunASR 的 dynamic_axes，
换旧导出器能导出但输出长度被固定成示例输入的长度，只能识别出一个字。

## 说话人聚类

用一场 76 分钟真实会议的 5314 个 CAM++ 声纹（与 sherpa 的 campplus 是同一个模型）对比：

- 现在的抽样谱聚类：11 人。
- sherpa `FastClustering`（层次聚类，按距离阈值）：0.4 秒；阈值 1.1 时 11 人、与谱聚类 95%～98% 对应，
  但对阈值很敏感（1.0 → 17 人，1.2 → 7 人），固定阈值换一场会未必合适。
- 结论：Rust 版移植现在的谱聚类（自动估计人数，约百行矩阵运算），不用固定阈值的层次聚类。

## 结论

**主引擎：FunASR 官方 Paraformer-large ONNX，经 sherpa-onnx 运行。**

- 和现在同一系列的模型，真实会议上与现在的输出只差约 4%；速度 70 倍实时（现在约 4 倍），
  加载 1 秒以内，内存不到 1 GB。
- sherpa-onnx 有 C / Rust 接口，VAD（silero）、标点（同一个 CT-Transformer，int8）、
  声纹（同一个 CAM++）都在里面，Rust 侧不用自己写特征提取和解码。
- 模型合计约 320 MB：识别 227 + 标点 61 + 声纹 27 + VAD 1，可首次启动时下载。

**热词**：这条路没有热词。Contextual Paraformer 在 sherpa 里不支持，FunASR 自己的 C++ 运行时支持但接入更重。
先把热词挪到纪要阶段：作为术语表交给大模型纠正同音字（成本几乎为零，效果待验证）；
需要更准时提供可选的"高精度模式"用 Fun-ASR-Nano（热词最准，但 11 倍实时、2 GB 内存）。

## 复现

```bash
# 模型放在 ~/.cache/jiyao-dev/sherpa 与 ~/.cache/jiyao-dev/funasr-onnx（下载命令见对话记录 / bench.py 里的路径）
uv run --no-project --with onnx --with pyyaml python spike/sherpa/to_sherpa.py <FunASR ONNX 目录> ~/.cache/jiyao-dev/sherpa/funasr-paraformer-large-int8
uv run --no-project --with sherpa-onnx --with funasr-onnx --with numpy python spike/sherpa/bench.py all <素材目录>
```
