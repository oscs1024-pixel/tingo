//! 本地 HTTP 服务：接口与 Python 版 server.py 完全一致，界面不用改。只监听 127.0.0.1。
//!
//! 同一时间只跑一个任务（下载模型 / 录音 / 转写 / 生成纪要），界面轮询 /api/job 拿进度。
//! 重活（识别、调大模型、下载）都在独立线程里跑，HTTP 处理只读写状态。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{Multipart, Path as UrlPath, Request, State};
use axum::http::{HeaderValue, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tower::ServiceExt;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tower_http::services::ServeFile;

use crate::asr::{Engine, ModelPaths};
use crate::chunked::{transcribe_samples, LiveTranscriber};
use crate::config::{self, Config};
use crate::meetings::{self, AUDIO, MINUTES, TRANSCRIPT_JSON};
use crate::record::{self, Recorder};
use crate::summarize::{self, Hooks};
use crate::transcript::{fmt_ts, Transcript};
use crate::{audio, models, push};

pub struct Options {
    pub root: PathBuf,
    pub config_path: PathBuf,
    pub models_dir: PathBuf,
    /// Swift 录音组件（录音、解码）
    pub recorder_bin: PathBuf,
    pub version: String,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Job {
    pub kind: String,
    pub meeting: String,
    pub state: String,
    pub started_at: f64,
    pub error: String,
    pub minutes: String,
    pub stage: String,
    pub sources: Vec<String>,
}

impl Job {
    fn new(kind: &str, meeting: &str, state: &str) -> Self {
        Self {
            kind: kind.into(),
            meeting: meeting.into(),
            state: state.into(),
            started_at: now(),
            ..Default::default()
        }
    }
    fn busy(&self) -> bool {
        !matches!(self.state.as_str(), "done" | "error")
    }
}

struct Live {
    recorder: Recorder,
    live: Option<LiveTranscriber>,
    hotwords: Vec<String>,
}

pub struct AppState {
    opt: Options,
    engine: OnceLock<Arc<Engine>>,
    engine_lock: Mutex<()>,
    job: Mutex<Option<Job>>,
    recording: Mutex<Option<Live>>,
}

type Shared = Arc<AppState>;

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

struct ApiError(StatusCode, String);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(json!({ "detail": self.1 }))).into_response()
    }
}

type ApiResult<T> = Result<T, ApiError>;

fn bad(msg: impl Into<String>) -> ApiError {
    ApiError(StatusCode::BAD_REQUEST, msg.into())
}

fn not_found() -> ApiError {
    ApiError(StatusCode::NOT_FOUND, "找不到这场会议".into())
}

impl AppState {
    pub fn new(opt: Options) -> Shared {
        Arc::new(Self {
            opt,
            engine: OnceLock::new(),
            engine_lock: Mutex::new(()),
            job: Mutex::new(None),
            recording: Mutex::new(None),
        })
    }

    fn config(&self) -> Config {
        config::load(&self.opt.config_path)
    }

    fn model_paths(&self) -> ModelPaths {
        ModelPaths::new(&self.opt.models_dir)
    }

    /// 识别引擎：第一次用时加载（约 1 秒），之后常驻。
    fn engine(&self) -> Result<Arc<Engine>, String> {
        if let Some(e) = self.engine.get() {
            return Ok(e.clone());
        }
        let _guard = self.engine_lock.lock().unwrap();
        if let Some(e) = self.engine.get() {
            return Ok(e.clone());
        }
        if !self.model_paths().ready() {
            return Err("识别模型还没有下载，先在首页下载（约 330 MB）".into());
        }
        let threads = std::thread::available_parallelism()
            .map(|n| n.get().clamp(2, 4) as i32)
            .unwrap_or(4);
        let e = Arc::new(Engine::load(&self.model_paths(), threads)?);
        let _ = self.engine.set(e.clone());
        Ok(e)
    }

    fn folder(&self, id: &str) -> ApiResult<PathBuf> {
        let root = self.opt.root.canonicalize().map_err(|_| not_found())?;
        let path = root.join(id).canonicalize().map_err(|_| not_found())?;
        if path.parent() != Some(root.as_path()) || !path.is_dir() {
            return Err(not_found());
        }
        Ok(path)
    }

