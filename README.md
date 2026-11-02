# jiyao 纪要

**本地录音 → 中文转写 → 区分说话人 → 一键出会议纪要。** 录音和识别都在你自己的电脑上完成，纪要用你自己选的大模型生成（可以是本地 Ollama，全程不出本机）。

```bash
jiyao ui                      # 图形界面
jiyao record -t 周会          # 命令行录音，Ctrl+C 结束，自动出纪要
jiyao run 腾讯会议录制.mp4     # 处理已有的录音/录屏
```

> Local-first meeting minutes for Chinese: records system audio + mic on macOS, transcribes with FunASR (Paraformer + CAM++ speaker diarization), and writes structured minutes (decisions, action items with owners and due dates) with any OpenAI-compatible LLM.

## 为什么做这个

Meetily、Hyprnote 这类开源会议助手做得很好，但它们是为英文场景设计的（比如 Meetily 的识别核心是 Whisper / Parakeet）。中文会议有几个问题需要专门处理：

- **中文识别**：用阿里达摩院的 [FunASR](https://github.com/modelscope/FunASR) Paraformer，中文准确率和标点都比 Whisper 好，模型从 ModelScope 下载，国内直连。
- **谁说了什么**：CAM++ 说话人区分，并在换人处按字重新切分，修正 FunASR 把后一个人开头几个字算给前一个人的问题。
- **专有名词**：支持热词（人名、项目代号、内部系统名）。
- **纪要格式**：按国内职场习惯输出议题、决议、待办（事项 / 负责人 / 截止时间）、待定问题，每条附时间戳便于回听核对；"下周三"这类说法会按会议日期换算成具体日期。
- **发到群里**：一条命令推送到飞书、钉钉、企业微信群机器人。
- **线上会议**：macOS 上直接录系统声音，不用装 BlackHole 之类的虚拟声卡，腾讯会议、飞书、Zoom 里对方的声音都能录到。

## 输出示例

下面是一段约 1 分钟的模拟会议（用 macOS 语音合成生成的两人对话），用本地 Qwen3-8B 生成的纪要：

```markdown
# 产品发布及客服系统迁移会议

- 日期：2026-11-02（星期一）
- 时长：00:52
- 参会人：李经理、小张

## 一句话总结
讨论下个月产品发布时间及客服系统迁移方案。

## 决议
- 产品上线时间定为11月25日（[00:29]）

## 待办
| 事项 | 负责人 | 截止时间 | 出处 |
|---|---|---|---|
| 提交客服系统迁移方案 | 小张 | 下周三（11月11日） | [00:40] |
| 修复支付模块 | 小王 | 11月15日 | [00:29] |
```

同时会生成带说话人和时间戳的完整转写 `转写.md`：

```markdown
**李经理** `00:19`

那十五号提测的话，二十号上线来得及吗？

**小张** `00:23`

有点紧，我建议把上线推迟到二十五号，留出一周的测试。
```

## 安装

需要 Python 3.11+ 和 ffmpeg。

```bash
brew install ffmpeg            # Linux: apt install ffmpeg
uv tool install git+https://github.com/tancheng33/jiyao
# 或 pipx install git+https://github.com/tancheng33/jiyao

jiyao doctor                   # 检查环境
```

首次转写会从 ModelScope 下载识别模型（约 2.1 GB），之后离线可用。

## 配置大模型

转写不需要大模型，生成纪要需要。任何 OpenAI 兼容接口都可以：

```bash
jiyao config                   # 生成 ~/.config/jiyao/config.toml 模板
```

```toml
[llm]
base_url = "https://api.deepseek.com"
api_key = "sk-..."
model = "deepseek-chat"

hotwords = ["星河计划", "张晓东", "Kafka"]

[push]
webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

也可以用环境变量 `JIYAO_LLM_BASE_URL`、`JIYAO_LLM_API_KEY`、`JIYAO_LLM_MODEL`，或直接设置 `DEEPSEEK_API_KEY`。

**完全不出本机**：用 [Ollama](https://ollama.com) 跑本地模型，`base_url = "http://localhost:11434/v1"`、`model = "qwen3:8b"`，不需要 key。本地模型上下文较短，长会议会自动分段提炼再合并。

## 图形界面

```bash
jiyao ui            # 独立窗口（macOS）
jiyao ui --browser  # 在浏览器里打开
```

- **录音**：填个会议名称，点录音按钮。录音时能看到两路电平（系统声音 / 麦克风），背景的极光会跟着音量起伏；识别模型在录音期间就提前在后台加载，结束后马上开始转写。
- **导入**：把腾讯会议、飞书导出的录音或录屏直接拖进窗口。
- **会议详情**：纪要和转写两个视图，点任意时间戳跳到那里回听，播放时当前发言会高亮；可以给说话人改名后重新生成纪要，也可以直接编辑纪要。
- **设置**：大模型（DeepSeek / 通义千问 / 本地 Ollama 一键切换，带连接测试）、热词、群机器人。

界面只监听 127.0.0.1，不对外开放。

## 用法

```bash
# 录音（线上会议：系统声音 + 麦克风）
jiyao record -t 周会 -w "星河计划,张晓东" -s "张晓东,李雷"

# 线下会议只录麦克风 / 只旁听线上会议不录自己
jiyao record --no-system
jiyao record --no-mic

# 处理已有文件：wav、mp3、m4a、mp4……
jiyao run ~/Downloads/项目评审.mp4 -d 2026-11-02

# 转写后发现说话人顺序和名字，重新生成纪要（不用重新转写）
jiyao summarize ~/Documents/jiyao/2026-11-02_1430_周会 -s "王总,张晓东,李雷"

# 推送到群
jiyao push ~/Documents/jiyao/2026-11-02_1430_周会
jiyao run 录音.m4a --push
```

每场会议一个文件夹（默认在 `~/Documents/jiyao/`）：

```
2026-11-02_1430_周会/
├── 录音.m4a          # 16 kHz 单声道，1 小时约 30 MB
├── 转写.md
├── transcript.json   # 结构化转写，summarize 用
└── 纪要.md
```

`-s` 按**首次发言的先后顺序**给说话人命名，可以留空位：`-s ",李雷"` 只给第二位改名。

## 工作原理

```
录音 ─┬─ 系统声音（ScreenCaptureKit）─┐
      └─ 麦克风 ──────────────────────┴─ 合成 16 kHz 单声道
                                              │
            FunASR：VAD 切分 → Paraformer 识别（热词）→ 标点 → CAM++ 说话人聚类
                                              │
                     按字级时间戳在换人处重新切分句子
                                              │
                    大模型生成纪要（长会议分段提炼 + 合并）
                                              │
                            纪要.md ──→ 飞书 / 钉钉 / 企业微信
```

几个实现细节：

- **系统声音**：macOS 15+ 用 ScreenCaptureKit 同时录系统声音和麦克风。录音组件是一个 Swift 小程序，首次使用时自动编译（需要 Xcode Command Line Tools）。第一次运行要在「系统设置 → 隐私与安全性 → 屏幕与系统录音」里勾选你的终端。
- **外放回声**：开着扬声器开会时，麦克风会把对方的声音再录一遍，直接混音会叠成回声、明显拉低识别率。jiyao 在系统声音有人说话时关掉麦克风（并多保持 0.7 秒盖住回声尾巴），只在系统声音安静时用麦克风。代价是双方同时说话时本地一侧会被盖掉；**戴耳机效果最好**。
- **元信息不交给模型**：纪要里的日期、时长、参会人由程序填写，模型只负责内容，避免编造。
- **推理模型**：Qwen3、DeepSeek-R1 等输出的 `<think>` 会被过滤。

## 性能

M1 Pro 上纯 CPU 推理，10 分钟录音转写约 3 分钟（约 3.2 倍实时）：识别 79 秒、标点 49 秒、说话人区分 52 秒。录音越长单位时长越慢，1 小时的会议要 20 分钟以上；边录边转（录音时分段处理）在路线图上。转写时内存峰值约 2.5 GB。

## 已知限制

- 录系统声音目前只支持 macOS 15+；Linux 只能录麦克风（PulseAudio）；Windows 请先用会议软件录制，再 `jiyao run`。
- 说话人区分在多人抢话、声音相近时会出错，可以用 `summarize -s` 修正名字。
- 外放时的回声处理是"让路"而不是真正的回声消除，换人处偶尔有一两个字的误识别。

## 路线图

- [ ] 边录边转：录音时分段识别，结束后几秒出转写
- [ ] 真正的回声消除（AEC），外放也能达到耳机的效果
- [ ] 菜单栏 App：一键开始/结束、会议软件启动时提醒录音
- [ ] 导出到飞书文档 / 钉钉文档
- [ ] Windows 系统声音录制（WASAPI loopback）

## 开发

```bash
git clone https://github.com/tancheng33/jiyao && cd jiyao
uv sync
uv run pytest -m "not slow"   # 不下载模型的单元测试
uv run pytest                 # 含端到端测试（首次会下载模型）
```

## 致谢

- [FunASR](https://github.com/modelscope/FunASR)：语音识别、VAD、标点、说话人模型
- [Meetily](https://github.com/Zackriya-Solutions/meetily)、[Hyprnote](https://github.com/fastrepl/anarlog)：本地会议助手的思路

## 许可

MIT
