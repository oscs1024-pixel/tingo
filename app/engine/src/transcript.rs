//! 转写结果。transcript.json 的格式与 Python 版完全一致，两个版本可以互相读对方的会议。

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Sentence {
    pub speaker: usize,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Turn {
    pub speaker: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Transcript {
    pub source: String,
    pub duration_ms: u64,
    /// 说话人编号 → 名字。JSON 里键是字符串（"0"），和 Python 版一致
    #[serde(default, with = "string_keys")]
    pub speaker_names: BTreeMap<usize, String>,
    pub sentences: Vec<Sentence>,
}

impl Transcript {
    pub fn name_of(&self, speaker: usize) -> String {
        self.speaker_names
            .get(&speaker)
            .filter(|n| !n.is_empty())
            .cloned()
            .unwrap_or_else(|| format!("说话人{}", speaker + 1))
    }

    /// 按首次发言顺序排列的说话人编号。
    pub fn speakers_in_order(&self) -> Vec<usize> {
        let mut seen = Vec::new();
        for s in &self.sentences {
            if !seen.contains(&s.speaker) {
                seen.push(s.speaker);
            }
        }
        seen
    }

    /// 按首次发言顺序给说话人命名，空字符串表示保留默认名。
    pub fn rename(&mut self, names: &[String]) {
        for (spk, name) in self.speakers_in_order().into_iter().zip(names) {
            if !name.trim().is_empty() {
                self.speaker_names.insert(spk, name.trim().to_string());
            }
        }
    }

    /// 同一说话人连续的句子合成一段发言。
    pub fn turns(&self) -> Vec<Turn> {
        let mut turns: Vec<Turn> = Vec::new();
        for s in &self.sentences {
            let name = self.name_of(s.speaker);
            match turns.last_mut() {
                Some(t) if t.speaker == name => {
                    t.text.push_str(&s.text);
                    t.end_ms = s.end_ms;
                }
                _ => turns.push(Turn {
                    speaker: name,
                    start_ms: s.start_ms,
                    end_ms: s.end_ms,
                    text: s.text.clone(),
                }),
            }
        }
        turns
    }

    /// `tingo record` 的录音统一叫 录音.m4a，这时用会议文件夹名。
    pub fn title(&self) -> String {
        let src = Path::new(&self.source);
        let stem = src
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        match src.parent().and_then(|p| p.file_name()) {
            Some(dir) if stem == "录音" => dir.to_string_lossy().into_owned(),
            _ => stem,
        }
    }

    pub fn to_markdown(&self) -> String {
        let mut out = format!(
            "# 会议转写：{}\n\n时长 {}，说话人 {} 位。\n\n",
            self.title(),
            fmt_ts(self.duration_ms),
            self.speakers_in_order().len()
        );
        for t in self.turns() {
            out.push_str(&format!(
                "**{}** `{}`\n\n{}\n\n",
                t.speaker,
                fmt_ts(t.start_ms),
                t.text
            ));
        }
        out.trim_end_matches('\n').to_string()
    }

    /// 喂给大模型的紧凑格式：[mm:ss] 说话人：内容。
    pub fn to_prompt_text(&self) -> String {
        self.turns()
            .iter()
            .map(|t| format!("[{}] {}：{}", fmt_ts(t.start_ms), t.speaker, t.text))
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn load(path: &Path) -> std::io::Result<Self> {
        let text = std::fs::read_to_string(path)?;
        serde_json::from_str(&text).map_err(std::io::Error::other)
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        std::fs::write(
            path,
            serde_json::to_string_pretty(self).map_err(std::io::Error::other)?,
        )
    }
}

pub fn fmt_ts(ms: u64) -> String {
    let s = ms / 1000;
    let (h, m, sec) = (s / 3600, (s % 3600) / 60, s % 60);
    if h > 0 {
        format!("{h}:{m:02}:{sec:02}")
    } else {
        format!("{m:02}:{sec:02}")
    }
}

mod string_keys {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    pub fn serialize<S: Serializer>(m: &BTreeMap<usize, String>, s: S) -> Result<S::Ok, S::Error> {
        m.iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect::<BTreeMap<_, _>>()
            .serialize(s)
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(
        d: D,
    ) -> Result<BTreeMap<usize, String>, D::Error> {
        let raw = BTreeMap::<String, String>::deserialize(d)?;
        raw.into_iter()
            .map(|(k, v)| k.parse().map(|k| (k, v)).map_err(serde::de::Error::custom))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tr() -> Transcript {
        Transcript {
            source: "/x/周会.m4a".into(),
            duration_ms: 65_000,
            speaker_names: BTreeMap::new(),
            sentences: vec![
                Sentence {
                    speaker: 1,
                    start_ms: 0,
                    end_ms: 1000,
                    text: "大家好，".into(),
                },
                Sentence {
                    speaker: 1,
                    start_ms: 1000,
                    end_ms: 2000,
                    text: "开始吧。".into(),
                },
                Sentence {
                    speaker: 0,
                    start_ms: 2500,
                    end_ms: 4000,
                    text: "好的。".into(),
                },
                Sentence {
                    speaker: 1,
                    start_ms: 61_000,
                    end_ms: 62_000,
                    text: "散会。".into(),
                },
            ],
        }
    }

    #[test]
    fn turns_merge_consecutive_same_speaker() {
        let turns = tr().turns();
        let got: Vec<(&str, &str)> = turns
            .iter()
            .map(|t| (t.speaker.as_str(), t.text.as_str()))
            .collect();
        assert_eq!(
            got,
            vec![
                ("说话人2", "大家好，开始吧。"),
                ("说话人1", "好的。"),
                ("说话人2", "散会。")
            ]
        );
        assert_eq!(turns[0].end_ms, 2000);
    }

    #[test]
    fn rename_by_first_appearance() {
        let mut t = tr();
        t.rename(&["张三".into(), "".into()]);
        assert_eq!(t.name_of(1), "张三");
        assert_eq!(t.name_of(0), "说话人1");
    }

    #[test]
    fn prompt_markdown_title() {
        let mut t = tr();
        assert_eq!(
            t.to_prompt_text().lines().last().unwrap(),
            "[01:01] 说话人2：散会。"
        );
        assert!(t
            .to_markdown()
            .starts_with("# 会议转写：周会\n\n时长 01:05，说话人 2 位。"));
        t.source = "/a/2026-11-02_1213_周会/录音.m4a".into();
        assert_eq!(t.title(), "2026-11-02_1213_周会");
    }

    #[test]
    fn json_compatible_with_python() {
        // Python 版写出的 transcript.json
        let py = r#"{"source": "m.wav", "duration_ms": 5000, "speaker_names": {"1": "张三"},
                     "sentences": [{"speaker": 1, "start_ms": 0, "end_ms": 900, "text": "好。"}]}"#;
        let t: Transcript = serde_json::from_str(py).unwrap();
        assert_eq!(t.name_of(1), "张三");
        let back: serde_json::Value =
            serde_json::from_str(&serde_json::to_string(&t).unwrap()).unwrap();
        assert_eq!(back["speaker_names"]["1"], "张三");
        assert_eq!(back["sentences"][0]["start_ms"], 0);
    }

    #[test]
    fn fmt_ts_hours() {
        assert_eq!(fmt_ts(0), "00:00");
        assert_eq!(fmt_ts(61_500), "01:01");
        assert_eq!(fmt_ts(3_725_000), "1:02:05");
    }
}
