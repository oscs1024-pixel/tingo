//! 纪要里的鱼骨图（```fishbone 代码块，JSON）：解析、对照转写核实引用、画成 Excalidraw。
//! 引用核实的规则与界面（app/src/fishbone.ts）一致：时间戳落在某段发言里才算引用了这段话。
//! 图上的文字跟纪要的语言走（见 [`crate::i18n::doc_lang`]）。

use crate::i18n::Lang;
use crate::transcript::Turn;
use regex::Regex;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::LazyLock;

static FENCE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)```fishbone\s*\n(.*?)\n```").unwrap());

const WEAK_CHARS: usize = 20; // 引用的原文不到这么多字，算"依据很少"

#[derive(Deserialize, Clone, Debug)]
pub struct Branch {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub refs: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct Spec {
    pub problem: String,
    pub branches: Vec<Branch>,
}

pub fn parse(json: &str) -> Option<Spec> {
    let mut s: Spec = serde_json::from_str(json).ok()?;
    s.branches.retain(|b| !b.title.is_empty());
    Some(s)
}

/// 纪要里所有鱼骨图代码块：（代码块在原文里的位置，解析结果）。
pub fn blocks(md: &str) -> Vec<(std::ops::Range<usize>, Option<Spec>)> {
    FENCE.captures_iter(md).map(|c| (c.get(0).unwrap().range(), parse(&c[1]))).collect()
}

#[derive(Clone, Debug, PartialEq)]
pub struct Quote {
    pub ms: u64,
    pub speaker: String,
    pub text: String,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Level {
    Ok,
    Weak,
    None,
}

#[derive(Clone, Debug)]
pub struct Grounding {
    pub quotes: Vec<Quote>,
    pub chars: usize,
    pub level: Level,
}

impl Grounding {
    pub fn label(&self, lang: Lang) -> String {
        let (n, c) = (self.quotes.len(), self.chars);
        match (self.level, lang) {
            (Level::None, Lang::Zh) => "无原文依据".into(),
            (Level::Weak, Lang::Zh) => format!("依据很少 · 引用 {n} 段 · {c} 字"),
            (Level::Ok, Lang::Zh) => format!("引用 {n} 段 · {c} 字"),
            (Level::None, Lang::En) => "No evidence".into(),
            (Level::Weak, Lang::En) => format!("Thin evidence · {n} quote(s) · {c} chars"),
            (Level::Ok, Lang::En) => format!("{n} quote(s) · {c} chars"),
        }
    }
}

fn ts_ms(t: &str) -> Option<u64> {
    let parts: Vec<u64> = t.split(':').map(|p| p.parse().ok()).collect::<Option<_>>()?;
    match parts[..] {
        [m, s] => Some((m * 60 + s) * 1000),
        [h, m, s] => Some((h * 3600 + m * 60 + s) * 1000),
        _ => None,
    }
}

pub fn fmt_ms(ms: u64) -> String {
    let s = ms / 1000;
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
    } else {
        format!("{:02}:{:02}", s / 60, s % 60)
    }
}

pub fn resolve(turns: &[Turn], duration_ms: u64, r: &str) -> Option<Quote> {
    let ms = ts_ms(r.trim().trim_matches(|c| c == '[' || c == ']'))?;
    if ms > duration_ms + 1000 {
        return None;
    }
    let t = turns.iter().take_while(|t| t.start_ms <= ms + 999).last()?;
    Some(Quote { ms: t.start_ms, speaker: t.speaker.clone(), text: t.text.clone() })
}

pub fn ground(b: &Branch, turns: &[Turn], duration_ms: u64) -> Grounding {
    let mut quotes: Vec<Quote> = Vec::new();
    for r in &b.refs {
        if let Some(q) = resolve(turns, duration_ms, r) {
            if !quotes.iter().any(|x| x.ms == q.ms) {
                quotes.push(q);
            }
        }
    }
    quotes.sort_by_key(|q| q.ms);
    let chars = quotes.iter().map(|q| q.text.chars().count()).sum();
    let level = if quotes.is_empty() {
        Level::None
    } else if chars < WEAK_CHARS {
        Level::Weak
    } else {
        Level::Ok
    };
    Grounding { quotes, chars, level }
}

// ---------- Excalidraw ----------

const INK: &str = "#1e1e1e";
const MUTED: &str = "#868e96";

fn color(l: Level) -> &'static str {
    match l {
        Level::Ok => INK,
        Level::Weak => "#f08c00",
        Level::None => "#e03131",
    }
}

