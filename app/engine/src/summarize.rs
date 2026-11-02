//! 用任一 OpenAI 兼容接口把转写整理成中文会议纪要。Python 版 summarize.py 的移植，
//! 另加一条：热词作为术语表交给模型纠正同音字（这个识别模型不支持热词）。

use chrono::{Datelike, Duration as Days, NaiveDate};
use regex::{Captures, Regex};
use std::io::{BufRead, BufReader};
use std::sync::LazyLock;
use std::time::Duration;

use crate::config::LlmConfig;
use crate::transcript::{fmt_ts, Transcript};

const SYSTEM: &str =
    "你是一位严谨的会议秘书，擅长把口语化、带识别错误的会议转写整理成中国职场常用的会议纪要。";

const MINUTES_PROMPT: &str = "下面是一场会议的语音转写，格式为「[时间戳] 说话人：内容」。会议日期：{meeting_date}。

请整理成会议纪要，严格使用以下 Markdown 结构，不要输出其他内容：

# <根据内容拟一个会议标题>

## 一句话总结
<一句话>

## 议题与讨论
### 1. <议题>
- <要点>（[mm:ss]）

## 决议
- <已经拍板的事项>（[mm:ss]）

## 待办
| 事项 | 负责人 | 截止时间 | 出处 |
|---|---|---|---|
| <具体动作> | <人名或说话人> | <时间> | [mm:ss] |

## 待定问题与风险
- <没有结论的问题、提到的风险>

## 原因分析
```fishbone
{\"problem\": \"<被分析的问题，用问句>\", \"branches\": [{\"title\": \"<一个原因，10 字以内>\", \"detail\": \"<一句话补充>\", \"refs\": [\"mm:ss\", \"mm:ss\"]}]}
```

要求：
1. 只依据转写内容，不编造。负责人或截止时间没有明确说出的，写\"未明确\"。
2. 转写有同音错字，结合上下文改正明显的错误；拿不准的保留原文。
3. 转写里的\"下周五\"\"明天\"等已由程序标好具体日期，如\"下周五（11月13日）\"，照抄括号里的日期，不要自己推算。其他相对时间（如\"十五号\"）保留原话，并查上面的日期对照表在括号里写出具体日期。
4. 每条要点、决议、待办都附上出处时间戳，便于回听核对。
5. 某一节没有内容时写\"无\"，不要删掉这一节（「原因分析」除外，见第 7 条）。
6. 去掉寒暄和口头禅，语言简洁、书面化。
7. 只有会议里明确分析了某个问题的原因时，才写「原因分析」这一节：每个问题一个 fishbone 代码块（最多两个），每块 2～6 个分支，内容是合法 JSON。refs 填支撑这个原因的原话的时间戳，必须是上面转写里出现过的 [mm:ss]（不带方括号）；找不到原话支撑就留空数组，不要编。没有分析原因的会议，整节省略，不要写\"无\"。{glossary}

转写：
{transcript}
";

const CHUNK_PROMPT: &str = "下面是一场长会议中的一段转写（第 {index}/{total} 段），格式为「[时间戳] 说话人：内容」。会议日期：{meeting_date}。

请提炼这一段的：讨论要点、已拍板的决议、待办（事项/负责人/截止时间）、待定问题与风险。
每条都附出处时间戳 [mm:ss]，只依据原文，不编造，用简洁的条目输出。{glossary}

转写：
{transcript}
";

const REDUCE_PROMPT: &str = "下面是一场长会议按时间顺序分段提炼的笔记。请把它们合并成一份完整的会议纪要，去重、按议题归并，保留出处时间戳。

{instructions}

分段笔记：
{notes}
";

const WEEKDAYS: [&str; 7] = ["一", "二", "三", "四", "五", "六", "日"];
const LOCAL_CHUNK_CHARS: usize = 6000;
const CHARS_PER_TOKEN: f64 = 1.3;
const RESERVED_TOKENS: usize = 3500;

