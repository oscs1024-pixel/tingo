//! 把纪要存进 Obsidian 库：一场会议一篇笔记（带属性），鱼骨图另存为 Excalidraw 画板嵌进笔记，
//! 转写全文放在折叠的引用块里。重复导出覆盖同名笔记。

use crate::config::ObsidianConfig;
use crate::fishbone::{self, fmt_ms, Level};
use crate::meetings::{self, MINUTES, TRANSCRIPT_JSON};
use crate::transcript::Transcript;
use serde_json::Value;
use std::path::{Path, PathBuf};

/// 本机 Obsidian 打开过的库（读 Obsidian 自己的配置），设置页里直接选。
pub fn vaults() -> Vec<(String, String)> {
    let Some(home) = std::env::var_os("HOME") else { return vec![] };
    let p = PathBuf::from(home).join("Library/Application Support/obsidian/obsidian.json");
    let Ok(v) = std::fs::read_to_string(p).map(|s| serde_json::from_str::<Value>(&s).unwrap_or_default()) else {
        return vec![];
    };
    let mut out: Vec<(u64, String, String)> = v["vaults"]
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(_, x)| {
            let path = x["path"].as_str()?;
            Path::new(path).is_dir().then(|| {
                let name = Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                (x["ts"].as_u64().unwrap_or(0), name, path.to_string())
            })
        })
        .collect();
    out.sort_by(|a, b| b.0.cmp(&a.0)); // 最近打开的在前
    out.into_iter().map(|(_, n, p)| (n, p)).collect()
}