    fn start_job(&self, job: Job) -> ApiResult<()> {
        let mut slot = self.job.lock().unwrap();
        if slot.as_ref().is_some_and(Job::busy) {
            return Err(ApiError(
                StatusCode::CONFLICT,
                "还有任务在进行，请稍等".into(),
            ));
        }
        *slot = Some(job);
        Ok(())
    }

    fn update(&self, f: impl FnOnce(&mut Job)) {
        if let Some(j) = self.job.lock().unwrap().as_mut() {
            f(j);
        }
    }

    fn fail(&self, e: impl Into<String>) {
        let e = e.into();
        eprintln!("任务失败：{e}");
        self.update(|j| {
            j.state = "error".into();
            j.error = e;
        });
    }

    /// 生成纪要：流式写进 job.minutes，写完存到会议文件夹。`extra` 是这场会议额外填的热词。
    fn run_summarize(&self, tr: &Transcript, dir: &Path, extra: &[String]) -> Result<(), String> {
        let cfg = self.config();
        let mut hotwords = cfg.hotwords.clone();
        hotwords.extend(
            extra
                .iter()
                .filter(|w| !hotwords.contains(w))
                .cloned()
                .collect::<Vec<_>>(),
        );
        self.update(|j| j.state = "summarizing".into());
        let text = summarize::summarize(
            tr,
            &cfg.llm,
            meetings::meeting_date(dir),
            &hotwords,
            Hooks {
                on_token: &mut |t| self.update(|j| j.minutes.push_str(t)),
                on_stage: &mut |s| {
                    self.update(|j| {
                        j.stage = s.into();
                        j.minutes.clear();
                    })
                },
            },
        )?;
        std::fs::write(dir.join(MINUTES), &text).map_err(|e| e.to_string())?;
        self.update(|j| j.minutes = text);
        Ok(())
    }

    /// 处理录音文件：解码 → 分段转写 → 存转写 → 生成纪要。
    fn run_process(&self, audio_path: &Path, dir: &Path, extra: &[String]) -> Result<(), String> {
        self.update(|j| j.state = "loading".into());
        let engine = self.engine()?;
        self.update(|j| j.state = "transcribing".into());
        let samples = record::decode(&self.opt.recorder_bin, audio_path)?;
        let tr = transcribe_samples(&engine, &samples, audio_path, |f| {
            self.update(|j| j.stage = format!("{:.0}%", f * 100.0))
        });
        self.update(|j| j.stage.clear());
        if tr.sentences.is_empty() {
            return Err("没有识别出任何语音".into());
        }
        meetings::save_transcript(&tr, dir).map_err(|e| e.to_string())?;
        self.run_summarize(&tr, dir, extra)
    }
}

fn spawn_job(state: &Shared, work: impl FnOnce(&AppState) -> Result<(), String> + Send + 'static) {
    let s = state.clone();
    std::thread::spawn(move || match work(&s) {
        Ok(()) => s.update(|j| j.state = "done".into()),
        Err(e) => s.fail(e),
    });
}

// ---------- 环境与配置 ----------

async fn env(State(s): State<Shared>) -> Json<Value> {
    let cfg = s.config();
    let s2 = s.clone();
    let llm_ready = !cfg.llm.api_key.is_empty()
        || tokio::task::spawn_blocking(move || summarize::is_ollama(&s2.config().llm))
            .await
            .unwrap_or(false);
    Json(json!({
        "version": s.opt.version,
        "root": s.opt.root,
        "ffmpeg": true, // 桌面版不需要 ffmpeg
        "system_audio": system_audio_supported(&s.opt.recorder_bin),
        "model_cached": s.model_paths().ready(),
        "models_downloadable": true, // 桌面版：模型在界面里下载（Python 版首次转写时自动下载，没有这个字段）
        "models_size_mb": models::total_size() / 1_000_000,
        "model_loaded": s.engine.get().is_some(),
        "llm_ready": llm_ready,
        "llm_model": cfg.llm.model,
    }))
}

fn system_audio_supported(bin: &Path) -> bool {
    if !bin.is_file() {
        return false;
    }
    let out = std::process::Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output();
    out.ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .and_then(|v| {
            v.trim()
                .split('.')
                .next()
                .and_then(|m| m.parse::<u32>().ok())
        })
        .is_some_and(|major| major >= 15)
}

