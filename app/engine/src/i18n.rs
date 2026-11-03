//! 中英双语。两种语言来源：
//! - 界面语言：前端每个请求带 `X-Tingo-Lang: zh|en`，记到全局（单用户本地应用，取最近一次的值，默认中文）。
//!   错误提示、任务进度、提示信息都按它给。
//! - 文档语言：鱼骨图、推送、Obsidian 里的文字跟纪要本身走，纪要里有 `## Summary` 就是英文。

use std::sync::atomic::{AtomicBool, Ordering};

use axum::extract::Request;
use axum::middleware::Next;
use axum::response::Response;

pub const HEADER: &str = "x-tingo-lang";

static UI_EN: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Lang {
    Zh,
    En,
}

impl Lang {
    /// "zh" / "zh-CN" / "en-US" 之类；认不出时返回 None
    pub fn parse(s: &str) -> Option<Lang> {
        let s = s.trim().to_ascii_lowercase();
        if s.starts_with("en") {
            Some(Lang::En)
        } else if s.starts_with("zh") {
            Some(Lang::Zh)
        } else {
            None
        }
    }

    pub fn is_en(self) -> bool {
        self == Lang::En
    }

    pub fn pick<'a>(self, zh: &'a str, en: &'a str) -> &'a str {
        if self.is_en() {
            en
        } else {
            zh
        }
    }
}

/// 当前界面语言
pub fn ui() -> Lang {
    if UI_EN.load(Ordering::Relaxed) {
        Lang::En
    } else {
        Lang::Zh
    }
}

pub fn set_ui(lang: Lang) {
    UI_EN.store(lang.is_en(), Ordering::Relaxed);
}

pub fn is_en() -> bool {
    ui().is_en()
}

/// 按界面语言二选一。两个都是字面量时返回 &'static str；带参数的各 format! 一份再传进来。
pub fn tr<'a>(zh: &'a str, en: &'a str) -> &'a str {
    ui().pick(zh, en)
}

/// 纪要本身的语言：英文模板的「## Summary」在就是英文
pub fn doc_lang(minutes: &str) -> Lang {
    if minutes.lines().any(|l| l.trim_end() == "## Summary") {
        Lang::En
    } else {
        Lang::Zh
    }
}

fn is_cjk(c: char) -> bool {
    matches!(c, '\u{4e00}'..='\u{9fff}' | '\u{3400}'..='\u{4dbf}' | '\u{f900}'..='\u{faff}' | '\u{20000}'..='\u{2ffff}')
}

fn is_latin(c: char) -> bool {
    c.is_ascii_alphabetic() || (('\u{c0}'..='\u{24f}').contains(&c) && c.is_alphabetic())
}

/// 一段文字是哪种语言：汉字占「汉字 + 拉丁字母」≥ 0.3 算中文（中文会议里夹英文术语很常见）。没有字母时算中文。
pub fn detect(text: &str) -> Lang {
    let (mut cjk, mut latin) = (0usize, 0usize);
    for c in text.chars() {
        if is_cjk(c) {
            cjk += 1;
        } else if is_latin(c) {
            latin += 1;
        }
    }
    if cjk + latin == 0 || cjk as f64 >= 0.3 * (cjk + latin) as f64 {
        Lang::Zh
    } else {
        Lang::En
    }
}

/// 配置里的语言（auto | zh | en）：auto 时看 `text` 是哪种语言
pub fn resolve(setting: &str, text: &str) -> Lang {
    match setting.trim() {
        "zh" => Lang::Zh,
        "en" => Lang::En,
        _ => detect(text),
    }
}

/// 读请求头 X-Tingo-Lang，更新界面语言
pub fn remember(headers: &axum::http::HeaderMap) {
    if let Some(lang) = headers.get(HEADER).and_then(|v| v.to_str().ok()).and_then(Lang::parse) {
        set_ui(lang);
    }
}

/// axum 中间件：每个请求都过一遍 [`remember`]
pub async fn layer(req: Request, next: Next) -> Response {
    remember(req.headers());
    next.run(req).await
}

/// 改全局界面语言、或者断言界面语言文字的测试都要先拿这把锁（测试是并行跑的）
#[cfg(test)]
pub static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;
    use axum::routing::get;
    use axum::Router;
    use tower::ServiceExt;

    #[test]
    fn parse_and_detect() {
        assert_eq!(Lang::parse("en-US"), Some(Lang::En));
        assert_eq!(Lang::parse(" zh "), Some(Lang::Zh));
        assert_eq!(Lang::parse("fr"), None);
        assert_eq!(detect("大家好，今天讨论 Kafka 迁移和 API 网关"), Lang::Zh);
        assert_eq!(detect("Let's review the release plan for next week."), Lang::En);
        assert_eq!(detect("We agreed: 上线 next Monday, then rollback plan"), Lang::En);
        assert_eq!(detect(""), Lang::Zh);
        assert_eq!(resolve("en", "全是中文"), Lang::En);
        assert_eq!(resolve("zh", "all english"), Lang::Zh);
        assert_eq!(resolve("auto", "all english words here"), Lang::En);
        assert_eq!(doc_lang("# T\n\n## Summary\nok"), Lang::En);
        assert_eq!(doc_lang("# T\n\n## 一句话总结\n好"), Lang::Zh);
    }

    #[test]
    fn header_switches_ui_language() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        tokio::runtime::Builder::new_current_thread().build().unwrap().block_on(header_switch_checks());
    }

    async fn header_switch_checks() {
        let app: Router = Router::new()
            .route("/", get(|| async { tr("中文", "English").to_string() }))
            .layer(axum::middleware::from_fn(layer));
        let call = |lang: Option<&'static str>| {
            let app = app.clone();
            async move {
                let mut req = axum::http::Request::builder().uri("/");
                if let Some(l) = lang {
                    req = req.header("X-Tingo-Lang", l);
                }
                let resp = app.oneshot(req.body(axum::body::Body::empty()).unwrap()).await.unwrap();
                let body = axum::body::to_bytes(resp.into_body(), 1024).await.unwrap();
                String::from_utf8(body.to_vec()).unwrap()
            }
        };
        set_ui(Lang::Zh);
        assert_eq!(call(None).await, "中文");
        assert_eq!(call(Some("en")).await, "English");
        assert!(is_en());
        assert_eq!(call(None).await, "English"); // 没带请求头时沿用上一次的
        assert_eq!(call(Some("bogus")).await, "English");
        assert_eq!(call(Some("zh-CN")).await, "中文");
        assert_eq!(ui(), Lang::Zh);
    }
}
