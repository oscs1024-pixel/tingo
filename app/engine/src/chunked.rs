//! 分段转写：把音频按停顿切成 10～30 秒逐段识别，最后统一聚类说话人。
//! 录音中（边录边转）和处理已有文件共用；Python 版 live.py 的移植。

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::asr::{Engine, Segment, SR};
use crate::audio::{duck_mix_default, find_cut, GrowingWav, MAX_CHUNK_S, MIN_CHUNK_S, SEARCH_S};
use crate::cluster::cluster;
use crate::transcript::{Sentence, Transcript};

const MIX_MARGIN_S: f64 = 1.0; // 混音时往前多读一点，让"让路"的保持时间跨片段连续
const LOOKAHEAD_S: f64 = 0.2; // 文件末尾先不处理，留给"提前关麦克风"看后面的数据

#[derive(Default)]
pub struct Timings {
    pub tail_s: f64,
    pub cluster_s: f64,
    pub segments: usize,
}

#[derive(Default)]
pub struct Chunker {
    pending: Vec<f32>,
    pending_start: f64, // pending 第一个样本对应的秒数
    pub segments: Vec<Segment>,
    pub timings: Timings,
}

impl Chunker {
    pub fn feed(&mut self, audio: &[f32]) {
        self.pending.extend_from_slice(audio);
    }

    pub fn processed_s(&self) -> f64 {
        self.pending_start
    }

    /// 从待处理音频里切出下一段；不够长或找不到合适的切点时返回 None。
    pub fn next_chunk(&mut self, last: bool) -> Option<(Vec<f32>, f64)> {
        let min_len = if last {
            (0.3 * SR as f64) as usize
        } else {
            (MIN_CHUNK_S * SR as f64) as usize
        };
        if self.pending.len() < min_len {
            return None;
        }
        // 每段最长 MAX_CHUNK_S：处理落后时也不会攒出一个巨大的段
        let max = (MAX_CHUNK_S * SR as f64) as usize;
        let window = &self.pending[..self.pending.len().min(max)];
        let over = self.pending.len() > window.len();
        let cut = if last && !over {
            self.pending.len()
        } else {
            find_cut(
                window,
                last || over || window.len() >= max,
                MIN_CHUNK_S,
                SEARCH_S,
            )?
        };
        let chunk: Vec<f32> = self.pending.drain(..cut).collect();
        let offset = self.pending_start;
        self.pending_start += cut as f64 / SR as f64;
        Some((chunk, offset))
    }

    pub fn transcribe(&mut self, engine: &Engine, chunk: &[f32], offset_s: f64) -> Result<Vec<Segment>, String> {
        let segs = engine.transcribe(chunk, (offset_s * 1000.0).round() as u64)?;
        self.segments.extend(segs.iter().cloned());
        Ok(segs)
    }

    pub fn duration_ms(&self) -> u64 {
        ((self.pending_start + self.pending.len() as f64 / SR as f64) * 1000.0).round() as u64
    }

    /// 全部声纹统一聚类，得到说话人编号全场一致的转写。每段的说话人取段内小窗的多数。
    pub fn assemble(&mut self, source: &Path) -> Transcript {
        let t = Instant::now();
        let mut owner = Vec::new(); // 每个小窗属于哪一段
        let mut emb = Vec::new();
        for (i, seg) in self.segments.iter().enumerate() {
            for e in &seg.embeddings {
                owner.push(i);
                emb.push(e.clone());
            }
        }
        let labels = cluster(&emb);
        let mut votes: Vec<std::collections::BTreeMap<usize, usize>> =
            vec![Default::default(); self.segments.len()];
        for (&i, &l) in owner.iter().zip(&labels) {
            *votes[i].entry(l).or_default() += 1;
        }
        let mut speaker: Vec<Option<usize>> = votes
            .iter()
            .map(|v| {
                v.iter()
                    .max_by_key(|(l, n)| (**n, std::cmp::Reverse(**l)))
                    .map(|(l, _)| *l)
            })
            .collect();
        // 太短提不出声纹的段：归给前一段（开头就没有的归给后一段）
        let mut last = None;
        for s in speaker.iter_mut() {
            if s.is_none() {
                *s = last;
            }
            last = *s;
        }
        // 按首次出现的先后重新编号（第一个开口的是说话人1），和 FunASR 一致
        let mut order: Vec<usize> = Vec::new();
        for s in speaker.iter().flatten() {
            if !order.contains(s) {
                order.push(*s);
            }
        }
        let speaker: Vec<Option<usize>> = speaker
            .iter()
            .map(|s| s.map(|l| order.iter().position(|&o| o == l).unwrap()))
            .collect();
        let first = speaker.iter().flatten().next().copied().unwrap_or(0);
        self.timings.cluster_s = t.elapsed().as_secs_f64();
        self.timings.segments = self.segments.len();
        Transcript {
            source: source.to_string_lossy().into_owned(),
            duration_ms: self.duration_ms(),
            speaker_names: Default::default(),
            sentences: self
                .segments
                .iter()
                .zip(speaker)
                .map(|(seg, spk)| Sentence {
                    speaker: spk.unwrap_or(first),
                    start_ms: seg.start_ms,
                    end_ms: seg.end_ms,
                    text: seg.text.clone(),
                })
                .collect(),
        }
    }
}

/// 分段转写一段完整音频（16 kHz 单声道）。
pub fn transcribe_samples(
    engine: &Engine,
    audio: &[f32],
    source: &Path,
    mut progress: impl FnMut(f64),
) -> Result<Transcript, String> {
    let total = audio.len() as f64 / SR as f64;
    let mut c = Chunker::default();
    c.feed(audio);
    while let Some((chunk, offset)) = c.next_chunk(false).or_else(|| c.next_chunk(true)) {
        c.transcribe(engine, &chunk, offset)?;
        if total > 0.0 {
            progress((c.processed_s() / total).min(1.0));
        }
    }
    Ok(c.assemble(source))
}

