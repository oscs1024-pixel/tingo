//! 会议文件夹的约定，和 Python 版 meetings.py 一致：一场会议一个文件夹。
//!
//! <根目录>/2026-11-02_1430_周会/{录音.m4a, 转写.md, transcript.json, 纪要.md}

use chrono::{Local, NaiveDate, NaiveDateTime};
use regex::Regex;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use crate::transcript::{fmt_ts, Transcript};

pub const AUDIO: &str = "录音.m4a";
pub const MINUTES: &str = "纪要.md";
pub const TRANSCRIPT_MD: &str = "转写.md";
pub const TRANSCRIPT_JSON: &str = "transcript.json";
const RAW_TRACKS: [&str; 3] = ["system.wav", "mic.wav", "mixed.wav"];
pub const AUDIO_EXTS: [&str; 10] = [
    "m4a", "mp3", "wav", "mp4", "aac", "flac", "ogg", "webm", "mov", "mkv",
];

static FOLDER: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_(.+)$").unwrap());

pub fn default_root() -> PathBuf {
    if let Some(p) = crate::config::env_var("ROOT") {
        return PathBuf::from(p);
    }
    let docs = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default()
        .join("Documents");
    crate::config::with_legacy(docs.join("Tingo"), docs.join("jiyao"))
}

pub fn new_folder(root: &Path, title: &str, when: NaiveDateTime) -> PathBuf {
    static UNSAFE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"[\\/:*?"<>|\s]+"#).unwrap());
    let safe = UNSAFE.replace_all(title.trim(), "_");
    let safe = if safe.is_empty() {
        "会议".into()
    } else {
        safe
    };
    let base = format!("{}_{safe}", when.format("%Y-%m-%d_%H%M"));
    let mut path = root.join(&base);
    let mut n = 2;
    while path.exists() {
        path = root.join(format!("{base}_{n}"));
        n += 1;
    }
    path
}

pub fn save_transcript(tr: &Transcript, dir: &Path) -> std::io::Result<()> {
    tr.save(&dir.join(TRANSCRIPT_JSON))?;
    std::fs::write(dir.join(TRANSCRIPT_MD), tr.to_markdown())
}

pub fn find_audio(dir: &Path) -> Option<PathBuf> {
    let main = dir.join(AUDIO);
    if main.is_file() {
        return Some(main);
    }
    let mut files: Vec<PathBuf> = std::fs::read_dir(dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .collect();
    files.sort();
    files.into_iter().find(|p| {
        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let ext = p
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_lowercase();
        AUDIO_EXTS.contains(&ext.as_str()) && !RAW_TRACKS.contains(&name)
    })
}

fn modified_date(p: &Path) -> Option<NaiveDate> {
    let t = std::fs::metadata(p).ok()?.modified().ok()?;
    Some(chrono::DateTime::<Local>::from(t).date_naive())
}

/// 会议日期：文件夹名里的优先，否则用录音或转写文件的修改时间。
pub fn meeting_date(dir: &Path) -> NaiveDate {
    let name = dir.file_name().and_then(|n| n.to_str()).unwrap_or("");
    if let Some(c) = FOLDER.captures(name) {
        if let Ok(d) = NaiveDate::parse_from_str(&c[1], "%Y-%m-%d") {
            return d;
        }
    }
    find_audio(dir)
        .and_then(|a| modified_date(&a))
        .or_else(|| modified_date(&dir.join(TRANSCRIPT_JSON)))
        .unwrap_or_else(|| Local::now().date_naive())
}

pub fn minutes_title(text: &str) -> Option<String> {
    text.lines()
        .find_map(|l| l.strip_prefix("# ").map(|t| t.trim().to_string()))
}

/// 纪要里"一句话总结"下面的那句。
pub fn one_liner(text: &str) -> String {
    let mut lines = text.lines();
    while let Some(l) = lines.next() {
        if l.trim() == "## 一句话总结" {
            return lines
                .find(|n| !n.trim().is_empty())
                .map(|n| n.trim().to_string())
                .unwrap_or_default();
        }
    }
    String::new()
}

#[derive(Clone, Debug, Serialize)]
pub struct Summary {
    pub id: String,
    pub title: String,
    pub date: String,
    pub time: String,
    pub summary: String,
    pub has_minutes: bool,
    pub has_transcript: bool,
    pub duration: String,
    pub speakers: Vec<String>,
}

pub fn summary(dir: &Path) -> Summary {
    let name = dir
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_string();
    let minutes = std::fs::read_to_string(dir.join(MINUTES)).unwrap_or_default();
    let caps = FOLDER.captures(&name);
    let time = caps
        .as_ref()
        .map(|c| format!("{}:{}", &c[2], &c[3]))
        .unwrap_or_default();
    let fallback = caps
        .as_ref()
        .map(|c| c[4].to_string())
        .unwrap_or_else(|| name.clone());
    let tr = Transcript::load(&dir.join(TRANSCRIPT_JSON)).ok();
    Summary {
        title: minutes_title(&minutes).unwrap_or(fallback),
        date: meeting_date(dir).format("%Y-%m-%d").to_string(),
        time,
        summary: one_liner(&minutes),
        has_minutes: !minutes.is_empty(),
        has_transcript: tr.is_some(),
        duration: tr
            .as_ref()
            .map(|t| fmt_ts(t.duration_ms))
            .unwrap_or_default(),
        speakers: tr
            .as_ref()
            .map(|t| {
                t.speakers_in_order()
                    .into_iter()
                    .map(|s| t.name_of(s))
                    .collect()
            })
            .unwrap_or_default(),
        id: name,
    }
}

pub fn list(root: &Path) -> Vec<Summary> {
    let Ok(rd) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut items: Vec<Summary> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.is_dir() && (p.join(TRANSCRIPT_JSON).is_file() || find_audio(p).is_some()))
        .map(|p| summary(&p))
        .collect();
    items.sort_by(|a, b| (&b.date, &b.time, &b.id).cmp(&(&a.date, &a.time, &a.id)));
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folder_naming_and_listing() {
        let root = std::env::temp_dir().join(format!("tingo-meet-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let when = NaiveDate::from_ymd_opt(2026, 11, 2)
            .unwrap()
            .and_hms_opt(14, 30, 0)
            .unwrap();
        let a = new_folder(&root, "周 会/评审", when);
        assert_eq!(a.file_name().unwrap(), "2026-11-02_1430_周_会_评审");
        std::fs::create_dir_all(&a).unwrap();
        assert!(new_folder(&root, "周 会/评审", when).ends_with("2026-11-02_1430_周_会_评审_2"));
        std::fs::write(a.join(MINUTES), "# 周会\n\n## 一句话总结\n定了上线时间。\n").unwrap();
        std::fs::write(a.join("录音.m4a"), b"x").unwrap();
        let raw = root.join("2026-11-02_1500_录音中");
        std::fs::create_dir_all(&raw).unwrap();
        std::fs::write(raw.join("system.wav"), b"x").unwrap(); // 录音中的原始轨道不算会议
        let items = list(&root);
        assert_eq!(items.len(), 1);
        assert_eq!(
            (
                items[0].title.as_str(),
                items[0].summary.as_str(),
                items[0].time.as_str()
            ),
            ("周会", "定了上线时间。", "14:30")
        );
        assert_eq!(
            meeting_date(&a),
            NaiveDate::from_ymd_opt(2026, 11, 2).unwrap()
        );
        std::fs::remove_dir_all(root).ok();
    }
}
