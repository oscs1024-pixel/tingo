//! 纪要末尾的术语表（```terms 代码块，JSON）：会上出现的专有名词的正确写法和转写里听错的写法。
//!
//! - 推送、Obsidian、会议列表的一句话摘要都先用 [`strip`] 去掉它（像鱼骨图那样整块处理）。
//! - 跨会议搜索（[`search`]）用它扩展查询词：搜听错的写法也能找到正确写法，反过来也一样。

use std::path::Path;

use serde::Deserialize;
use serde_json::{json, Value};

use crate::i18n::Lang;
use crate::meetings::{self, MINUTES, TRANSCRIPT_JSON};
use crate::transcript::Transcript;

const OPEN: &str = "```terms";

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
pub struct Term {
    #[serde(default)]
    pub term: String,
    #[serde(default)]
    pub variants: Vec<String>,
}

#[derive(Deserialize)]
struct Block {
    #[serde(default)]
    terms: Vec<Term>,
}

/// 下一个 ```terms 代码块：（开头位置，代码块内容，结束位置）。没闭合时内容和结束都到文末。
fn next_block(md: &str, from: usize) -> Option<(usize, &str, usize)> {
    let mut at = from;
    loop {
        let i = at + md[at..].find(OPEN)?;
        let line_start = i == 0 || md[..i].ends_with('\n');
        let rest = &md[i + OPEN.len()..];
        let word_end = rest.chars().next().is_none_or(|c| c.is_whitespace());
        if !(line_start && word_end) {
            at = i + OPEN.len();
            continue;
        }
        let body_start = match rest.find('\n') {
            Some(n) => i + OPEN.len() + n + 1,
            None => return Some((i, "", md.len())),
        };
        // 结束的 ``` 单独一行
        let mut pos = body_start;
        loop {
            let line_end = md[pos..].find('\n').map(|n| pos + n).unwrap_or(md.len());
            if md[pos..line_end].trim() == "```" {
                let end = if line_end < md.len() { line_end + 1 } else { line_end };
                return Some((i, &md[body_start..pos], end));
            }
            if line_end >= md.len() {
                return Some((i, &md[body_start..], md.len()));
            }
            pos = line_end + 1;
        }
    }
}

/// 去掉所有 terms 代码块（不完整的代码块去到文末），并收拾掉它留下的空行。
pub fn strip(md: &str) -> String {
    let mut out = String::new();
    let mut last = 0;
    while let Some((start, _, end)) = next_block(md, last) {
        out.push_str(&md[last..start]);
        let kept = out.trim_end().len();
        out.truncate(kept);
        last = end;
        let rest = md[last..].trim_start_matches(['\n', '\r']);
        last = md.len() - rest.len();
        if !out.is_empty() && !rest.trim().is_empty() {
            out.push_str("\n\n");
        }
    }
    out.push_str(&md[last..]);
    if md.ends_with('\n') && !out.is_empty() && !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

/// 纪要里所有 terms 代码块里的术语（解析不了的块跳过，空的术语丢掉）。
pub fn parse(md: &str) -> Vec<Term> {
    let mut out = Vec::new();
    let mut at = 0;
    while let Some((_, body, end)) = next_block(md, at) {
        at = end;
        let Ok(b) = serde_json::from_str::<Block>(body.trim()) else { continue };
        for t in b.terms {
            let term = t.term.trim().to_string();
            if term.is_empty() {
                continue;
            }
            let variants = t
                .variants
                .iter()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
                .collect();
            out.push(Term { term, variants });
        }
    }
    out
}

/// 去掉所有代码块（鱼骨图、术语表……），只留纪要正文。
pub fn strip_code_blocks(md: &str) -> String {
    let mut out = Vec::new();
    let mut fenced = false;
    for line in md.lines() {
        if line.trim_start().starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if !fenced {
            out.push(line);
        }
    }
    out.join("\n")
}

/// 比较查询词用：忽略大小写、去掉空白
fn norm(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).flat_map(char::to_lowercase).collect()
}

/// 查询词扩展：q 等于某个术语或它的某个听错写法时，把这一组都加进来。没有命中时就是 [q]。
pub fn expand(q: &str, terms: &[Term]) -> Vec<String> {
    let q = q.trim();
    let nq = norm(q);
    fn push(out: &mut Vec<String>, w: &str) {
        if !w.is_empty() && !out.iter().any(|x| norm(x) == norm(w)) {
            out.push(w.to_string());
        }
    }
    let mut out: Vec<String> = Vec::new();
    for t in terms {
        if norm(&t.term) == nq || t.variants.iter().any(|v| norm(v) == nq) {
            push(&mut out, &t.term);
            for v in &t.variants {
                push(&mut out, v);
            }
        }
    }
    if !out.iter().any(|x| norm(x) == nq) {
        out.insert(0, q.to_string());
    }
    out
}