fn md(d: NaiveDate) -> String {
    format!("{}月{}日", d.month(), d.day())
}

fn weekday(d: NaiveDate) -> &'static str {
    WEEKDAYS[d.weekday().num_days_from_monday() as usize]
}

/// 日期对照表。小模型算"下周五是几号"经常算错，直接给表让它查。
pub fn calendar_hint(d: NaiveDate) -> String {
    let monday = d - Days::days(d.weekday().num_days_from_monday() as i64);
    let mut lines = vec![
        "日期对照表（每周从周一开始）：".to_string(),
        format!(
            "- 今天 {}（星期{}），明天 {}，后天 {}",
            md(d),
            weekday(d),
            md(d + Days::days(1)),
            md(d + Days::days(2))
        ),
    ];
    for (name, offset) in [("本周", 0), ("下周", 7), ("下下周", 14)] {
        let days: Vec<String> = (0..7)
            .map(|i| {
                format!(
                    "周{} {}",
                    WEEKDAYS[i as usize],
                    md(monday + Days::days(offset + i))
                )
            })
            .collect();
        lines.push(format!("- {name}：{}", days.join("、")));
    }
    lines.join("\n")
}

// Rust 的 regex 不支持后行断言：把紧跟的括号一起匹配进来，有括号说明已经标过，原样返回
static WEEK_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(下下|下|本|这)个?(?:周|星期|礼拜)的?(?:周|星期|礼拜)?([一二三四五六日天])([（(])?",
    )
    .unwrap()
});
static DAY_RE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(大后天|后天|明天)([（(])?").unwrap());

/// 在"下周五""下下周的周一""明天"后面标上具体日期，换算由程序完成，不靠模型。
pub fn annotate_dates(text: &str, d: NaiveDate) -> String {
    let monday = d - Days::days(d.weekday().num_days_from_monday() as i64);
    let text = WEEK_RE.replace_all(text, |c: &Captures| {
        if c.get(3).is_some() {
            return c[0].to_string();
        }
        let week = match &c[1] {
            "下下" => 14,
            "下" => 7,
            _ => 0,
        };
        let day = "一二三四五六日天"
            .chars()
            .position(|x| c[2].starts_with(x))
            .unwrap()
            .min(6) as i64;
        format!("{}（{}）", &c[0], md(monday + Days::days(week + day)))
    });
    DAY_RE
        .replace_all(&text, |c: &Captures| {
            if c.get(2).is_some() {
                return c[0].to_string();
            }
            let n = match &c[1] {
                "明天" => 1,
                "后天" => 2,
                _ => 3,
            };
            format!("{}（{}）", &c[0], md(d + Days::days(n)))
        })
        .into_owned()
}

fn glossary(hotwords: &[String]) -> String {
    let words: Vec<&str> = hotwords
        .iter()
        .map(|w| w.trim())
        .filter(|w| !w.is_empty())
        .collect();
    if words.is_empty() {
        return String::new();
    }
    format!(
        "\n8. 本场会议涉及的专有名词（人名、项目、系统名）：{}。转写里与它们同音或近音的写法，一律改成这些词。",
        words.join("、")
    )
}

/// 是不是本机 Ollama 上的这个模型：以 /api/show 里真实的模型文件格式为准
/// （本机的 OpenAI 兼容中转可能连 /api/show 都仿了，但给不出 gguf）。
pub fn is_ollama(cfg: &LlmConfig) -> bool {
    ollama_show(cfg).is_some()
}

fn ollama_show(cfg: &LlmConfig) -> Option<serde_json::Value> {
    let host = reqwest::Url::parse(&cfg.base_url)
        .ok()?
        .host_str()?
        .to_string();
    if !["localhost", "127.0.0.1", "::1", "[::1]"].contains(&host.as_str()) {
        return None;
    }
    let root = cfg
        .base_url
        .trim_end_matches('/')
        .trim_end_matches("/v1")
        .to_string();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .ok()?;
    let r = client
        .post(format!("{root}/api/show"))
        .json(&serde_json::json!({ "model": cfg.model }))
        .send()
        .ok()?;
    let data: serde_json::Value = r.json().ok()?;
    let fmt = data["details"]["format"].as_str()?;
    ["gguf", "safetensors"].contains(&fmt).then_some(data)
}

/// 一次交给模型的转写最多多少字，超过就分段提炼再合并。
pub fn default_max_chars(cfg: &LlmConfig) -> usize {
    let Some(show) = ollama_show(cfg) else {
        return 40000;
    };
    static NUM_CTX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"num_ctx\s+(\d+)").unwrap());
    let ctx = show["parameters"]
        .as_str()
        .and_then(|p| NUM_CTX.captures(p))
        .and_then(|c| c[1].parse::<usize>().ok())
        .unwrap_or(4096);
    // 上下文够大也不超过 LOCAL_CHUNK_CHARS：实测 8B 模型一次读 1.3 万字，纪要里的待办从 14 条掉到 6 条
    let fits = (ctx.saturating_sub(RESERVED_TOKENS) as f64 * CHARS_PER_TOKEN) as usize;
    fits.clamp(4000, LOCAL_CHUNK_CHARS)
}