fn config_json(s: &AppState) -> Value {
    let c = s.config();
    let key = &c.llm.api_key;
    let hint = if key.chars().count() > 10 {
        let chars: Vec<char> = key.chars().collect();
        format!(
            "{}…{}",
            chars[..3].iter().collect::<String>(),
            chars[chars.len() - 4..].iter().collect::<String>()
        )
    } else if key.is_empty() {
        String::new()
    } else {
        "已设置".into()
    };
    json!({
        "base_url": c.llm.base_url,
        "model": c.llm.model,
        "api_key_set": !key.is_empty(),
        "api_key_hint": hint,
        "hotwords": c.hotwords,
        "webhook": c.webhook,
        "secret_set": !c.webhook_secret.is_empty(),
        "path": s.opt.config_path,
    })
}

async fn get_config(State(s): State<Shared>) -> Json<Value> {
    Json(config_json(&s))
}

#[derive(Deserialize)]
struct ConfigReq {
    base_url: String,
    model: String,
    api_key: Option<String>,
    #[serde(default)]
    hotwords: Vec<String>,
    #[serde(default)]
    webhook: String,
    secret: Option<String>,
}

async fn put_config(State(s): State<Shared>, Json(r): Json<ConfigReq>) -> ApiResult<Json<Value>> {
    let mut c = s.config();
    c.llm.base_url = r.base_url.trim().into();
    c.llm.model = r.model.trim().into();
    if let Some(k) = r.api_key {
        c.llm.api_key = k.trim().into();
    }
    c.hotwords = r
        .hotwords
        .iter()
        .map(|w| w.trim().to_string())
        .filter(|w| !w.is_empty())
        .collect();
    c.webhook = r.webhook.trim().into();
    if let Some(sec) = r.secret {
        c.webhook_secret = sec.trim().into();
    }
    config::save(&s.opt.config_path, &c).map_err(|e| bad(e.to_string()))?;
    Ok(Json(config_json(&s)))
}

#[derive(Deserialize)]
struct LlmReq {
    base_url: String,
    #[serde(default)]
    model: String,
    api_key: Option<String>,
}

fn list_models(base_url: &str, key: &str) -> Option<Vec<String>> {
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .ok()?;
    let r = client
        .get(format!("{}/models", base_url.trim_end_matches('/')))
        .bearer_auth(if key.is_empty() { "none" } else { key })
        .send()
        .ok()?;
    if !r.status().is_success() {
        return None;
    }
    let v: Value = r.json().ok()?;
    let mut ids: Vec<String> = v["data"]
        .as_array()?
        .iter()
        .filter_map(|m| m["id"].as_str().map(String::from))
        .collect();
    ids.sort();
    Some(ids)
}

async fn test_llm(State(s): State<Shared>, Json(r): Json<LlmReq>) -> Json<Value> {
    let key = r.api_key.unwrap_or_else(|| s.config().llm.api_key);
    let (url, model) = (r.base_url, r.model);
    let ids = tokio::task::spawn_blocking(move || list_models(&url, &key))
        .await
        .ok()
        .flatten();
    Json(match ids {
        None => json!({"ok": false, "message": "连不上：接口地址或 API Key 不对"}),
        // Ollama 列出的模型名带 :latest
        Some(ids)
            if !ids.is_empty()
                && !ids.contains(&model)
                && !ids.contains(&format!("{model}:latest")) =>
        {
            let some: Vec<&str> = ids.iter().take(8).map(String::as_str).collect();
            json!({"ok": false, "message": format!("连上了，但没有模型 {model}。可用：{}", some.join("、"))})
        }
        Some(_) => json!({"ok": true, "message": "连接正常"}),
    })
}

async fn llm_models(State(s): State<Shared>, Json(r): Json<LlmReq>) -> Json<Value> {
    let key = r.api_key.unwrap_or_else(|| s.config().llm.api_key);
    let url = r.base_url;
    let ids = tokio::task::spawn_blocking(move || list_models(&url, &key))
        .await
        .ok()
        .flatten();
    Json(json!({"ok": ids.is_some(), "models": ids.unwrap_or_default()}))
}

