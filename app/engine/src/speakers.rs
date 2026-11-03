//! 让大模型根据转写里的明确线索（自我介绍、被点名后回应等）猜说话人的名字。
//! 只给有把握的；index 和会议详情里 speaker_list[].index 一致（按首次发言顺序，0 起）。

use serde::Serialize;
use serde_json::Value;

use crate::i18n::Lang;
use crate::transcript::Transcript;

/// 交给模型的转写最多这么多字（取开头）
const MAX_CHARS: usize = 12000;
/// 名字最长这么多字
const MAX_NAME: usize = 20;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Suggestion {
    pub index: usize,
    pub name: String,
    pub evidence: String,
}

const SYSTEM: &str = "你是一位细心的会议助理，只根据会议转写里的明确线索判断每位说话人是谁。";

const PROMPT: &str = "下面是一场会议的语音转写，格式为「[时间戳] 说话人：内容」。说话人是程序自动区分的，编号如下：
{speakers}

请根据转写里的明确线索，推断每位说话人的名字或称呼（如\"王总\"\"小李\"）。明确线索包括：
1. 自我介绍，如\"我是林舟\"；
2. 被别人点名称呼，如\"林舟你说一下\"，紧接着发言回应的人；
3. 别人直接称呼他/她，他/她作出了回应。