/// 逐字符小写，保持字符位置一一对应
fn lower_chars(s: &str) -> Vec<char> {
    s.chars().map(|c| c.to_lowercase().next().unwrap_or(c)).collect()
}

/// 在 text 里找命中：包含 queries 任一项（忽略大小写），或者（fuzzy 至少 3 个字时）有和 fuzzy 等长、
/// 只差一个字符的片段。返回原文里命中的那段。
pub fn find_match(text: &str, queries: &[String], fuzzy: &str) -> Option<String> {
    let chars: Vec<char> = text.chars().collect();
    let low = lower_chars(text);
    let window = |q: &[char], max_diff: usize| -> Option<String> {
        if q.is_empty() || q.len() > low.len() {
            return None;
        }
        (0..=low.len() - q.len()).find_map(|i| {
            let diff = low[i..i + q.len()].iter().zip(q).filter(|(a, b)| a != b).count();
            (diff <= max_diff).then(|| chars[i..i + q.len()].iter().collect())
        })
    };
    for q in queries {
        if let Some(m) = window(&lower_chars(q.trim()), 0) {
            return Some(m);
        }
    }
    // 3 个字的词差一个字太容易误中（"验证码"会命中"验证手"），4 个字起才模糊匹配
    let f = lower_chars(fuzzy.trim());
    if f.len() >= 4 {
        return window(&f, 1);
    }
    None
}

const MAX_HITS: usize = 5;
const MAX_MEETINGS: usize = 30;

/// 跨会议搜索。`lang` 是界面语言（没改名的说话人叫 说话人N / Speaker N）。会议按时间倒序。
pub fn search(root: &Path, q: &str, lang: Lang) -> Value {
    let q = q.trim();
    let list = meetings::list(root);
    let docs: Vec<(String, Option<Transcript>)> = list
        .iter()
        .map(|m| {
            let dir = root.join(&m.id);
            let minutes = std::fs::read_to_string(dir.join(MINUTES)).unwrap_or_default();
            (minutes, Transcript::load(&dir.join(TRANSCRIPT_JSON)).ok())
        })
        .collect();
    let all_terms: Vec<Term> = docs.iter().flat_map(|(m, _)| parse(m)).collect();
    let expanded = expand(q, &all_terms);
    let mut results = Vec::new();
    for (m, (minutes, tr)) in list.iter().zip(&docs) {
        if results.len() >= MAX_MEETINGS {
            break;
        }
        let minutes_hit = !minutes.is_empty() && find_match(&strip_code_blocks(minutes), &expanded, q).is_some();
        let mut hits = Vec::new();
        if let Some(tr) = tr {
            let tr = tr.localized(lang);
            // 先列完全命中的句子，名额没满再补只差一个字的
            let mut seen = std::collections::HashSet::new();
            for exact in [true, false] {
                for s in &tr.sentences {
                    if hits.len() >= MAX_HITS {
                        break;
                    }
                    let found = if exact { find_match(&s.text, &expanded, "") } else { find_match(&s.text, &[], q) };
                    if let Some(found) = found {
                        if seen.insert(s.start_ms) {
                            hits.push(json!({"ms": s.start_ms, "speaker": tr.name_of(s.speaker), "text": s.text, "match": found}));
                        }
                    }
                }
            }
        }
        if minutes_hit || !hits.is_empty() {
            results.push(json!({
                "id": m.id, "title": m.title, "date": m.date, "time": m.time,
                "minutes_hit": minutes_hit, "hits": hits, "archived": m.archived,
            }));
        }
    }
    json!({"q": q, "expanded": expanded, "results": results})
}

#[cfg(test)]
mod tests {
    use super::*;

    const MD: &str = "# 周会\n\n## 一句话总结\n定了。\n\n## 原因分析\n```fishbone\n{\"problem\": \"为什么\", \"branches\": []}\n```\n\n```terms\n{\"terms\": [{\"term\": \"历史工单\", \"variants\": [\"历史中单\"]}, {\"term\": \"Kafka\", \"variants\": []}, {\"term\": \" \"}]}\n```\n";