/// 去掉推理模型输出里的 <think>…</think>。partial 用于流式：未闭合的 <think> 和末尾半截标签先不显示。
pub fn strip_think(text: &str, partial: bool) -> String {
    static THINK: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?s)<think>.*?</think>\s*").unwrap());
    let mut t = THINK.replace_all(text, "").into_owned();
    if partial {
        if let Some(i) = t.find("<think>") {
            t.truncate(i);
        }
        for n in (1.."<think>".len()).rev() {
            if t.ends_with(&"<think>"[..n]) {
                t.truncate(t.len() - n);
                break;
            }
        }
    }
    t
}

/// 流式调用 chat/completions，返回去掉思考过程的完整回答。
/// `on_think(已思考字数)`：推理模型先长时间思考，不报出来界面就像卡住了。
/// 思考内容有的放在 reasoning_content / reasoning 字段，有的夹在正文的 <think> 里，两种都算。
pub fn chat(
    cfg: &LlmConfig,
    prompt: &str,
    think: bool,
    mut on_token: impl FnMut(&str),
    mut on_think: impl FnMut(usize),
) -> Result<String, String> {
    let local = is_ollama(cfg);
    let key = if cfg.api_key.is_empty() && local {
        "ollama".to_string()
    } else {
        cfg.api_key.clone()
    };
    if key.is_empty() {
        return Err("没有配置大模型的 API Key：在设置里填写".into());
    }
    let mut body = serde_json::json!({
        "model": cfg.model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": true,
    });
    if !think && local {
        // Ollama 的兼容接口用 reasoning_effort=none 关掉 Qwen3 等模型的思考；远程接口未必认这个值，只对本地生效
        body["reasoning_effort"] = "none".into();
    }
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(1800))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(format!(
            "{}/chat/completions",
            cfg.base_url.trim_end_matches('/')
        ))
        .bearer_auth(key)
        .json(&body)
        .send()
        .map_err(|e| format!("连不上大模型（{}）：{e}", cfg.base_url))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().unwrap_or_default();
        return Err(format!(
            "大模型返回 {status}：{}",
            text.chars().take(300).collect::<String>()
        ));
    }
    let mut raw = String::new();
    let mut shown = 0;
    let mut thought = 0usize;
    for line in BufReader::new(resp).lines() {
        let line = line.map_err(|e| format!("读取大模型输出中断：{e}"))?;
        let Some(data) = line.strip_prefix("data:").map(str::trim) else {
            continue;
        };
        if data == "[DONE]" {
            break;
        }
        let Ok(v) = serde_json::from_str::<serde_json::Value>(data) else {
            continue;
        };
        let d = &v["choices"][0]["delta"];
        if let Some(r) = d["reasoning_content"].as_str().or_else(|| d["reasoning"].as_str()) {
            if !r.is_empty() {
                thought += r.chars().count();
                on_think(thought);
            }
        }
        let Some(delta) = d["content"].as_str() else {
            continue;
        };
        if delta.is_empty() {
            continue;
        }
        raw.push_str(delta);
        let visible = strip_think(&raw, true);
        if visible.len() > shown {
            on_token(&visible[shown..]);
            shown = visible.len();
        } else if shown == 0 && raw.trim_start().starts_with("<think>") {
            on_think(thought + raw.chars().count());
        }
    }
    Ok(strip_think(&raw, false).trim().to_string())
}

