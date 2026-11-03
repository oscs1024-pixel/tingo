//! 配置：~/.config/tingo/config.toml，和 Python 版共用同一个文件、同一种格式。

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

/// 存进 Obsidian：库的路径、库里的文件夹、生成纪要后是否自动存、是否附带转写全文
#[derive(Clone, Debug, PartialEq)]
pub struct ObsidianConfig {
    pub vault: String,
    pub folder: String,
    pub auto: bool,
    pub transcript: bool,
}

impl Default for ObsidianConfig {
    fn default() -> Self {
        Self { vault: String::new(), folder: "会议纪要".into(), auto: true, transcript: true }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Config {
    pub llm: LlmConfig,
    pub hotwords: Vec<String>,
    pub webhook: String,
    pub webhook_secret: String,
    pub obsidian: ObsidianConfig,
    /// [ui] language：auto | zh | en，界面语言（auto 由前端按系统语言决定）
    pub ui_language: String,
    /// [minutes] language：auto | zh | en，纪要语言；auto = 跟会议转写的语言
    pub minutes_language: String,
    /// [asr] language：zh | en | auto，会议语言（SenseVoice / Fun-ASR-Nano 用；Paraformer 只认中文）
    pub asr_language: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            llm: LlmConfig::default(),
            hotwords: Vec::new(),
            webhook: String::new(),
            webhook_secret: String::new(),
            obsidian: ObsidianConfig::default(),
            ui_language: "auto".into(),
            minutes_language: "auto".into(),
            asr_language: "zh".into(),
        }
    }
}

/// 三个语言选项都只认 auto / zh / en
pub const LANGUAGES: [&str; 3] = ["auto", "zh", "en"];

fn language(v: Option<String>, default: &str) -> String {
    v.map(|s| s.trim().to_ascii_lowercase())
        .filter(|s| LANGUAGES.contains(&s.as_str()))
        .unwrap_or_else(|| default.into())
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
struct RawObsidian {
    vault: Option<String>,
    folder: Option<String>,
    auto: Option<bool>,
    transcript: Option<bool>,
}

#[derive(Deserialize, Default)]
struct RawLang {
    language: Option<String>,
}

#[derive(Deserialize, Default)]
struct Raw {
    hotwords: Option<Vec<String>>,
    #[serde(default)]
    llm: RawLlm,
    #[serde(default)]
    push: RawPush,
    #[serde(default)]
    obsidian: RawObsidian,
    #[serde(default)]
    ui: RawLang,
    #[serde(default)]
    minutes: RawLang,
    #[serde(default)]
    asr: RawLang,
}

/// 读环境变量；改名前的 JIYAO_ 前缀照样认
pub fn env_var(name: &str) -> Option<String> {
    [format!("TINGO_{name}"), format!("JIYAO_{name}")]
        .iter()
        .find_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()))
}

/// 新位置不存在而改名前（jiyao）的位置存在时，沿用旧位置，老用户的配置和会议不用搬
pub fn with_legacy(new: PathBuf, old: PathBuf) -> PathBuf {
    if !new.exists() && old.exists() {
        old
    } else {
        new
    }
}