/// 本机常见的 OpenAI 兼容中转。recommended 是实测过、又快又好的纪要模型。
fn local_providers() -> Vec<Value> {
    vec![
        json!({"id": "magpie", "name": "Magpie", "base_url": "http://127.0.0.1:3425/v1", "api_key": "magpie",
               "recommended": "deepseek/deepseek-v4-flash", "hint": "Magpie 没有在运行：打开 Magpie 应用后再试"}),
        json!({"id": "ccswitch", "name": "CC Switch", "base_url": "http://127.0.0.1:15721/v1", "api_key": "cc-switch",
               "recommended": "", "hint": "CC Switch 的本地代理没有打开：在 CC Switch 设置里开启「本地代理」（默认端口 15721）"}),
        json!({"id": "ollama", "name": "本地 Ollama", "base_url": "http://localhost:11434/v1", "api_key": "",
               "recommended": "", "hint": "Ollama 没有在运行，或者还没有下载模型"}),
    ]
}

async fn llm_providers() -> Json<Value> {
    let found = tokio::task::spawn_blocking(|| {
        let handles: Vec<_> = local_providers()
            .into_iter()
            .map(|mut p| {
                std::thread::spawn(move || {
                    let models = list_models(
                        p["base_url"].as_str().unwrap(),
                        p["api_key"].as_str().unwrap(),
                    );
                    p["running"] = models.is_some().into();
                    p["models"] = json!(models.unwrap_or_default());
                    p
                })
            })
            .collect();
        handles
            .into_iter()
            .filter_map(|h| h.join().ok())
            .collect::<Vec<_>>()
    })
    .await
    .unwrap_or_default();
    Json(Value::Array(found))
}

// ---------- 会议 ----------

async fn list_meetings(State(s): State<Shared>) -> Json<Value> {
    Json(json!(meetings::list(&s.opt.root)))
}

fn meeting_json(dir: &Path) -> Value {
    let info = meetings::summary(dir);
    let mut data = serde_json::to_value(&info).unwrap();
    data["minutes"] = std::fs::read_to_string(dir.join(MINUTES))
        .unwrap_or_default()
        .into();
    data["audio"] = meetings::find_audio(dir).is_some().into();
    data["path"] = dir.to_string_lossy().into();
    data["turns"] = json!([]);
    data["speaker_list"] = json!([]);
    if let Ok(tr) = Transcript::load(&dir.join(TRANSCRIPT_JSON)) {
        let order = tr.speakers_in_order();
        data["speaker_list"] = order
            .iter()
            .enumerate()
            .map(|(i, &s)| json!({"index": i, "name": tr.name_of(s), "custom": tr.speaker_names.contains_key(&s)}))
            .collect();
        let index_of: BTreeMap<String, usize> = order
            .iter()
            .enumerate()
            .map(|(i, &s)| (tr.name_of(s), i))
            .collect();
        data["turns"] = tr
            .turns()
            .into_iter()
            .map(|t| {
                json!({"speaker": t.speaker, "color": index_of.get(&t.speaker).copied().unwrap_or(0),
                       "start_ms": t.start_ms, "ts": fmt_ts(t.start_ms), "text": t.text})
            })
            .collect();
    }
    data
}

async fn get_meeting(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
) -> ApiResult<Json<Value>> {
    Ok(Json(meeting_json(&s.folder(&id)?)))
}

async fn get_audio(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
    req: Request,
) -> ApiResult<Response> {
    let audio = meetings::find_audio(&s.folder(&id)?)
        .ok_or_else(|| ApiError(StatusCode::NOT_FOUND, "没有录音文件".into()))?;
    // ServeFile 支持 Range 请求：播放器拖动进度条时只取需要的那段
    Ok(ServeFile::new(audio)
        .oneshot(req)
        .await
        .map(IntoResponse::into_response)
        .unwrap_or_else(|e| match e {}))
}

#[derive(Deserialize)]
struct SpeakersReq {
    names: Vec<String>,
}

async fn put_speakers(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
    Json(r): Json<SpeakersReq>,
) -> ApiResult<Json<Value>> {
    let dir = s.folder(&id)?;
    let mut tr = Transcript::load(&dir.join(TRANSCRIPT_JSON)).map_err(|_| bad("还没有转写"))?;
    tr.speaker_names = tr
        .speakers_in_order()
        .into_iter()
        .zip(r.names)
        .filter(|(_, n)| !n.trim().is_empty())
        .map(|(spk, n)| (spk, n.trim().to_string()))
        .collect();
    meetings::save_transcript(&tr, &dir).map_err(|e| bad(e.to_string()))?;
    Ok(Json(meeting_json(&dir)))
}

