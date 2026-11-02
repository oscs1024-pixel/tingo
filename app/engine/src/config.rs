//! 配置：~/.config/jiyao/config.toml，和 Python 版共用同一个文件、同一种格式。

use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq)]
pub struct LlmConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
}

impl Default for LlmConfig {
    fn default() -> Self {
        Self {
            base_url: "https://api.deepseek.com".into(),
            api_key: String::new(),
            model: "deepseek-chat".into(),
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Config {
    pub llm: LlmConfig,
    pub hotwords: Vec<String>,
    pub webhook: String,
    pub webhook_secret: String,
}

#[derive(Deserialize, Default)]
struct RawLlm {
    base_url: Option<String>,
    api_key: Option<String>,
    model: Option<String>,
    hotwords: Option<Vec<String>>, // 早期模板误把 hotwords 写在 [llm] 下
}

#[derive(Deserialize, Default)]
struct RawPush {
    webhook: Option<String>,
    secret: Option<String>,
}

#[derive(Deserialize, Default)]
struct Raw {
    hotwords: Option<Vec<String>>,
    #[serde(default)]
    llm: RawLlm,
    #[serde(default)]
    push: RawPush,
}

pub fn default_path() -> PathBuf {
    if let Some(p) = std::env::var_os("JIYAO_CONFIG") {
        return PathBuf::from(p);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".config/jiyao/config.toml")
}

/// 读配置；环境变量优先（与 Python 版相同的几个）。文件不存在或格式不对时用默认值。
pub fn load(path: &Path) -> Config {
    let raw: Raw = std::fs::read_to_string(path)
        .ok()
        .and_then(|s| toml::from_str(&s).ok())
        .unwrap_or_default();
    let d = LlmConfig::default();
    let mut cfg = Config {
        llm: LlmConfig {
            base_url: raw.llm.base_url.unwrap_or(d.base_url),
            api_key: raw.llm.api_key.unwrap_or_default(),
            model: raw.llm.model.unwrap_or(d.model),
        },
        hotwords: raw.hotwords.or(raw.llm.hotwords).unwrap_or_default(),
        webhook: raw.push.webhook.unwrap_or_default(),
        webhook_secret: raw.push.secret.unwrap_or_default(),
    };
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    if let Some(v) = env("JIYAO_LLM_BASE_URL") {
        cfg.llm.base_url = v;
    }
    if let Some(v) = env("JIYAO_LLM_MODEL") {
        cfg.llm.model = v;
    }
    if let Some(v) = env("JIYAO_LLM_API_KEY") {
        cfg.llm.api_key = v;
    } else if cfg.llm.api_key.is_empty() {
        cfg.llm.api_key = env("DEEPSEEK_API_KEY").unwrap_or_default();
    }
    if let Some(v) = env("JIYAO_WEBHOOK") {
        cfg.webhook = v;
    }
    if let Some(v) = env("JIYAO_WEBHOOK_SECRET") {
        cfg.webhook_secret = v;
    }
    cfg
}

fn quote(s: &str) -> String {
    // TOML 基本字符串和 JSON 字符串的转义规则兼容
    serde_json::to_string(s).unwrap()
}

/// 写回配置（设置页用）。hotwords 必须写在所有表之前，否则会被当成 [llm] 的配置。
pub fn save(path: &Path, cfg: &Config) -> std::io::Result<()> {
    let words: Vec<String> = cfg.hotwords.iter().map(|w| quote(w)).collect();
    let text = format!(
        "# jiyao 配置，由图形界面写入；也可以直接编辑。\n\n# 专有名词、人名、项目代号，提高识别准确率\nhotwords = [{}]\n\n[llm]\nbase_url = {}\napi_key = {}\nmodel = {}\n\n[push]\nwebhook = {}\nsecret = {}\n",
        words.join(", "),
        quote(&cfg.llm.base_url),
        quote(&cfg.llm.api_key),
        quote(&cfg.llm.model),
        quote(&cfg.webhook),
        quote(&cfg.webhook_secret),
    );
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    std::fs::write(path, text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
        // 里面有 API key
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("jiyao-cfg-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d.join("config.toml")
    }

    #[test]
    fn roundtrip_and_python_compat() {
        let p = tmp("rt");
        let cfg = Config {
            llm: LlmConfig {
                base_url: "http://127.0.0.1:3425/v1".into(),
                api_key: "k\"q".into(),
                model: "deepseek/deepseek-v4-flash".into(),
            },
            hotwords: vec!["星河".into(), "Kafka".into()],
            webhook: "".into(),
            webhook_secret: "".into(),
        };
        save(&p, &cfg).unwrap();
        assert_eq!(load(&p).llm, cfg.llm);
        assert_eq!(load(&p).hotwords, cfg.hotwords);
        // Python 版写出的文件
        std::fs::write(&p, "hotwords = [\"青鸾\"]\n\n[llm]\nbase_url = \"https://ai.example/v1\"\napi_key = \"sk\"\nmodel = \"m\"\n\n[push]\nwebhook = \"\"\nsecret = \"\"\n").unwrap();
        let c = load(&p);
        assert_eq!(
            (c.llm.model.as_str(), c.hotwords[0].as_str()),
            ("m", "青鸾")
        );
        // 早期模板把 hotwords 写在 [llm] 下
        std::fs::write(&p, "[llm]\nmodel = \"m\"\nhotwords = [\"旧版\"]\n").unwrap();
        assert_eq!(load(&p).hotwords, vec!["旧版".to_string()]);
    }

    #[test]
    fn missing_file_gives_defaults() {
        let c = load(Path::new("/nonexistent/jiyao.toml"));
        assert_eq!(c.llm.base_url, "https://api.deepseek.com");
    }
}
