//! 把纪要推送到飞书 / 钉钉 / 企业微信群机器人。Python 版 push.py 的移植。

use base64::Engine as _;
use hmac::{Hmac, KeyInit, Mac};
use sha2::Sha256;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Platform {
    Feishu,
    Dingtalk,
    Wecom,
}

impl Platform {
    pub fn name(self) -> &'static str {
        match self {
            Platform::Feishu => "飞书",
            Platform::Dingtalk => "钉钉",
            Platform::Wecom => "企业微信",
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
        Err(format!(
            "认不出这个 webhook 属于哪个平台：{host}（支持飞书、钉钉、企业微信）"
        ))
    }
}

/// 钉钉、企业微信的 markdown 不支持表格，把表格转成列表。
pub fn tables_to_lists(md: &str) -> String {
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
                        .map(|(h, c)| format!("{h}：{c}"))
                        .collect();
                    let first = cells.first().cloned().unwrap_or_default();
                    out.push(if extra.is_empty() {
                        format!("- {first}")
                    } else {
                        format!("- {first}（{}）", extra.join("，"))
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

fn truncate(text: &str, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text.to_string();
    }
    let mut end = max_bytes - 40;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n\n……（内容过长已截断）", &text[..end])
}

fn title(md: &str) -> String {
    md.lines()
        .find_map(|l| l.strip_prefix("# ").map(|t| t.trim().to_string()))
        .unwrap_or_else(|| "会议纪要".into())
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
    let t = title(minutes);
    if platform == Platform::Feishu {
        let mut body = serde_json::json!({
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": t}, "template": "blue"},
                "body": {"elements": [{"tag": "markdown", "content": truncate(minutes, 25000)}]},
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
            serde_json::json!({"msgtype": "markdown", "markdown": {"title": t, "text": truncate(&text, 18000)}}),
        ));
    }
    Ok((
        webhook.to_string(),
        serde_json::json!({"msgtype": "markdown", "markdown": {"content": truncate(&text, 4000)}}),
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
        .map_err(|e| format!("推送失败：{e}"))?;
    let status = resp.status();
    let data: serde_json::Value = resp.json().unwrap_or_default();
    let code = data
        .get("code")
        .or_else(|| data.get("errcode"))
        .and_then(|c| c.as_i64())
        .unwrap_or(0);
    if !status.is_success() || code != 0 {
        return Err(format!("推送失败：{status} {data}"));
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
}