#[derive(Deserialize)]
struct MinutesReq {
    text: String,
}

async fn put_minutes(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
    Json(r): Json<MinutesReq>,
) -> ApiResult<Json<Value>> {
    let dir = s.folder(&id)?;
    std::fs::write(dir.join(MINUTES), format!("{}\n", r.text.trim_end()))
        .map_err(|e| bad(e.to_string()))?;
    Ok(Json(json!({"ok": true})))
}

async fn post_summarize(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
) -> ApiResult<Json<Job>> {
    let dir = s.folder(&id)?;
    let tr = Transcript::load(&dir.join(TRANSCRIPT_JSON))
        .map_err(|_| bad("还没有转写，不能生成纪要"))?;
    let job = Job::new("summarize", &id, "summarizing");
    s.start_job(job.clone())?;
    spawn_job(&s, move |st| st.run_summarize(&tr, &dir, &[]));
    Ok(Json(job))
}

async fn post_process(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
) -> ApiResult<Json<Job>> {
    let dir = s.folder(&id)?;
    let audio = meetings::find_audio(&dir).ok_or_else(|| bad("没有录音文件"))?;
    let job = Job::new("process", &id, "loading");
    s.start_job(job.clone())?;
    spawn_job(&s, move |st| st.run_process(&audio, &dir, &[]));
    Ok(Json(job))
}

async fn post_push(
    State(s): State<Shared>,
    UrlPath(id): UrlPath<String>,
) -> ApiResult<Json<Value>> {
    let dir = s.folder(&id)?;
    let cfg = s.config();
    if cfg.webhook.is_empty() {
        return Err(bad("还没有配置群机器人 webhook，去设置里填一下"));
    }
    let minutes = std::fs::read_to_string(dir.join(MINUTES)).map_err(|_| bad("还没有纪要"))?;
    let platform = tokio::task::spawn_blocking(move || {
        push::send(&cfg.webhook, &minutes, &cfg.webhook_secret)
    })
    .await
    .map_err(|e| bad(e.to_string()))?
    .map_err(|e| ApiError(StatusCode::BAD_GATEWAY, e))?;
    Ok(Json(
        json!({"ok": true, "message": format!("已推送到{}群", platform.name())}),
    ))
}

async fn reveal(State(s): State<Shared>, UrlPath(id): UrlPath<String>) -> ApiResult<Json<Value>> {
    let dir = s.folder(&id)?;
    let _ = std::process::Command::new("/usr/bin/open").arg(dir).spawn();
    Ok(Json(json!({"ok": true})))
}

// ---------- 录音与导入 ----------

#[derive(Deserialize)]
struct RecordReq {
    #[serde(default = "default_title")]
    title: String,
    #[serde(default = "yes")]
    system: bool,
    #[serde(default = "yes")]
    mic: bool,
    #[serde(default)]
    hotwords: Vec<String>,
}

fn default_title() -> String {
    "会议".into()
}

fn yes() -> bool {
    true
}

async fn record_start(State(s): State<Shared>, Json(r): Json<RecordReq>) -> ApiResult<Json<Job>> {
    if !r.system && !r.mic {
        return Err(bad("系统声音和麦克风至少开一个"));
    }
    let engine = s.engine().map_err(bad)?; // 边录边转需要模型；没下载就先提示下载
    let system = r.system && system_audio_supported(&s.opt.recorder_bin);
    let dir = meetings::new_folder(&s.opt.root, &r.title, chrono::Local::now().naive_local());
    let name = dir.file_name().unwrap().to_string_lossy().into_owned();
    let mut job = Job::new("record", &name, "recording");
    s.start_job(job.clone())?;
    let recorder = match Recorder::start(&s.opt.recorder_bin, &dir, system, r.mic) {
        Ok(rec) => rec,
        Err(e) => {
            *s.job.lock().unwrap() = None;
            let _ = std::fs::remove_dir_all(&dir);
            return Err(ApiError(StatusCode::INTERNAL_SERVER_ERROR, e));
        }
    };
    let live = LiveTranscriber::start(engine, &dir, &recorder.sources);
    job.sources = recorder.sources.clone();
    s.update(|j| j.sources = job.sources.clone());
    *s.recording.lock().unwrap() = Some(Live {
        recorder,
        live: Some(live),
        hotwords: r.hotwords,
    });
    Ok(Json(job))
}