要求：
- 只给有把握的说话人；没有明确线索的不要输出，不能凭说话内容猜职位或身份。
- name 写转写里出现的名字或称呼原文；evidence 写支撑判断的那句原话，带时间戳和说话人，如\"[00:30] 说话人2：林舟你说一下\"。
- 只输出 JSON，不要输出其他内容，格式：
{\"suggestions\": [{\"index\": <说话人编号>, \"name\": \"<名字或称呼>\", \"evidence\": \"<原话>\"}]}
一个都没有把握时输出 {\"suggestions\": []}。

转写：
{transcript}
";

const SYSTEM_EN: &str = "You are a careful meeting assistant. You identify who each speaker is using only explicit cues in the meeting transcript.";

const PROMPT_EN: &str = "Below is the speech-recognition transcript of a meeting, one line per turn in the form \"[timestamp] Speaker: content\". Speakers were separated automatically and are numbered as follows:
{speakers}

From explicit cues in the transcript, infer each speaker's name or form of address (e.g. \"Mr. Wang\", \"Lisa\"). Explicit cues are:
1. Self-introductions, e.g. \"I'm Lin Zhou\";
2. Being called on by name, e.g. \"Lin Zhou, go ahead\", followed by that speaker responding;
3. Being addressed directly by someone else and responding.

Rules:
- Only include speakers you are confident about; leave out anyone without an explicit cue. Never guess a job title or role from what someone talks about.
- For name, use the name or form of address exactly as it appears in the transcript; for evidence, quote the line that supports it, with its timestamp and speaker, e.g. \"[00:30] Speaker 2: Lin Zhou, go ahead\".
- Output JSON only, nothing else, in this format:
{\"suggestions\": [{\"index\": <speaker number>, \"name\": \"<name or form of address>\", \"evidence\": \"<quoted line>\"}]}
If you are not confident about anyone, output {\"suggestions\": []}.

Transcript:
{transcript}
";

/// （system, prompt）。`lang` 是界面语言：英文时没改名的说话人叫 Speaker N，提示词也用英文。
pub fn prompt(tr: &Transcript, lang: Lang) -> (&'static str, String) {
    let t = tr.localized(lang);
    let speakers: Vec<String> = t
        .speakers_in_order()
        .iter()
        .enumerate()
        .map(|(i, &s)| match lang {
            Lang::Zh => format!("- 编号 {i}：{}", t.name_of(s)),
            Lang::En => format!("- Number {i}: {}", t.name_of(s)),
        })
        .collect();
    let text: String = t.to_prompt_text_in(lang).chars().take(MAX_CHARS).collect();
    let (system, template) = match lang {
        Lang::Zh => (SYSTEM, PROMPT),
        Lang::En => (SYSTEM_EN, PROMPT_EN),
    };
    let p = template.replace("{speakers}", &speakers.join("\n")).replace("{transcript}", &text);
    (system, p)
}

fn clip(s: &str, n: usize) -> String {
    s.trim().chars().take(n).collect::<String>().trim().to_string()
}

/// 解析模型的回答：容忍 ```json 包裹和前后多余的文字（取第一个 { 到最后一个 }）。
/// 名字去空白、限长；编号越界、名字为空的丢掉；同一个编号只留第一条。解析不了返回 None。
pub fn parse(text: &str, speakers: usize) -> Option<Vec<Suggestion>> {
    let (a, b) = (text.find('{')?, text.rfind('}')?);
    if b < a {
        return None;
    }
    let v: Value = serde_json::from_str(&text[a..=b]).ok()?;
    let list = v.get("suggestions")?.as_array()?;
    let mut out: Vec<Suggestion> = Vec::new();
    for s in list {
        let index = match &s["index"] {
            Value::Number(n) => n.as_u64(),
            Value::String(x) => x.trim().parse().ok(),
            _ => None,
        };
        let Some(index) = index.map(|i| i as usize).filter(|&i| i < speakers) else { continue };
        let name = clip(s["name"].as_str().unwrap_or(""), MAX_NAME);
        if name.is_empty() || out.iter().any(|o| o.index == index) {
            continue;
        }
        let evidence = clip(s["evidence"].as_str().unwrap_or(""), 300);
        out.push(Suggestion { index, name, evidence });
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transcript::Sentence;

    #[test]
    fn parse_wrapped_and_noisy_output() {
        let raw = "好的，结果如下：\n```json\n{\"suggestions\": [{\"index\": 1, \"name\": \" 林舟 \", \"evidence\": \"[00:30] 说话人2：林舟你说一下\"}, {\"index\": 5, \"name\": \"越界\"}, {\"index\": \"0\", \"name\": \"王总\"}, {\"index\": 0, \"name\": \"重复\"}, {\"index\": 2, \"name\": \"\"}]}\n```\n以上。";
        let got = parse(raw, 3).unwrap();
        assert_eq!(
            got,
            vec![
                Suggestion { index: 1, name: "林舟".into(), evidence: "[00:30] 说话人2：林舟你说一下".into() },
                Suggestion { index: 0, name: "王总".into(), evidence: "".into() },
            ]
        );
        // 名字限长 20 字
        let long = format!("{{\"suggestions\": [{{\"index\": 0, \"name\": \"{}\"}}]}}", "长".repeat(30));
        assert_eq!(parse(&long, 1).unwrap()[0].name.chars().count(), 20);
        assert_eq!(parse("{\"suggestions\": []}", 2), Some(vec![]));
        assert_eq!(parse("我不知道", 2), None);
        assert_eq!(parse("{\"suggestions\": [", 2), None);
        assert_eq!(parse("} {", 2), None);
        assert_eq!(parse("{\"names\": []}", 2), None);
    }

    #[test]
    fn prompt_lists_speakers_by_first_appearance() {
        let t = Transcript {
            sentences: vec![
                Sentence { speaker: 1, start_ms: 0, end_ms: 900, text: "我是林舟。".into() },
                Sentence { speaker: 0, start_ms: 1000, end_ms: 1900, text: "好。".into() },
            ],
            ..Default::default()
        };
        let (_, zh) = prompt(&t, Lang::Zh);
        assert!(zh.contains("- 编号 0：说话人2\n- 编号 1：说话人1"));
        assert!(zh.contains("[00:00] 说话人2：我是林舟。"));
        let (_, en) = prompt(&t, Lang::En);
        assert!(en.contains("- Number 0: Speaker 2\n- Number 1: Speaker 1"));
        assert!(en.contains("[00:01] Speaker 1: 好。"));
    }
}
