<p align="center"><img src="brand/tingo-icon.svg" width="120" alt="Tingo"></p>

<h1 align="center">Tingo</h1>

<p align="center"><b>Every meeting, heard.</b><br>Local-first meeting minutes for Chinese — and every conclusion links back to what was actually said.</p>

<p align="center">
  <a href="https://github.com/tancheng33/tingo/releases/latest"><img src="https://img.shields.io/badge/download-macOS%20app-1f4bff?logo=apple" alt="Download"></a>
  <img src="https://img.shields.io/badge/macOS-13%2B-000?logo=apple" alt="macOS 13+">
  <img src="https://img.shields.io/badge/license-MIT-34c6f4" alt="MIT">
  <a href="README.md"><img src="https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-555" alt="中文"></a>
</p>

<p align="center"><img src="docs/screenshots/detail.png" width="880" alt="Meeting minutes"></p>

*Tingo* comes from 听 (*tīng*, "to listen"); its Chinese name 听过 means "heard it".

Tingo records Tencent Meeting / Feishu / Zoom audio plus your mic, transcribes it **on your machine**, tells speakers apart, and has the LLM of your choice write structured minutes: topics, decisions, action items with owners and due dates, risks — and, when the meeting discussed *why* something happened, a **fishbone diagram whose every branch is bound to the original quotes**.

- **Audio never leaves your Mac.** Speech recognition and speaker diarization run locally. Minutes can be written by DeepSeek, Qwen or any OpenAI-compatible API — or a local Ollama model, fully offline.
- **Built for Chinese.** Uses Alibaba DAMO's FunASR models, which handle Chinese punctuation, numbers and names far better than English-first stacks. Relative dates ("next Wednesday", "end of month") are resolved against the meeting date.
- **Every claim has a source.** Each line of the minutes carries a timestamp that jumps to that second of the recording. The fishbone diagram counts how much original speech backs each branch and flags the ones with **no evidence** — so hand-waving is visible at a glance.
- **12 MB desktop app.** Tauri + Rust, no Python or ffmpeg needed. A live recording is fully transcribed seconds after you hit stop; a 76-minute file imports in about 3.5 minutes on an M1 Pro.

> The meetings, names and numbers in the screenshots are fictional demo data.

## Highlights

**Evidence-bound fishbone diagrams.** When a meeting analyzes causes, Tingo draws a fishbone. Expand a branch to see who said what and when; click a timestamp to listen. Branches whose cited timestamps can't be found in the transcript are marked red ("no evidence"), thin ones amber. Export to [Excalidraw](https://excalidraw.com) with one click.

<p align="center"><img src="docs/screenshots/fishbone.png" width="820" alt="Fishbone diagram bound to quotes"></p>

**Never wait.** Stop recording and start the next meeting right away — transcription and minutes run in the background, with per-task progress in the sidebar. Open a meeting to watch the minutes being written section by section (including "thinking" progress for reasoning models).

<p align="center"><img src="docs/screenshots/home.png" width="49%" alt="Background tasks"> <img src="docs/screenshots/generating.png" width="49%" alt="Minutes being written"></p>

**Export & share.** A4 PDF (fishbone quotes expanded on paper), Obsidian notes with properties and an embedded Excalidraw drawing (optionally automatic), Feishu / DingTalk / WeCom group bots, or plain Markdown.

<p align="center"><img src="docs/screenshots/pdf.png" width="820" alt="PDF export"></p>

**Swappable ASR models.** Paraformer-large (default, fast and accurate for Chinese), SenseVoice (mixed Chinese / English / Japanese / Korean / Cantonese) or Fun-ASR-Nano (most accurate, applies your hotwords during recognition) — downloaded and switched in Settings, all running locally.

## Install

**Desktop app:** download `Tingo_x.y.z_aarch64.dmg` from [Releases](https://github.com/tancheng33/tingo/releases/latest) (Apple Silicon, macOS 13+; recording system audio needs macOS 15+). On first launch it downloads ~330 MB of models, then works offline.

The app is not notarized yet. On first launch, right-click Tingo in Applications → **Open**, or run:

```bash
xattr -dr com.apple.quarantine /Applications/Tingo.app
```

**Command line** (Python + FunASR; needs Python 3.11+ and ffmpeg):

```bash
uv tool install git+https://github.com/tancheng33/tingo
tingo record -t standup        # record system audio + mic, Ctrl+C to stop, minutes follow
tingo run meeting.mp4          # process an existing recording
tingo ui                       # open the GUI
```

## Privacy

Audio and transcripts stay on your machine. When generating minutes, the **transcript text** is sent to the LLM endpoint you configure — use a local Ollama model to keep that offline too. The built-in server listens on 127.0.0.1 only. No analytics, no telemetry.

## How it works

ScreenCaptureKit records system audio and mic → audio is chunked at pauses while recording → VAD → ASR (Paraformer / SenseVoice / Fun-ASR-Nano via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)) → punctuation → CAM++ speaker embeddings → spectral clustering → LLM minutes (map-reduce for long meetings, dates resolved, glossary to fix homophones) → every fishbone citation is checked against the transcript. Engine comparison notes: [`spike/sherpa/RESULTS.md`](spike/sherpa/RESULTS.md) (in Chinese).

## Development

```bash
git clone https://github.com/tancheng33/tingo && cd tingo
uv sync && uv run pytest -m "not slow"     # Python backend / CLI
cd app && pnpm install && pnpm tauri dev   # desktop app (Tauri + Rust)
cd engine && cargo test                    # Rust core
```

Issues and PRs are welcome. Licensed under [MIT](LICENSE).
