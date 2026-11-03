//! 把纪要推送到飞书 / 钉钉 / 企业微信群机器人。Python 版 push.py 的移植。
//! 卡片标题、鱼骨图、截断提示跟纪要的语言走；报错跟界面语言走。

use base64::Engine as _;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::i18n::{doc_lang, tr, Lang};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Platform {
    Feishu,
    Dingtalk,
    Wecom,
}

impl Platform {
    pub fn name(self) -> &'static str {
        match self {
            Platform::Feishu => tr("飞书", "Feishu"),
            Platform::Dingtalk => tr("钉钉", "DingTalk"),
            Platform::Wecom => tr("企业微信", "WeCom"),
        }
    }
}

pub fn detect(webhook: &str) -> Result<Platform, String> {
    let host = reqwest::Url::parse(webhook)
        .ok()
        .and_then(|u| u.host_str().map(String::from))
        .unwrap_or_default();
    if host.contains("feishu") || host.contains("larksuite") {
        Ok(Platform::Feishu)
    } else if host.contains("dingtalk") {
        Ok(Platform::Dingtalk)
    } else if host.contains("weixin.qq.com") {
        Ok(Platform::Wecom)
    } else {
        Err(tr(
            &format!("认不出这个 webhook 属于哪个平台：{host}（支持飞书、钉钉、企业微信）"),
            &format!("Can't tell which platform this webhook belongs to: {host} (Feishu, DingTalk and WeCom are supported)"),
        )
        .into())
    }
}

/// 钉钉、企业微信的 markdown 不支持表格，把表格转成列表。
pub fn tables_to_lists(md: &str) -> String {
    let lang = doc_lang(md);
    let (colon, open, close, comma) = match lang {
        Lang::Zh => ("：", "（", "）", "，"),
        Lang::En => (": ", " (", ")", ", "),
    };
    let mut out = Vec::new();
    let mut header: Option<Vec<String>> = None;
    for line in md.lines() {
        if line.trim_start().starts_with('|') {
            let cells: Vec<String> = line
                .trim()
                .trim_matches('|')
                .split('|')
                .map(|c| c.trim().to_string())
                .collect();
            match &header {
                None => header = Some(cells),
                Some(_)
                    if cells
                        .iter()
                        .all(|c| !c.is_empty() && c.chars().all(|ch| ch == '-' || ch == ':')) => {}
                Some(h) => {
                    let extra: Vec<String> = h
                        .iter()
                        .skip(1)
                        .zip(cells.iter().skip(1))
                        .filter(|(_, c)| !c.is_empty())
                        .map(|(h, c)| format!("{h}{colon}{c}"))
                        .collect();
                    let first = cells.first().cloned().unwrap_or_default();
                    out.push(if extra.is_empty() {
                        format!("- {first}")
                    } else {
                        format!("- {first}{open}{}{close}", extra.join(comma))
                    });
                }
            }
        } else {
            header = None;
            out.push(line.to_string());
        }
    }
    out.join("\n")
}

/// 纪要里的鱼骨图是 ```fishbone 代码块（JSON），群消息里换成可读的列表。
pub fn fishbone_to_text(md: &str) -> String {
    let en = doc_lang(md).is_en();
    static FENCE: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| regex::Regex::new(r"(?s)```fishbone\s*\n(.*?)\n```").unwrap());
    FENCE
        .replace_all(md, |c: &regex::Captures| {
            let Ok(fb) = serde_json::from_str::<serde_json::Value>(&c[1]) else { return String::new() };
            let problem = if en { "Root Cause Analysis" } else { "原因分析" };
            let mut lines = vec![format!("**{}**", fb["problem"].as_str().unwrap_or(problem))];
            for b in fb["branches"].as_array().into_iter().flatten() {
                let refs: Vec<&str> = b["refs"].as_array().into_iter().flatten().filter_map(|r| r.as_str()).collect();
                let sep = if en { ": " } else { "：" };
                let detail = b["detail"].as_str().filter(|d| !d.is_empty()).map(|d| format!("{sep}{d}")).unwrap_or_default();
                let src = match (refs.is_empty(), en) {
                    (true, false) => "（无原文依据）".to_string(),
                    (true, true) => " (no evidence)".to_string(),
                    (false, false) => format!("（{}）", refs.join("、")),
                    (false, true) => format!(" ({})", refs.join(", ")),
                };
                lines.push(format!("- {}{detail}{src}", b["title"].as_str().unwrap_or("")));
            }
            lines.join("\n")
        })
        .into_owned()
}

