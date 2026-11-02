//! 识别引擎：sherpa-onnx 运行 FunASR Paraformer-large（int8）+ Silero VAD + CT-Transformer 标点 + CAM++ 声纹。
//!
//! 这条路线的选型依据见 spike/sherpa/RESULTS.md。识别结果没有逐字时间戳，
//! 所以按 VAD 切出的语音段识别、每段提一个声纹，说话人以语音段为单位判断。

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use sherpa_onnx::{
    OfflinePunctuation, OfflinePunctuationConfig, OfflinePunctuationModelConfig, OfflineRecognizer,
    OfflineRecognizerConfig, SpeakerEmbeddingExtractor, SpeakerEmbeddingExtractorConfig,
    VadModelConfig, VoiceActivityDetector,
};

pub const SR: usize = 16000;

/// 模型文件布局（相对模型目录）。
pub struct ModelPaths {
    pub dir: PathBuf,
}

impl ModelPaths {
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self { dir: dir.into() }
    }
    pub fn paraformer(&self) -> PathBuf {
        self.dir.join("paraformer/model.int8.onnx")
    }
    pub fn tokens(&self) -> PathBuf {
        self.dir.join("paraformer/tokens.txt")
    }
    pub fn vad(&self) -> PathBuf {
        self.dir.join("silero_vad.onnx")
    }
    pub fn punct(&self) -> PathBuf {
        self.dir.join("punct/model.int8.onnx")
    }
    pub fn speaker(&self) -> PathBuf {
        self.dir.join("campplus_zh.onnx")
    }
    pub fn all(&self) -> [PathBuf; 5] {
        [
            self.paraformer(),
            self.tokens(),
            self.vad(),
            self.punct(),
            self.speaker(),
        ]
    }
    pub fn ready(&self) -> bool {
        self.all().iter().all(|p| p.is_file())
    }
}

/// 一段语音的识别结果（时间是整段音频里的绝对毫秒）。
#[derive(Clone, Debug)]
pub struct Segment {
    pub start_ms: u64,
    pub end_ms: u64,
    pub text: String,
    /// 声纹：段内每 1.5 秒一个小窗（滑动 0.75 秒），和 FunASR 的做法一致；太短提不出时为空
    pub embeddings: Vec<Vec<f32>>,
}

pub struct Engine {
    rec: OfflineRecognizer,
    punct: OfflinePunctuation,
    spk: SpeakerEmbeddingExtractor,
    vad: VadModelConfig,
    // sherpa 的对象单线程使用是安全的，这里统一串行化
    lock: Mutex<()>,
}

// SAFETY: 所有使用都经过 `lock` 串行化；sherpa-onnx 的 C 对象在单线程使用时是安全的。
unsafe impl Sync for Engine {}
unsafe impl Send for Engine {}

fn path_str(p: &Path) -> Option<String> {
    Some(p.to_string_lossy().into_owned())
}

impl Engine {
    pub fn load(models: &ModelPaths, threads: i32) -> Result<Self, String> {
        if let Some(missing) = models.all().iter().find(|p| !p.is_file()) {
            return Err(format!("缺少模型文件：{}", missing.display()));
        }
        let mut cfg = OfflineRecognizerConfig::default();
        cfg.model_config.paraformer.model = path_str(&models.paraformer());
        cfg.model_config.tokens = path_str(&models.tokens());
        cfg.model_config.num_threads = threads;
        let rec = OfflineRecognizer::create(&cfg).ok_or("识别模型加载失败")?;

        let punct = OfflinePunctuation::create(&OfflinePunctuationConfig {
            model: OfflinePunctuationModelConfig {
                ct_transformer: path_str(&models.punct()),
                num_threads: 2,
                ..Default::default()
            },
        })
        .ok_or("标点模型加载失败")?;

        let spk = SpeakerEmbeddingExtractor::create(&SpeakerEmbeddingExtractorConfig {
            model: path_str(&models.speaker()),
            num_threads: 2,
            ..Default::default()
        })
        .ok_or("声纹模型加载失败")?;

        let mut vad = VadModelConfig::default();
        vad.silero_vad.model = path_str(&models.vad());
        vad.silero_vad.threshold = 0.5;
        vad.silero_vad.min_silence_duration = 0.3;
        vad.silero_vad.min_speech_duration = 0.25;
        vad.silero_vad.window_size = 512;
        vad.silero_vad.max_speech_duration = 10.0;
        vad.sample_rate = SR as i32;
        vad.num_threads = 1;
        Ok(Self {
            rec,
            punct,
            spk,
            vad,
            lock: Mutex::new(()),
        })
    }

    /// 识别一段 16 kHz 单声道音频。`offset_ms` 是这段在整场会议里的起点。
    /// 这个模型不支持热词；热词在生成纪要时作为术语表交给大模型纠正。
    pub fn transcribe(&self, samples: &[f32], offset_ms: u64) -> Vec<Segment> {
        let _guard = self.lock.lock().unwrap();
        let Some(vad) = VoiceActivityDetector::create(&self.vad, 120.0) else {
            return Vec::new();
        };
        for chunk in samples.chunks(512) {
            vad.accept_waveform(chunk);
        }
        vad.flush();
        let mut out = Vec::new();
        while let Some(seg) = vad.front() {
            let audio = seg.samples();
            let start = offset_ms + seg.start() as u64 * 1000 / SR as u64;
            let end = start + audio.len() as u64 * 1000 / SR as u64;
            let stream = self.rec.create_stream();
            stream.accept_waveform(SR as i32, audio);
            self.rec.decode(&stream);
            let raw = stream.get_result().map(|r| r.text).unwrap_or_default();
            let raw = raw.trim();
            if !raw.is_empty() {
                let text = self
                    .punct
                    .add_punctuation(raw)
                    .unwrap_or_else(|| raw.to_string());
                let embeddings = windows(audio.len())
                    .filter_map(|(a, b)| self.embed(&audio[a..b]))
                    .collect();
                out.push(Segment {
                    start_ms: start,
                    end_ms: end,
                    text,
                    embeddings,
                });
            }
            vad.pop();
        }
        out
    }

    fn embed(&self, audio: &[f32]) -> Option<Vec<f32>> {
        let stream = self.spk.create_stream()?;
        stream.accept_waveform(SR as i32, audio);
        stream.input_finished();
        if !self.spk.is_ready(&stream) {
            return None;
        }
        self.spk.compute(&stream).filter(|e| !e.is_empty())
    }
}

/// 声纹小窗：1.5 秒一窗、滑动 0.75 秒，最后一窗贴着段尾；不足 1.5 秒的段整段一窗（FunASR sv_chunk）。
pub fn windows(len: usize) -> impl Iterator<Item = (usize, usize)> {
    let (win, shift) = (SR * 3 / 2, SR * 3 / 4);
    let mut st = 0;
    let mut done = false;
    std::iter::from_fn(move || {
        if done || len == 0 {
            return None;
        }
        let ed = (st + win).min(len);
        let a = ed.saturating_sub(win);
        done = ed == len;
        st += shift;
        Some((a, ed))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_cover_segment() {
        assert_eq!(windows(SR).collect::<Vec<_>>(), vec![(0, SR)]);
        let w: Vec<_> = windows(SR * 3).collect();
        assert_eq!(w.first(), Some(&(0, SR * 3 / 2)));
        assert_eq!(w.last().unwrap().1, SR * 3);
        assert!(w.windows(2).all(|p| p[1].0 - p[0].0 <= SR * 3 / 4));
        assert_eq!(windows(0).count(), 0);
    }
}