/// 估算文字宽度：汉字算一个字号宽，其余算半个多。Excalidraw 打开后会按真实字体重新排。
fn text_width(s: &str, size: f64) -> f64 {
    s.lines()
        .map(|l| l.chars().map(|c| if c.is_ascii() { 0.6 } else { 1.0 }).sum::<f64>() * size)
        .fold(0.0, f64::max)
}

/// 按宽度折行：中文没有空格，逐字折；英文单词不拆开，整个挪到下一行（单词比一行还长时才拆）。
fn wrap(s: &str, size: f64, max: f64) -> String {
    let cw = |c: char| if c.is_ascii() { 0.6 } else { 1.0 } * size;
    let mut out = String::new();
    let mut w = 0.0;
    for c in s.chars() {
        if w + cw(c) > max && w > 0.0 {
            let line_start = out.rfind('\n').map_or(0, |i| i + 1);
            // 正在一个英文单词中间：从这个单词前的空格处折
            let cut = (c.is_ascii_alphanumeric() && out.ends_with(|p: char| p.is_ascii_alphanumeric()))
                .then(|| out[line_start..].rfind(' ').map(|i| line_start + i))
                .flatten()
                .filter(|&i| i > line_start);
            match cut {
                Some(i) => {
                    let word = out.split_off(i + 1);
                    out.pop(); // 行尾的空格
                    out.push('\n');
                    w = word.chars().map(cw).sum();
                    out.push_str(&word);
                }
                None => {
                    if out.ends_with(' ') {
                        out.pop();
                    }
                    out.push('\n');
                    w = 0.0;
                    if c == ' ' {
                        continue; // 折行处的空格不要
                    }
                }
            }
        }
        out.push(c);
        w += cw(c);
    }
    out
}

fn clip(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.into()
    } else {
        format!("{}…", s.chars().take(n).collect::<String>())
    }
}

struct Canvas {
    elements: Vec<Value>,
    seed: u64,
}

impl Canvas {
    fn next(&mut self) -> (String, u64) {
        self.seed += 1;
        (format!("jy{:06x}", self.seed * 7919 % 0xffffff), 100_000 + self.seed * 7919)
    }

    fn base(&mut self, kind: &str, x: f64, y: f64, w: f64, h: f64, stroke: &str) -> serde_json::Map<String, Value> {
        let (id, seed) = self.next();
        let v = json!({
            "id": id, "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
            "strokeColor": stroke, "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 2, "strokeStyle": "solid", "roughness": 1, "opacity": 100,
            "groupIds": [], "frameId": null, "roundness": null, "seed": seed, "version": 1,
            "versionNonce": seed + 1, "isDeleted": false, "boundElements": [], "updated": 1,
            "link": null, "locked": false,
        });
        v.as_object().unwrap().clone()
    }

    fn text(&mut self, x: f64, y: f64, s: &str, size: f64, stroke: &str) -> f64 {
        let lines = s.lines().count().max(1) as f64;
        let h = lines * size * 1.25;
        let mut e = self.base("text", x, y, text_width(s, size), h, stroke);
        for (k, v) in [
            ("text", json!(s)),
            ("originalText", json!(s)),
            ("fontSize", json!(size)),
            ("fontFamily", json!(5)),
            ("textAlign", json!("left")),
            ("verticalAlign", json!("top")),
            ("containerId", json!(null)),
            ("lineHeight", json!(1.25)),
            ("autoResize", json!(true)),
        ] {
            e.insert(k.into(), v);
        }
        self.elements.push(Value::Object(e));
        h
    }

    fn line(&mut self, from: (f64, f64), to: (f64, f64), stroke: &str, width: f64) {
        let (dx, dy) = (to.0 - from.0, to.1 - from.1);
        let mut e = self.base("line", from.0, from.1, dx.abs(), dy.abs(), stroke);
        e.insert("strokeWidth".into(), json!(width));
        e.insert("points".into(), json!([[0, 0], [dx, dy]]));
        for k in ["lastCommittedPoint", "startBinding", "endBinding", "startArrowhead", "endArrowhead"] {
            e.insert(k.into(), Value::Null);
        }
        self.elements.push(Value::Object(e));
    }

    fn rect(&mut self, x: f64, y: f64, w: f64, h: f64, stroke: &str, fill: &str) {
        let mut e = self.base("rectangle", x, y, w, h, stroke);
        e.insert("backgroundColor".into(), json!(fill));
        e.insert("roundness".into(), json!({"type": 3}));
        self.elements.push(Value::Object(e));
    }
}