fn truncate(text: &str, max_bytes: usize, lang: Lang) -> String {
    if text.len() <= max_bytes {
        return text.to_string();
    }
    let mut end = max_bytes - 40;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n\n{}", &text[..end], lang.pick("……（内容过长已截断）", "…(truncated)"))
}

fn title(md: &str) -> String {
    md.lines()
        .find_map(|l| l.strip_prefix("# ").map(|t| t.trim().to_string()))
        .unwrap_or_else(|| doc_lang(md).pick("会议纪要", "Meeting Minutes").into())
}

fn hmac_b64(key: &[u8], msg: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("HMAC 接受任意长度的 key");
    mac.update(msg);
    base64::engine::general_purpose::STANDARD.encode(mac.finalize().into_bytes())
}

/// 返回 (url, json)。拆出来是为了不发请求也能测。`now` 是秒。
pub fn build_request(
    webhook: &str,
    minutes: &str,
    secret: &str,
    now: f64,
) -> Result<(String, serde_json::Value), String> {
    let platform = detect(webhook)?;
    let lang = doc_lang(minutes);
    let t = title(minutes);
    // 术语表只给搜索用，群里不发
    let minutes = &fishbone_to_text(&crate::terms::strip(minutes));
    if platform == Platform::Feishu {
        let mut body = serde_json::json!({
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": t}, "template": "blue"},
                "body": {"elements": [{"tag": "markdown", "content": truncate(minutes, 25000, lang)}]},
            },
        });
        if !secret.is_empty() {
            let ts = (now as u64).to_string();
            body["timestamp"] = ts.clone().into();
            body["sign"] = hmac_b64(format!("{ts}\n{secret}").as_bytes(), b"").into();
        }
        return Ok((webhook.to_string(), body));
    }
    let text = tables_to_lists(minutes);
    if platform == Platform::Dingtalk {
        let mut url = webhook.to_string();
        if !secret.is_empty() {
            let ts = ((now * 1000.0) as u64).to_string();
            let sign = hmac_b64(secret.as_bytes(), format!("{ts}\n{secret}").as_bytes());
            let sign = sign
                .replace('+', "%2B")
                .replace('/', "%2F")
                .replace('=', "%3D");
            url = format!(
                "{url}{}timestamp={ts}&sign={sign}",
                if url.contains('?') { '&' } else { '?' }
            );
        }
        return Ok((
            url,
            serde_json::json!({"msgtype": "markdown", "markdown": {"title": t, "text": truncate(&text, 18000, lang)}}),
        ));
    }
    Ok((
        webhook.to_string(),
        serde_json::json!({"msgtype": "markdown", "markdown": {"content": truncate(&text, 4000, lang)}}),
    ))
}

pub fn send(webhook: &str, minutes: &str, secret: &str) -> Result<Platform, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let (url, body) = build_request(webhook, minutes, secret, now)?;
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .post(url)
        .json(&body)
        .send()
        .map_err(|e| tr(&format!("推送失败：{e}"), &format!("Push failed: {e}")).to_string())?;
    let status = resp.status();
    let data: serde_json::Value = resp.json().unwrap_or_default();
    let code = data
        .get("code")
        .or_else(|| data.get("errcode"))
        .and_then(|c| c.as_i64())
        .unwrap_or(0);
    if !status.is_success() || code != 0 {
        return Err(tr(&format!("推送失败：{status} {data}"), &format!("Push failed: {status} {data}")).into());
    }
    detect(webhook)
}

#[cfg(test)]
mod tests {
    use super::*;

    const MINUTES: &str = "# 周会\n\n## 待办\n| 事项 | 负责人 | 截止时间 | 出处 |\n|---|---|---|---|\n| 修复支付模块 | 小王 | 11月15日 | [00:29] |\n| 出迁移方案 | 小张 |  | [00:40] |\n\n## 风险\n- 无\n";