pub fn split_chunks(lines: &[&str], max_chars: usize) -> Vec<String> {
    let mut chunks = Vec::new();
    let mut buf: Vec<&str> = Vec::new();
    let mut size = 0;
    for line in lines {
        let n = line.chars().count();
        if !buf.is_empty() && size + n > max_chars {
            chunks.push(buf.join("\n"));
            buf.clear();
            size = 0;
        }
        buf.push(line);
        size += n + 1;
    }
    if !buf.is_empty() {
        chunks.push(buf.join("\n"));
    }
    chunks
}

pub struct Hooks<'a> {
    pub on_token: &'a mut dyn FnMut(&str),
    pub on_stage: &'a mut dyn FnMut(&str),
    /// 推理模型思考了多少字（还没开始写正文时用来显示进展）
    pub on_think: &'a mut dyn FnMut(usize),
}

pub fn summarize(
    tr: &Transcript,
    cfg: &LlmConfig,
    meeting_date: NaiveDate,
    hotwords: &[String],
    hooks: Hooks,
) -> Result<String, String> {
    let day = format!(
        "{}（星期{}）",
        meeting_date.format("%Y-%m-%d"),
        weekday(meeting_date)
    );
    let dated = format!("{day}\n\n{}", calendar_hint(meeting_date));
    let gl = glossary(hotwords);
    let max_chars = default_max_chars(cfg);
    let text = annotate_dates(&tr.to_prompt_text(), meeting_date);
    let minutes_prompt = MINUTES_PROMPT
        .replace("{meeting_date}", &dated)
        .replace("{glossary}", &gl);

    let body = if text.chars().count() <= max_chars {
        chat(
            cfg,
            &minutes_prompt.replace("{transcript}", &text),
            true,
            hooks.on_token,
            hooks.on_think,
        )?
    } else {
        let lines: Vec<&str> = text.lines().collect();
        let chunks = split_chunks(&lines, max_chars);
        let mut notes = Vec::new();
        for (i, chunk) in chunks.iter().enumerate() {
            (hooks.on_stage)(&format!("分段提炼 {}/{}", i + 1, chunks.len()));
            let p = CHUNK_PROMPT
                .replace("{index}", &(i + 1).to_string())
                .replace("{total}", &chunks.len().to_string())
                .replace("{meeting_date}", &dated)
                .replace("{glossary}", &gl)
                .replace("{transcript}", chunk);
            // 分段提炼主要是摘条目，不需要思考；最后合并时再思考
            notes.push(format!(
                "### 第 {} 段\n{}",
                i + 1,
                chat(cfg, &p, false, |_| {}, |_| {})?
            ));
        }
        (hooks.on_stage)("合并成纪要");
        let instructions = minutes_prompt
            .split("转写：")
            .next()
            .unwrap_or("")
            .to_string();
        let p = REDUCE_PROMPT
            .replace("{instructions}", &instructions)
            .replace("{notes}", &notes.join("\n\n"));
        chat(cfg, &p, true, hooks.on_token, hooks.on_think)?
    };
    Ok(with_meta(&body, tr, &day))
}