pub fn default_path() -> PathBuf {
    if let Some(p) = env_var("CONFIG") {
        return PathBuf::from(p);
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default();
    with_legacy(home.join(".config/tingo/config.toml"), home.join(".config/jiyao/config.toml"))
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
        obsidian: {
            let d = ObsidianConfig::default();
            ObsidianConfig {
                vault: raw.obsidian.vault.unwrap_or(d.vault),
                folder: raw.obsidian.folder.unwrap_or(d.folder),
                auto: raw.obsidian.auto.unwrap_or(d.auto),
                transcript: raw.obsidian.transcript.unwrap_or(d.transcript),
            }
        },
        ui_language: language(raw.ui.language, "auto"),
        minutes_language: language(raw.minutes.language, "auto"),
        asr_language: language(raw.asr.language, "zh"),
    };
    let env = |k: &str| match k.strip_prefix("TINGO_") {
        Some(name) => env_var(name),
        None => std::env::var(k).ok().filter(|v| !v.is_empty()),
    };
    if let Some(v) = env("TINGO_LLM_BASE_URL") {
        cfg.llm.base_url = v;
    }
    if let Some(v) = env("TINGO_LLM_MODEL") {
        cfg.llm.model = v;
    }
    if let Some(v) = env("TINGO_LLM_API_KEY") {
        cfg.llm.api_key = v;
    } else if cfg.llm.api_key.is_empty() {
        cfg.llm.api_key = env("DEEPSEEK_API_KEY").unwrap_or_default();
    }
    if let Some(v) = env("TINGO_WEBHOOK") {
        cfg.webhook = v;
    }
    if let Some(v) = env("TINGO_WEBHOOK_SECRET") {
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
        "# tingo 配置，由图形界面写入；也可以直接编辑。\n\n# 专有名词、人名、项目代号，提高识别准确率\nhotwords = [{}]\n\n[llm]\nbase_url = {}\napi_key = {}\nmodel = {}\n\n[push]\nwebhook = {}\nsecret = {}\n\n[obsidian]\nvault = {}\nfolder = {}\nauto = {}\ntranscript = {}\n\n[ui]\nlanguage = {}\n\n[minutes]\nlanguage = {}\n\n[asr]\nlanguage = {}\n",
        words.join(", "),
        quote(&cfg.llm.base_url),
        quote(&cfg.llm.api_key),
        quote(&cfg.llm.model),
        quote(&cfg.webhook),
        quote(&cfg.webhook_secret),
        quote(&cfg.obsidian.vault),
        quote(&cfg.obsidian.folder),
        cfg.obsidian.auto,
        cfg.obsidian.transcript,
        quote(&cfg.ui_language),
        quote(&cfg.minutes_language),
        quote(&cfg.asr_language),
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
        let d = std::env::temp_dir().join(format!("tingo-cfg-{}-{name}", std::process::id()));
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
            obsidian: ObsidianConfig { vault: "/Users/a/笔记".into(), folder: "会议".into(), auto: false, transcript: true },
            ..Default::default()
        };
        save(&p, &cfg).unwrap();
        assert_eq!(load(&p).llm, cfg.llm);
        assert_eq!(load(&p).obsidian, cfg.obsidian);
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
        let c = load(Path::new("/nonexistent/tingo.toml"));
        assert_eq!(c.llm.base_url, "https://api.deepseek.com");
        assert_eq!((c.ui_language.as_str(), c.minutes_language.as_str(), c.asr_language.as_str()), ("auto", "auto", "zh"));
    }

    #[test]
    fn language_sections_roundtrip() {
        let p = tmp("lang");
        let cfg = Config { ui_language: "en".into(), minutes_language: "zh".into(), asr_language: "auto".into(), ..Default::default() };
        save(&p, &cfg).unwrap();
        let text = std::fs::read_to_string(&p).unwrap();
        // 写在 [obsidian] 之后，顺序 [ui]、[minutes]、[asr]
        let pos = |k: &str| text.find(k).unwrap();
        assert!(pos("[obsidian]") < pos("[ui]") && pos("[ui]") < pos("[minutes]") && pos("[minutes]") < pos("[asr]"));
        assert!(text.ends_with("transcript = true\n\n[ui]\nlanguage = \"en\"\n\n[minutes]\nlanguage = \"zh\"\n\n[asr]\nlanguage = \"auto\"\n"));
        let c = load(&p);
        assert_eq!((c.ui_language.as_str(), c.minutes_language.as_str(), c.asr_language.as_str()), ("en", "zh", "auto"));
        // 老配置没有这三节、或者填了不认识的值：用默认
        std::fs::write(&p, "[llm]\nmodel = \"m\"\n\n[asr]\nlanguage = \"fr\"\n\n[minutes]\nlanguage = \"EN\"\n").unwrap();
        let c = load(&p);
        assert_eq!((c.ui_language.as_str(), c.minutes_language.as_str(), c.asr_language.as_str()), ("auto", "en", "zh"));
    }
}