/// 画一张经典鱼骨图：主骨水平，问题在鱼头（右侧），分支上下交替斜插在主骨上，
/// 每个分支下面写着引用统计和引用的原话；没有原文依据的分支画成红色。
pub fn excalidraw(spec: &Spec, grounds: &[Grounding], lang: Lang) -> Value {
    const COL: f64 = 300.0; // 每对分支占的宽度
    const RISE: f64 = 150.0; // 斜骨的高度
    const SLANT: f64 = 110.0;
    const TEXT_W: f64 = 270.0;
    let mut c = Canvas { elements: Vec::new(), seed: 0 };
    let cols = spec.branches.len().div_ceil(2).max(1) as f64;
    let spine_end = cols * COL + 40.0;

    for (i, b) in spec.branches.iter().enumerate() {
        let g = &grounds[i];
        let up = i % 2 == 0;
        let attach = ((i / 2) as f64 * COL + 230.0, 0.0);
        let tip = (attach.0 - SLANT, if up { -RISE } else { RISE });
        c.line(tip, attach, color(g.level), 2.0);

        // 文字块：标题、补充、引用统计、原话
        let mut parts: Vec<(String, f64, &str)> = vec![(wrap(&b.title, 20.0, TEXT_W), 20.0, color(g.level))];
        if !b.detail.is_empty() {
            parts.push((wrap(&b.detail, 14.0, TEXT_W), 14.0, MUTED));
        }
        parts.push((g.label(lang), 13.0, color(g.level)));
        for q in g.quotes.iter().take(3) {
            let s = format!("{} {}{}{}", fmt_ms(q.ms), q.speaker, lang.pick("：", ": "), clip(&q.text, 28));
            parts.push((wrap(&s, 12.0, TEXT_W), 12.0, MUTED));
        }
        let gap = 6.0;
        let height: f64 = parts.iter().map(|(s, size, _)| s.lines().count() as f64 * size * 1.25 + gap).sum();
        let x = tip.0 - 40.0;
        let mut y = if up { tip.1 - height - 8.0 } else { tip.1 + 10.0 };
        for (s, size, col) in parts {
            y += c.text(x, y, &s, size, col) + gap;
        }
    }

    c.line((0.0, 0.0), (spine_end, 0.0), INK, 4.0);
    // 鱼头：问题
    let problem = wrap(&spec.problem, 22.0, 300.0);
    let (w, h) = (text_width(&problem, 22.0) + 40.0, problem.lines().count() as f64 * 27.5 + 32.0);
    c.rect(spine_end, -h / 2.0, w, h, INK, "#e7f5ff");
    c.text(spine_end + 20.0, -h / 2.0 + 16.0, &problem, 22.0, INK);

    json!({
        "type": "excalidraw",
        "version": 2,
        "source": "https://github.com/tancheng33/tingo",
        "elements": c.elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": null},
        "files": {},
    })
}