async fn record_stop(State(s): State<Shared>) -> ApiResult<Json<Job>> {
    let job = s
        .job
        .lock()
        .unwrap()
        .clone()
        .filter(|j| j.kind == "record" && j.state == "recording");
    let Some(job) = job else {
        return Err(bad("没有正在进行的录音"));
    };
    let Some(mut live) = s.recording.lock().unwrap().take() else {
        return Err(bad("没有正在进行的录音"));
    };
    s.update(|j| j.state = "saving".into());
    let dir = s.opt.root.join(&job.meeting);
    spawn_job(&s, move |st| {
        live.recorder.stop();
        st.update(|j| j.state = "finalizing".into());
        let engine = st.engine()?;
        let final_audio = dir.join(AUDIO);
        // 原始音轨合成前先把最后几秒转完
        let result = live.live.take().map(|l| l.finish(&engine, &final_audio));
        match result {
            Some(Ok((tr, t))) if !tr.sentences.is_empty() => {
                eprintln!(
                    "结束录音后转写：剩余 {:.1}s，{} 段，聚类 {:.2}s",
                    t.tail_s, t.segments, t.cluster_s
                );
                meetings::save_transcript(&tr, &dir).map_err(|e| e.to_string())?;
                // 合成录音和生成纪要同时进行
                let mix_dir = dir.clone();
                let mixer =
                    std::thread::spawn(move || record::mixdown(&mix_dir, &mix_dir.join(AUDIO)));
                let summary = st.run_summarize(&tr, &dir, &live.hotwords);
                let mixed = mixer.join().map_err(|_| "合成录音线程异常".to_string())?;
                summary?;
                mixed.map_err(|e| {
                    format!("纪要已生成，但合成录音失败：{e}（原始音轨还在会议文件夹里）")
                })
            }
            // 边录边转出错或什么都没识别到：合成录音后整段重转
            _ => {
                record::mixdown(&dir, &final_audio)?;
                st.run_process(&final_audio, &dir, &live.hotwords)
            }
        }
    });
    let mut out = job;
    out.state = "saving".into();
    Ok(Json(out))
}

async fn import_file(State(s): State<Shared>, mut form: Multipart) -> ApiResult<Json<Job>> {
    let (mut data, mut filename, mut title, mut modified) =
        (None, String::from("录音.m4a"), String::new(), 0f64);
    while let Some(field) = form.next_field().await.map_err(|e| bad(e.to_string()))? {
        match field.name() {
            Some("file") => {
                filename = field.file_name().unwrap_or("录音.m4a").to_string();
                data = Some(field.bytes().await.map_err(|e| bad(e.to_string()))?);
            }
            Some("title") => title = field.text().await.unwrap_or_default(),
            Some("modified") => {
                modified = field
                    .text()
                    .await
                    .unwrap_or_default()
                    .parse()
                    .unwrap_or(0.0)
            }
            _ => {}
        }
    }
    let data = data.ok_or_else(|| bad("没有收到文件"))?;
    let ext = Path::new(&filename)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    if !meetings::AUDIO_EXTS.contains(&ext.as_str()) {
        return Err(bad(format!("不支持的文件类型：.{ext}")));
    }
    let when = if modified > 0.0 {
        chrono::DateTime::from_timestamp_millis(modified as i64)
            .map(|d| d.with_timezone(&chrono::Local).naive_local())
            .unwrap_or_else(|| chrono::Local::now().naive_local())
    } else {
        chrono::Local::now().naive_local()
    };
    let stem = Path::new(&filename)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("会议")
        .to_string();
    let dir = meetings::new_folder(
        &s.opt.root,
        if title.is_empty() { &stem } else { &title },
        when,
    );
    let name = dir.file_name().unwrap().to_string_lossy().into_owned();
    let job = Job::new("process", &name, "saving");
    s.start_job(job.clone())?;
    std::fs::create_dir_all(&dir).map_err(|e| bad(e.to_string()))?;
    let audio = dir.join(format!("录音.{ext}"));
    std::fs::write(&audio, &data).map_err(|e| bad(e.to_string()))?;
    spawn_job(&s, move |st| st.run_process(&audio, &dir, &[]));
    Ok(Json(job))
}