/// 录音进行中读取录音组件正在写的音轨，边录边转。
pub struct LiveTranscriber {
    inner: Arc<Mutex<LiveState>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

struct LiveState {
    tracks: Vec<(String, GrowingWav)>,
    mixed_until: f64,
    chunker: Chunker,
    lines: Vec<(u64, String)>,
    error: Option<String>,
}

impl LiveState {
    fn mix_new(&mut self, last: bool) {
        let avail = self
            .tracks
            .iter_mut()
            .map(|(_, t)| t.seconds())
            .fold(f64::INFINITY, f64::min);
        if !avail.is_finite() {
            return;
        }
        let commit_end = if last { avail } else { avail - LOOKAHEAD_S };
        if commit_end - self.mixed_until < if last { 0.05 } else { 1.0 } {
            return;
        }
        let margin = MIX_MARGIN_S.min(self.mixed_until);
        let start = self.mixed_until - margin;
        let mut parts: Vec<(String, Vec<f32>)> = self
            .tracks
            .iter_mut()
            .map(|(name, t)| (name.clone(), t.read(start, avail)))
            .collect();
        let mixed = if parts.len() == 2 {
            let mic = parts.iter().position(|(n, _)| n == "mic").unwrap_or(1);
            let mic = parts.remove(mic).1;
            duck_mix_default(&parts[0].1, &mic)
        } else {
            parts.pop().map(|p| p.1).unwrap_or_default()
        };
        let a = (margin * SR as f64).round() as usize;
        let b = (((commit_end - start) * SR as f64).round() as usize).min(mixed.len());
        if b <= a {
            return;
        }
        self.chunker.feed(&mixed[a..b]);
        // 按实际拿到的样本数推进，两路长度差几个样本也不会累积漂移
        self.mixed_until = start + b as f64 / SR as f64;
    }
}

impl LiveTranscriber {
    /// `sources` 是实际在录的音轨名（"system" / "mic"），文件在 `dir/<名字>.wav`。
    pub fn start(engine: Arc<Engine>, dir: &Path, sources: &[String]) -> Self {
        let state = LiveState {
            tracks: sources
                .iter()
                .map(|s| (s.clone(), GrowingWav::new(dir.join(format!("{s}.wav")))))
                .collect(),
            mixed_until: 0.0,
            chunker: Chunker::default(),
            lines: Vec::new(),
            error: None,
        };
        let inner = Arc::new(Mutex::new(state));
        let stop = Arc::new(AtomicBool::new(false));
        let thread = {
            let (inner, stop) = (inner.clone(), stop.clone());
            std::thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    if !step(&inner, &engine, false) {
                        std::thread::sleep(Duration::from_secs(1));
                    }
                }
            })
        };
        Self {
            inner,
            stop,
            thread: Some(thread),
        }
    }

    /// 最近识别出的几句，给界面实时显示。
    pub fn recent(&self, n: usize) -> Vec<(u64, String)> {
        let s = self.inner.lock().unwrap();
        s.lines[s.lines.len().saturating_sub(n)..].to_vec()
    }

    /// 到目前为止识别出了多少句（有没有新出文字，自动结束录音时用来判断有没有人说话）。
    pub fn line_count(&self) -> usize {
        let s = self.inner.lock().unwrap();
        s.lines.iter().filter(|(_, t)| !t.trim().is_empty()).count()
    }

    /// 录音停止后调用：处理剩下的几秒，统一聚类，返回完整转写。
    pub fn finish(
        mut self,
        engine: &Engine,
        source: &Path,
    ) -> Result<(Transcript, Timings), String> {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            t.join().map_err(|_| {
                crate::i18n::tr("边录边转线程异常退出", "The live transcription thread crashed").to_string()
            })?;
        }
        let t = Instant::now();
        loop {
            let mut s = self.inner.lock().unwrap();
            if let Some(e) = s.error.take() {
                return Err(e);
            }
            s.mix_new(true);
            let Some((chunk, offset)) = s.chunker.next_chunk(true) else {
                break;
            };
            let segs = s.chunker.transcribe(engine, &chunk, offset)?;
            s.lines
                .extend(segs.into_iter().map(|g| (g.start_ms, g.text)));
        }
        let mut s = self.inner.lock().unwrap();
        let tail_s = t.elapsed().as_secs_f64();
        let tr = s.chunker.assemble(source);
        let mut timings = std::mem::take(&mut s.chunker.timings);
        timings.tail_s = tail_s;
        Ok((tr, timings))
    }
}

/// 读新数据、必要时切一段识别；返回这一步是否处理了一段。
fn step(inner: &Mutex<LiveState>, engine: &Engine, last: bool) -> bool {
    let job = {
        let mut s = inner.lock().unwrap();
        if s.error.is_some() { return false; }
        s.mix_new(last);
        s.chunker.next_chunk(last)
    };
    let Some((chunk, offset)) = job else {
        return false;
    };
    // 识别时不持锁：界面取实时文字不用等
    let segs = engine.transcribe(&chunk, (offset * 1000.0).round() as u64);
    let mut s = inner.lock().unwrap();
    let segs = match segs { Ok(s) => s, Err(e) => { s.error = Some(e); return false; } };
    s.lines
        .extend(segs.iter().map(|g| (g.start_ms, g.text.clone())));
    s.chunker.segments.extend(segs);
    true
}

pub fn wav_path(dir: &Path, source: &str) -> PathBuf {
    dir.join(format!("{source}.wav"))
}