/// 会议时间、时长、参会人由程序填，不让模型猜。
fn with_meta(body: &str, tr: &Transcript, day: &str) -> String {
    let body = strip_fence(body);
    let names: Vec<String> = tr
        .speakers_in_order()
        .into_iter()
        .map(|s| tr.name_of(s))
        .collect();
    let names = if names.is_empty() {
        "无".to_string()
    } else {
        names.join("、")
    };
    let meta = format!(
        "- 日期：{day}\n- 时长：{}\n- 参会人：{names}",
        fmt_ts(tr.duration_ms)
    );
    let (first, rest) = body.split_once('\n').unwrap_or((&body, ""));
    let (title, rest) = if first.starts_with("# ") {
        (first, rest)
    } else {
        ("# 会议纪要", body.as_str())
    };
    format!("{title}\n\n{meta}\n\n{}\n", rest.trim())
}

fn strip_fence(text: &str) -> String {
    let mut t = text.trim().to_string();
    if t.starts_with("```") {
        t = t
            .split_once('\n')
            .map(|(_, r)| r.to_string())
            .unwrap_or_default();
        if t.trim_end().ends_with("```") {
            t = t.trim_end().trim_end_matches("```").to_string();
        }
    }
    t.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transcript::Sentence;

    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).unwrap()
    }

    #[test]
    fn annotate_dates_like_python() {
        let x = d(2026, 11, 2); // 星期一
        assert_eq!(annotate_dates("下周五完成", x), "下周五（11月13日）完成");
        assert_eq!(
            annotate_dates("下下周的周一合并", x),
            "下下周的周一（11月16日）合并"
        );
        assert_eq!(
            annotate_dates("这周三、下个星期二、下礼拜天", x),
            "这周三（11月4日）、下个星期二（11月10日）、下礼拜天（11月15日）"
        );
        assert_eq!(
            annotate_dates("明天和后天，大后天", x),
            "明天（11月3日）和后天（11月4日），大后天（11月5日）"
        );
        assert_eq!(
            annotate_dates("周五再说，今天天气好", x),
            "周五再说，今天天气好"
        );
        assert_eq!(
            annotate_dates("下周五（11月13日）", x),
            "下周五（11月13日）"
        );
        assert_eq!(
            annotate_dates("下下周一", d(2026, 11, 29)),
            "下下周一（12月7日）"
        );
    }

    #[test]
    fn calendar_and_strip() {
        assert!(calendar_hint(d(2026, 11, 2)).contains("下周：周一 11月9日"));
        assert_eq!(strip_think("<think>想</think>\n# 标题", false), "# 标题");
        assert_eq!(strip_think("<think>还在想", true), "");
        assert_eq!(strip_think("# 标题<thi", true), "# 标题");
        assert_eq!(strip_fence("```markdown\n# T\n```"), "# T");
    }

    #[test]
    fn chunks_respect_limit() {
        let lines: Vec<String> = (0..10).map(|i| format!("line{i:02}")).collect();
        let refs: Vec<&str> = lines.iter().map(|s| s.as_str()).collect();
        let chunks = split_chunks(&refs, 20);
        assert!(chunks.iter().all(|c| c.chars().count() <= 21));
        assert_eq!(chunks.join("\n").lines().count(), 10);
    }

    #[test]
    fn meta_filled_by_program_and_glossary() {
        let mut tr = Transcript {
            source: "m.wav".into(),
            duration_ms: 90_000,
            speaker_names: Default::default(),
            sentences: vec![
                Sentence {
                    speaker: 0,
                    start_ms: 0,
                    end_ms: 900,
                    text: "下周五交付。".into(),
                },
                Sentence {
                    speaker: 1,
                    start_ms: 1000,
                    end_ms: 1900,
                    text: "好。".into(),
                },
            ],
        };
        tr.rename(&["张三".into()]);
        let out = with_meta(
            "```markdown\n# 周会\n\n## 一句话总结\n好\n```",
            &tr,
            "2026-11-02（星期一）",
        );
        assert!(out.starts_with("# 周会\n\n- 日期：2026-11-02（星期一）\n- 时长：01:30\n- 参会人：张三、说话人2\n\n## 一句话总结"));
        assert!(glossary(&["青鸾".into(), " ".into()]).contains("：青鸾。"));
        assert_eq!(glossary(&[]), "");
    }
}