async fn get_job(State(s): State<Shared>) -> Json<Value> {
    let Some(job) = s.job.lock().unwrap().clone() else {
        return Json(json!({"state": "idle"}));
    };
    let mut data = serde_json::to_value(&job).unwrap();
    data["elapsed"] = (now() - job.started_at).into();
    data["model_loaded"] = s.engine.get().is_some().into();
    if job.state == "recording" {
        let dir = s.opt.root.join(&job.meeting);
        let levels: BTreeMap<&str, f32> = job
            .sources
            .iter()
            .map(|src| {
                (
                    src.as_str(),
                    audio::track_level(&dir.join(format!("{src}.wav")), 0.15),
                )
            })
            .collect();
        data["levels"] = json!(levels);
        let mut rec = s.recording.lock().unwrap();
        if let Some(live) = rec.as_mut() {
            data["live"] = live
                .live
                .as_ref()
                .map(|l| {
                    l.recent(8)
                        .into_iter()
                        .map(|(t, x)| json!({"start_ms": t, "text": x}))
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default()
                .into();
            if let Some(err) = live.recorder.exited() {
                data["error"] = format!("录音中断：{err}").into();
            }
        }
    }
    Json(data)
}

async fn dismiss(State(s): State<Shared>) -> Json<Value> {
    let mut slot = s.job.lock().unwrap();
    if slot.as_ref().is_some_and(|j| !j.busy()) {
        *slot = None;
    }
    Json(json!({"ok": true}))
}

// ---------- 模型下载（桌面版新增） ----------

async fn download_models(State(s): State<Shared>) -> ApiResult<Json<Job>> {
    if s.model_paths().ready() {
        return Err(bad("模型已经下载好了"));
    }
    let job = Job::new("models", "", "downloading");
    s.start_job(job.clone())?;
    spawn_job(&s, |st| {
        models::ensure(&st.model_paths(), |done, total| {
            st.update(|j| {
                j.stage = format!("{:.0} / {:.0} MB", done as f64 / 1e6, total as f64 / 1e6)
            })
        })?;
        st.engine().map(|_| ())
    });
    Ok(Json(job))
}

pub fn router(state: Shared) -> Router {
    let origins = [
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost:1420",
    ];
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::list(
            origins.iter().map(|o| HeaderValue::from_static(o)),
        ))
        .allow_methods([Method::GET, Method::POST, Method::PUT])
        .allow_headers([axum::http::header::CONTENT_TYPE, axum::http::header::RANGE]);
    Router::new()
        .route("/api/env", get(env))
        .route("/api/config", get(get_config).put(put_config))
        .route("/api/config/test", post(test_llm))
        .route("/api/llm/providers", get(llm_providers))
        .route("/api/llm/models", post(llm_models))
        .route("/api/meetings", get(list_meetings))
        .route("/api/meetings/{id}", get(get_meeting))
        .route("/api/meetings/{id}/audio", get(get_audio))
        .route("/api/meetings/{id}/speakers", put(put_speakers))
        .route("/api/meetings/{id}/minutes", put(put_minutes))
        .route("/api/meetings/{id}/summarize", post(post_summarize))
        .route("/api/meetings/{id}/process", post(post_process))
        .route("/api/meetings/{id}/push", post(post_push))
        .route("/api/meetings/{id}/reveal", post(reveal))
        .route("/api/record/start", post(record_start))
        .route("/api/record/stop", post(record_stop))
        .route("/api/import", post(import_file))
        .route("/api/job", get(get_job))
        .route("/api/job/dismiss", post(dismiss))
        .route("/api/models/download", post(download_models))
        .layer(axum::extract::DefaultBodyLimit::max(4 << 30)) // 导入的录屏可能有几个 GB
        .layer(cors)
        .with_state(state)
}

/// 在 127.0.0.1 上启动服务（port 为 0 时随机选），返回实际端口和要一直跑的服务。
pub async fn serve(
    opt: Options,
    port: u16,
) -> std::io::Result<(u16, impl std::future::Future<Output = ()>)> {
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    let port = listener.local_addr()?.port();
    let app = router(AppState::new(opt));
    Ok((port, async move {
        let _ = axum::serve(listener, app).await;
    }))
}