/// Obsidian Excalidraw 插件的 .excalidraw.md 格式：文字元素列在正文里便于搜索，画面数据放在注释里。
pub fn excalidraw_md(drawing: &Value) -> String {
    let texts: Vec<String> = drawing["elements"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|e| e["type"] == "text")
        .map(|e| format!("{} ^{}\n", e["text"].as_str().unwrap_or(""), e["id"].as_str().unwrap_or("")))
        .collect();
    format!(
        "---\n\nexcalidraw-plugin: parsed\ntags: [excalidraw]\n\n---\n==⚠  Switch to EXCALIDRAW VIEW in the MORE OPTIONS menu of this document. ⚠==\n\n\n# Excalidraw Data\n\n## Text Elements\n{}\n%%\n## Drawing\n```json\n{}\n```\n%%",
        texts.join("\n"),
        serde_json::to_string_pretty(drawing).unwrap()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn turns() -> Vec<Turn> {
        vec![
            Turn { speaker: "李经理".into(), start_ms: 0, end_ms: 8000, text: "大家好，今天讨论发布。".into() },
            Turn { speaker: "小张".into(), start_ms: 9000, end_ms: 18000, text: "支付模块还有两个问题没修，十五号才能提测。".into() },
            Turn { speaker: "李经理".into(), start_ms: 48000, end_ms: 52000, text: "行，散会。".into() },
        ]
    }

    #[test]
    fn grounding_matches_frontend_rules() {
        let b = |refs: &[&str]| Branch { title: "t".into(), detail: String::new(), refs: refs.iter().map(|s| s.to_string()).collect() };
        let t = turns();
        let g = ground(&b(&["00:09", "[00:12]", "00:09"]), &t, 52000);
        assert_eq!((g.quotes.len(), g.level), (1, Level::Ok)); // 同一段只算一次
        assert_eq!(ground(&b(&["00:48"]), &t, 52000).level, Level::Weak);
        assert_eq!(ground(&b(&["05:12"]), &t, 52000).level, Level::None); // 超出会议时长
        assert_eq!(ground(&b(&[]), &t, 52000).level, Level::None);
        assert_eq!(ground(&b(&["1:00:00"]), &t, 52000).level, Level::None);
    }

    #[test]
    fn excalidraw_has_spine_head_and_branches() {
        let md = "## 原因分析\n```fishbone\n{\"problem\": \"为什么延期？\", \"branches\": [{\"title\": \"支付有问题\", \"refs\": [\"00:09\"]}, {\"title\": \"猜的\", \"refs\": []}, {\"title\": \"\"}]}\n```\n";
        let bl = blocks(md);
        let spec = bl[0].1.clone().unwrap();
        assert_eq!(spec.branches.len(), 2); // 空标题的分支丢掉
        let g: Vec<_> = spec.branches.iter().map(|b| ground(b, &turns(), 52000)).collect();
        let d = excalidraw(&spec, &g, Lang::Zh);
        let els = d["elements"].as_array().unwrap();
        let texts: Vec<&str> = els.iter().filter_map(|e| e["text"].as_str()).collect();
        assert!(texts.contains(&"为什么延期？"));
        assert!(texts.contains(&"无原文依据"));
        assert!(texts.iter().any(|t| t.starts_with("00:09 小张：")));
        // 无依据的分支画红色
        let red = els.iter().filter(|e| e["strokeColor"] == "#e03131").count();
        assert!(red >= 3);
        let ids: std::collections::HashSet<_> = els.iter().map(|e| e["id"].as_str().unwrap()).collect();
        assert_eq!(ids.len(), els.len());
        let md = excalidraw_md(&d);
        assert!(md.contains("excalidraw-plugin: parsed") && md.contains("为什么延期？ ^jy"));
    }

    #[test]
    fn english_labels_and_drawing() {
        let b = |refs: &[&str]| Branch { title: "t".into(), detail: String::new(), refs: refs.iter().map(|s| s.to_string()).collect() };
        let t = turns();
        assert_eq!(ground(&b(&["00:09"]), &t, 52000).label(Lang::En), "1 quote(s) · 21 chars");
        assert_eq!(ground(&b(&["00:48"]), &t, 52000).label(Lang::En), "Thin evidence · 1 quote(s) · 5 chars");
        assert_eq!(ground(&b(&[]), &t, 52000).label(Lang::En), "No evidence");
        assert_eq!(ground(&b(&["00:09"]), &t, 52000).label(Lang::Zh), "引用 1 段 · 21 字");
        assert_eq!(ground(&b(&["00:48"]), &t, 52000).label(Lang::Zh), "依据很少 · 引用 1 段 · 5 字");
        let spec = parse("{\"problem\": \"Why the delay?\", \"branches\": [{\"title\": \"Payment bugs\", \"refs\": [\"00:09\"]}, {\"title\": \"Guess\", \"refs\": []}]}").unwrap();
        let g: Vec<_> = spec.branches.iter().map(|b| ground(b, &t, 52000)).collect();
        let d = excalidraw(&spec, &g, Lang::En);
        let texts: Vec<&str> = d["elements"].as_array().unwrap().iter().filter_map(|e| e["text"].as_str()).collect();
        assert!(texts.contains(&"No evidence"));
        assert!(texts.iter().any(|t| t.starts_with("00:09 小张: ")));
    }

    #[test]
    fn wrap_keeps_english_words_whole() {
        let out = wrap("the payment module still has two open bugs", 10.0, 100.0);
        assert!(out.lines().all(|l| !l.starts_with(' ') && !l.ends_with(' ')));
        assert_eq!(out.replace('\n', " "), "the payment module still has two open bugs");
        assert!(out.lines().all(|l| text_width(l, 10.0) <= 100.0));
        // 中文照旧逐字折
        assert_eq!(wrap("一二三四五", 10.0, 30.0), "一二三\n四五");
    }
}