    #[test]
    fn detect_platforms() {
        assert_eq!(
            detect("https://open.feishu.cn/open-apis/bot/v2/hook/abc").unwrap(),
            Platform::Feishu
        );
        assert_eq!(
            detect("https://oapi.dingtalk.com/robot/send?access_token=x").unwrap(),
            Platform::Dingtalk
        );
        assert_eq!(
            detect("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x").unwrap(),
            Platform::Wecom
        );
        assert!(detect("https://example.com/hook").is_err());
    }

    #[test]
    fn tables_become_lists_like_python() {
        let out = tables_to_lists(MINUTES);
        assert!(!out.contains('|'));
        assert!(out.contains("- 修复支付模块（负责人：小王，截止时间：11月15日，出处：[00:29]）"));
        assert!(out.contains("- 出迁移方案（负责人：小张，出处：[00:40]）"));
        assert!(out.contains("## 风险\n- 无"));
    }

    #[test]
    fn fishbone_becomes_list() {
        let md = "## 原因分析\n```fishbone\n{\"problem\": \"为什么慢？\", \"branches\": [{\"title\": \"页面大\", \"detail\": \"6 秒\", \"refs\": [\"01:06\"]}, {\"title\": \"猜的\", \"refs\": []}]}\n```\n";
        let out = fishbone_to_text(md);
        assert!(out.contains("**为什么慢？**\n- 页面大：6 秒（01:06）\n- 猜的（无原文依据）"));
        assert!(!out.contains("```"));
    }

    #[test]
    fn terms_block_not_pushed() {
        let md = format!("{MINUTES}\n```terms\n{{\"terms\": [{{\"term\": \"支付模块\", \"variants\": []}}]}}\n```\n");
        for hook in ["https://open.feishu.cn/open-apis/bot/v2/hook/abc", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k"] {
            let (_, b) = build_request(hook, &md, "", 0.0).unwrap();
            assert!(!b.to_string().contains("terms"));
        }
    }

    #[test]
    fn signatures_match_python() {
        // 与 Python 版测试里相同输入算出的签名
        let (_, b) = build_request(
            "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
            MINUTES,
            "s3",
            1700000000.0,
        )
        .unwrap();
        assert_eq!(b["timestamp"], "1700000000");
        assert_eq!(b["sign"], "eadg3K8D5pJHina1SanWPdQUcpqxvN+0kGvFxsngPzI=");
        let (url, b) = build_request(
            "https://oapi.dingtalk.com/robot/send?access_token=x",
            MINUTES,
            "s3",
            1700000000.5,
        )
        .unwrap();
        assert!(url.ends_with(
            "&timestamp=1700000000500&sign=FJom%2B1obor0kJAo1zYkeYVqwqUkyXLctgpZc5cW8i9E%3D"
        ));
        assert_eq!(b["markdown"]["title"], "周会");
        let (_, b) = build_request(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
            &format!("# 长\n{}", "字".repeat(3000)),
            "",
            0.0,
        )
        .unwrap();
        let content = b["markdown"]["content"].as_str().unwrap();
        assert!(content.len() <= 4096 && content.ends_with("（内容过长已截断）"));
    }

    #[test]
    fn english_minutes_push_in_english() {
        let md = "# Release review\n\n## Summary\nShip next week.\n\n## Action Items\n| Item | Owner | Due | Source |\n|---|---|---|---|\n| Fix payment | Wang | Nov 15 | [00:29] |\n\n## Root Cause Analysis\n```fishbone\n{\"problem\": \"Why slow?\", \"branches\": [{\"title\": \"Big page\", \"detail\": \"6 s\", \"refs\": [\"01:06\", \"01:10\"]}, {\"title\": \"Guess\", \"refs\": []}]}\n```\n";
        let out = fishbone_to_text(md);
        assert!(out.contains("**Why slow?**\n- Big page: 6 s (01:06, 01:10)\n- Guess (no evidence)"));
        assert!(tables_to_lists(md).contains("- Fix payment (Owner: Wang, Due: Nov 15, Source: [00:29])"));
        assert_eq!(title("## Summary\nx"), "Meeting Minutes");
        assert_eq!(title("## 一句话总结\nx"), "会议纪要");
        let (_, b) = build_request(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
            &format!("# Long\n\n## Summary\n{}", "word ".repeat(2000)),
            "",
            0.0,
        )
        .unwrap();
        let content = b["markdown"]["content"].as_str().unwrap();
        assert!(content.len() <= 4096 && content.ends_with("\n\n…(truncated)"));
    }
}