/// 文件名里不能出现的字符（Obsidian 链接也不认 # ^ [ ] |）
fn safe_name(s: &str) -> String {
    let s: String = s
        .chars()
        .map(|c| if "/\\:*?\"<>|#^[]".contains(c) || c.is_control() { ' ' } else { c })
        .collect();
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn yaml_str(s: &str) -> String {
    serde_json::to_string(s).unwrap() // JSON 字符串也是合法的 YAML 字符串
}

/// 打开某篇笔记的 obsidian:// 链接
pub fn uri(path: &Path) -> String {
    let mut out = String::from("obsidian://open?path=");
    for b in path.to_string_lossy().bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~/".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

pub struct Exported {
    pub note: PathBuf,
    pub drawings: Vec<PathBuf>,
}

pub fn export(cfg: &ObsidianConfig, dir: &Path) -> Result<Exported, String> {
    let vault = PathBuf::from(cfg.vault.trim());
    if cfg.vault.trim().is_empty() || !vault.is_dir() {
        return Err("还没有设置 Obsidian 库，或者库的文件夹不存在，去设置里选一下".into());
    }
    let minutes = std::fs::read_to_string(dir.join(MINUTES)).map_err(|_| "这场会议还没有纪要".to_string())?;
    let tr = Transcript::load(&dir.join(TRANSCRIPT_JSON)).ok();
    let id = dir.file_name().unwrap_or_default().to_string_lossy().into_owned();
    let date = meetings::meeting_date(dir);
    // 用会议文件夹的标题命名（重新生成纪要标题会变，文件名不跟着变，避免同一场会出现多篇）
    let topic = id.splitn(3, '_').nth(2).unwrap_or(&id);
    let name = safe_name(&format!("{} {}", date.format("%Y-%m-%d"), topic));
    let folder = vault.join(cfg.folder.trim().trim_matches('/'));
    std::fs::create_dir_all(&folder).map_err(|e| format!("建不了文件夹 {}：{e}", folder.display()))?;

    let turns = tr.as_ref().map(|t| t.turns()).unwrap_or_default();
    let duration = tr.as_ref().map(|t| t.duration_ms).unwrap_or(u64::MAX);
    let mut body = String::new();
    let mut last = 0;
    let mut drawings = Vec::new();
    for (k, (range, spec)) in fishbone::blocks(&minutes).into_iter().enumerate() {
        body.push_str(&minutes[last..range.start]);
        last = range.end;
        let Some(spec) = spec else { continue };
        let grounds: Vec<_> = spec.branches.iter().map(|b| fishbone::ground(b, &turns, duration)).collect();
        let draw_name = format!("{name} 鱼骨图{}", if k == 0 { String::new() } else { (k + 1).to_string() });
        let path = folder.join(format!("{draw_name}.excalidraw.md"));
        std::fs::write(&path, fishbone::excalidraw_md(&fishbone::excalidraw(&spec, &grounds))).map_err(|e| e.to_string())?;
        drawings.push(path);
        body.push_str(&format!("![[{draw_name}.excalidraw]]\n\n**{}**\n", spec.problem));
        for (b, g) in spec.branches.iter().zip(&grounds) {
            let detail = if b.detail.is_empty() { String::new() } else { format!("：{}", b.detail) };
            let mark = if g.level == Level::None { " ⚠️" } else { "" };
            body.push_str(&format!("- **{}**{detail} · {}{mark}\n", b.title, g.label()));
            for q in &g.quotes {
                body.push_str(&format!("    - `{}` {}：{}\n", fmt_ms(q.ms), q.speaker, q.text));
            }
        }
    }
    body.push_str(&minutes[last..]);

    let title = meetings::minutes_title(&minutes).unwrap_or_else(|| topic.to_string());
    let mut fm = vec![
        "---".to_string(),
        format!("title: {}", yaml_str(&title)),
        format!("date: {}", date.format("%Y-%m-%d")),
    ];
    if let Some(t) = &tr {
        fm.push(format!("duration: {}", yaml_str(&fmt_ms(t.duration_ms))));
        let names: Vec<String> = t.speakers_in_order().iter().map(|&s| yaml_str(&t.name_of(s))).collect();
        fm.push(format!("speakers: [{}]", names.join(", ")));
    }
    fm.push("tags: [会议纪要]".into());
    fm.push(format!("jiyao: {}", yaml_str(&id)));
    fm.push("---\n".into());
    let mut note = fm.join("\n") + body.trim_end() + "\n";

    if cfg.transcript {
        if let Some(t) = &tr {
            note.push_str(&format!(
                "\n## 转写\n\n> [!quote]- 转写全文（{}，{} 人）\n",
                fmt_ms(t.duration_ms),
                t.speakers_in_order().len()
            ));
            for turn in &turns {
                note.push_str(&format!(">\n> **{}** `{}`\n> {}\n", turn.speaker, fmt_ms(turn.start_ms), turn.text));
            }
        }
    }
    let path = folder.join(format!("{name}.md"));
    std::fs::write(&path, note).map_err(|e| format!("写不进 {}：{e}", path.display()))?;
    Ok(Exported { note: path, drawings })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exports_note_with_drawing_and_transcript() {
        let base = std::env::temp_dir().join(format!("jiyao-obs-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let dir = base.join("meetings/2026-11-02_1030_发布评审");
        let vault = base.join("vault");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::create_dir_all(&vault).unwrap();
        let tr: Transcript = serde_json::from_str(
            r#"{"source":"a.wav","duration_ms":52000,"speaker_names":{"0":"李经理","1":"小张"},
                "sentences":[{"speaker":0,"start_ms":0,"end_ms":8000,"text":"今天讨论发布。"},
                             {"speaker":1,"start_ms":9000,"end_ms":18000,"text":"支付模块还有两个问题没修，十五号才能提测。"}]}"#,
        )
        .unwrap();
        tr.save(&dir.join(TRANSCRIPT_JSON)).unwrap();
        std::fs::write(
            dir.join(MINUTES),
            "# 发布评审会\n\n## 原因分析\n```fishbone\n{\"problem\": \"为什么延期？\", \"branches\": [{\"title\": \"支付有问题\", \"refs\": [\"00:09\"]}, {\"title\": \"猜的\", \"refs\": []}]}\n```\n",
        )
        .unwrap();
        let cfg = ObsidianConfig { vault: vault.to_string_lossy().into(), folder: "会议纪要".into(), auto: true, transcript: true };
        let out = export(&cfg, &dir).unwrap();
        assert_eq!(out.note, vault.join("会议纪要/2026-11-02 发布评审.md"));
        let note = std::fs::read_to_string(&out.note).unwrap();
        assert!(note.starts_with("---\ntitle: \"发布评审会\"\ndate: 2026-11-02\n"));
        assert!(note.contains("speakers: [\"李经理\", \"小张\"]"));
        assert!(note.contains("![[2026-11-02 发布评审 鱼骨图.excalidraw]]"));
        assert!(note.contains("- **猜的** · 无原文依据 ⚠️"));
        assert!(note.contains("    - `00:09` 小张：支付模块"));
        assert!(note.contains("> [!quote]- 转写全文（00:52，2 人）"));
        assert!(!note.contains("```fishbone"));
        assert!(out.drawings[0].ends_with("2026-11-02 发布评审 鱼骨图.excalidraw.md"));
        assert_eq!(uri(Path::new("/a b/会")), "obsidian://open?path=/a%20b/%E4%BC%9A");
        let _ = std::fs::remove_dir_all(&base);
    }
}