    #[test]
    fn strip_removes_terms_block() {
        let out = strip(MD);
        assert!(!out.contains("terms") && !out.contains("历史工单"));
        assert!(out.ends_with("```fishbone\n{\"problem\": \"为什么\", \"branches\": []}\n```\n"));
        // 代码块在中间：前后正文保留，中间只留一个空行
        assert_eq!(strip("a\n\n```terms\n{}\n```\n\nb\n"), "a\n\nb\n");
        // 不完整（流式输出到一半）：去到文末
        assert_eq!(strip("# T\n\n正文\n\n```terms\n{\"terms\": [{\"te"), "# T\n\n正文");
        assert_eq!(strip("# T\n```terms"), "# T");
        // 没有 terms 块、或者只是行内提到 ```termsx：原样
        assert_eq!(strip("# T\n\n```termsx\nx\n```\n"), "# T\n\n```termsx\nx\n```\n");
        assert_eq!(strip("# T\n"), "# T\n");
    }

    #[test]
    fn parse_terms() {
        let t = parse(MD);
        assert_eq!(t.len(), 2);
        assert_eq!(t[0], Term { term: "历史工单".into(), variants: vec!["历史中单".into()] });
        assert!(parse("```terms\n不是 JSON\n```").is_empty());
        assert!(parse("```terms\n{\"terms\": []}\n```").is_empty());
    }

    #[test]
    fn query_expansion() {
        let t = parse(MD);
        assert_eq!(expand("历史中单", &t), vec!["历史工单", "历史中单"]);
        assert_eq!(expand(" 历史 工单 ", &t), vec!["历史工单", "历史中单"]);
        assert_eq!(expand("kafka", &t), vec!["Kafka"]);
        assert_eq!(expand("网关", &t), vec!["网关"]);
    }

    #[test]
    fn fuzzy_match_one_char() {
        let q = vec!["历史工单".to_string()];
        assert_eq!(find_match("把历史中单都导出来", &q, "历史工单").as_deref(), Some("历史中单"));
        assert_eq!(find_match("把历史工单都导出来", &q, "历史工单").as_deref(), Some("历史工单"));
        // 差两个字不算
        assert_eq!(find_match("把历来中单都导出来", &q, "历史工单"), None);
        // 不到 4 个字不做模糊
        let q2 = vec!["验证码".to_string()];
        assert_eq!(find_match("先验证手机", &q2, "验证码"), None);
        // 忽略大小写，返回原文写法
        let k = vec!["kafka".to_string()];
        assert_eq!(find_match("迁到 KAFKA 上", &k, "kafka").as_deref(), Some("KAFKA"));
        assert_eq!(find_match("迁到 Kafkb 上", &k, "kafka").as_deref(), Some("Kafkb"));
    }

    #[test]
    fn search_across_meetings() {
        let root = std::env::temp_dir().join(format!("tingo-search-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let a = root.join("2026-10-01_0900_周会");
        let b = root.join("2026-10-02_0900_复盘");
        for d in [&a, &b] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(a.join(MINUTES), MD).unwrap();
        let tr = Transcript {
            source: "录音.m4a".into(),
            duration_ms: 5000,
            speaker_names: [(0usize, "林舟".to_string())].into(),
            sentences: vec![
                crate::transcript::Sentence { speaker: 0, start_ms: 1000, end_ms: 2000, text: "历史中单要清理".into() },
                crate::transcript::Sentence { speaker: 1, start_ms: 3000, end_ms: 4000, text: "好的".into() },
            ],
        };
        tr.save(&a.join(TRANSCRIPT_JSON)).unwrap();
        // 第二场只有纪要命中
        std::fs::write(b.join(MINUTES), "# 复盘\n\n## 一句话总结\n历史工单清完了。\n").unwrap();
        std::fs::write(b.join(TRANSCRIPT_JSON), serde_json::to_string(&Transcript::default()).unwrap()).unwrap();
        let r = search(&root, "历史工单", Lang::Zh);
        assert_eq!(r["expanded"], json!(["历史工单", "历史中单"]));
        let res = r["results"].as_array().unwrap();
        assert_eq!(res.len(), 2);
        assert_eq!(res[0]["id"], "2026-10-02_0900_复盘"); // 时间倒序
        assert_eq!(res[0]["minutes_hit"], true);
        assert_eq!(res[0]["archived"], false); // 归档的会议照样搜，带上 archived
        assert_eq!(res[0]["hits"], json!([]));
        // 第一场：纪要正文里没写（只在代码块里），句子命中听错的写法
        assert_eq!(res[1]["minutes_hit"], false);
        assert_eq!(res[1]["hits"], json!([{"ms": 1000, "speaker": "林舟", "text": "历史中单要清理", "match": "历史中单"}]));
        let r = search(&root, "好的", Lang::En);
        assert_eq!(r["results"][0]["hits"][0]["speaker"], "Speaker 2");
        std::fs::remove_dir_all(root).ok();
    }
}
