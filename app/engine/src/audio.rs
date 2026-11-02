//! 音频小工具：读取正在写入的 wav、重采样、外放回声"让路"、电平、找停顿切点。
//! 都是 Python 版 record.py / live.py 的移植，参数和行为保持一致。

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use crate::asr::SR;

/// 读取正在被写入的 wav：录音组件持续追加数据，文件头里的长度字段不可信，按文件大小算。
pub struct GrowingWav {
    path: PathBuf,
    header: Option<Header>,
}

#[derive(Clone, Copy)]
struct Header {
    float: bool,
    channels: usize,
    rate: usize,
    width: usize,
    data_start: u64,
}

impl GrowingWav {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            header: None,
        }
    }

    fn header(&mut self) -> Option<Header> {
        if self.header.is_none() {
            self.header = parse_header(&self.path);
        }
        self.header
    }

    pub fn seconds(&mut self) -> f64 {
        let Some(h) = self.header() else { return 0.0 };
        let size = std::fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0);
        let frames = size.saturating_sub(h.data_start) / (h.width * h.channels) as u64;
        frames as f64 / h.rate as f64
    }

    /// [start_s, end_s) 这段，单声道 16 kHz。
    pub fn read(&mut self, start_s: f64, end_s: f64) -> Vec<f32> {
        let Some(h) = self.header() else {
            return Vec::new();
        };
        if end_s <= start_s {
            return Vec::new();
        }
        let frame = h.width * h.channels;
        let a = (start_s * h.rate as f64) as u64;
        let b = (end_s * h.rate as f64) as u64;
        let mut raw = vec![0u8; ((b - a) as usize) * frame];
        let Ok(mut f) = File::open(&self.path) else {
            return Vec::new();
        };
        if f.seek(SeekFrom::Start(h.data_start + a * frame as u64))
            .is_err()
        {
            return Vec::new();
        }
        let n = read_up_to(&mut f, &mut raw);
        raw.truncate(n - n % frame);
        let mono = decode_mono(&raw, h);
        resample(&mono, h.rate)
    }
}

fn read_up_to(f: &mut File, buf: &mut [u8]) -> usize {
    let mut n = 0;
    while n < buf.len() {
        match f.read(&mut buf[n..]) {
            Ok(0) | Err(_) => break,
            Ok(k) => n += k,
        }
    }
    n
}

fn parse_header(path: &Path) -> Option<Header> {
    let mut head = vec![0u8; 4096];
    let n = read_up_to(&mut File::open(path).ok()?, &mut head);
    let head = &head[..n];
    let find = |tag: &[u8]| head.windows(4).position(|w| w == tag);
    let (fmt_at, data_at) = (find(b"fmt ")?, find(b"data")?);
    let u16_at = |i: usize| u16::from_le_bytes([head[i], head[i + 1]]);
    let u32_at = |i: usize| u32::from_le_bytes([head[i], head[i + 1], head[i + 2], head[i + 3]]);
    let mut tag = u16_at(fmt_at + 8);
    let channels = u16_at(fmt_at + 10) as usize;
    let rate = u32_at(fmt_at + 12) as usize;
    let bits = u16_at(fmt_at + 22) as usize;
    if tag == 0xFFFE {
        tag = u16_at(fmt_at + 8 + 24); // WAVE_FORMAT_EXTENSIBLE：真实格式在子格式 GUID 的前两个字节
    }
    let float = match (tag, bits) {
        (3, 32) => true,
        (1, 16) => false,
        _ => return None,
    };
    Some(Header {
        float,
        channels: channels.max(1),
        rate,
        width: bits / 8,
        data_start: data_at as u64 + 8,
    })
}

fn decode_mono(raw: &[u8], h: Header) -> Vec<f32> {
    let frame = h.width * h.channels;
    raw.chunks_exact(frame)
        .map(|f| {
            let sum: f32 = f
                .chunks_exact(h.width)
                .map(|s| {
                    if h.float {
                        f32::from_le_bytes([s[0], s[1], s[2], s[3]])
                    } else {
                        i16::from_le_bytes([s[0], s[1]]) as f32 / 32768.0
                    }
                })
                .sum();
            sum / h.channels as f32
        })
        .collect()
}

pub fn resample(x: &[f32], rate: usize) -> Vec<f32> {
    if rate == SR || x.is_empty() {
        return x.to_vec();
    }
    match sherpa_onnx::LinearResampler::create(rate as i32, SR as i32) {
        Some(r) => r.resample(x, true),
        None => Vec::new(),
    }
}

/// 读整个 wav 文件为 16 kHz 单声道（只认 16 位整型 / 32 位浮点 PCM）。
pub fn read_wav16k(path: &Path) -> Option<Vec<f32>> {
    let mut w = GrowingWav::new(path);
    let secs = w.seconds();
    (secs > 0.0).then(|| w.read(0.0, secs))
}

/// 外放回声"让路"：系统声音里有人说话时只用系统声音，其余时间用麦克风。
///
/// 系统声音一有信号就关麦克风，并多保持 hold_ms 盖住回声尾巴；ScreenCaptureKit 的系统声音轨
/// 比麦克风里的回声还晚 40～55 ms，所以再提前 pre_ms 关。代价是双方同时说话时本地一侧会被盖掉。
pub fn duck_mix(
    system: &[f32],
    mic: &[f32],
    threshold_db: f32,
    hold_ms: usize,
    pre_ms: usize,
    fade_ms: usize,
) -> Vec<f32> {
    let n = system.len().max(mic.len());
    let at = |v: &[f32], i: usize| v.get(i).copied().unwrap_or(0.0);
    let frame = SR / 50; // 20 ms
    let frames = n.div_ceil(frame);
    let active: Vec<bool> = (0..frames)
        .map(|f| {
            let e: f32 = (f * frame..((f + 1) * frame).min(n))
                .map(|i| at(system, i).powi(2))
                .sum::<f32>()
                / frame as f32;
            10.0 * (e + 1e-12).log10() > threshold_db
        })
        .collect();
    let (hold, pre) = ((hold_ms / 20).max(1), pre_ms / 20);
    let open: Vec<f32> = (0..frames)
        .map(|f| {
            let lo = f.saturating_sub(hold);
            let hi = (f + pre).min(frames - 1);
            if active[lo..=hi].iter().any(|&a| a) {
                0.0
            } else {
                1.0
            }
        })
        .collect();
    // 每个样本的麦克风增益，做 fade_ms 的平滑，避免开关时的咔哒声
    let raw_gain: Vec<f32> = (0..n).map(|i| open[i / frame]).collect();
    let fade = (SR * fade_ms / 1000).max(1);
    let half = fade / 2;
    let mut prefix = vec![0.0f64; n + 1];
    for i in 0..n {
        prefix[i + 1] = prefix[i] + raw_gain[i] as f64;
    }
    (0..n)
        .map(|i| {
            let (a, b) = (i.saturating_sub(half), (i + fade - half).min(n));
            let g = ((prefix[b] - prefix[a]) / fade as f64) as f32;
            at(system, i) + at(mic, i) * g
        })
        .collect()
}

pub fn duck_mix_default(system: &[f32], mic: &[f32]) -> Vec<f32> {
    duck_mix(system, mic, -50.0, 700, 120, 10)
}

/// 正在写入的 wav 末尾 window_s 秒的音量，0～1，界面画电平用。
pub fn track_level(path: &Path, window_s: f64) -> f32 {
    let mut w = GrowingWav::new(path);
    let end = w.seconds();
    if end <= 0.0 {
        return 0.0;
    }
    let x = w.read((end - window_s).max(0.0), end);
    if x.is_empty() {
        return 0.0;
    }
    let e: f32 = x.iter().map(|v| v * v).sum::<f32>() / x.len() as f32;
    let db = 10.0 * (e + 1e-12).log10();
    ((db + 60.0) / 50.0).clamp(0.0, 1.0)
}

pub const MIN_CHUNK_S: f64 = 10.0;
pub const MAX_CHUNK_S: f64 = 30.0;
pub const SEARCH_S: f64 = 6.0;
const QUIET_WIN_S: f64 = 0.4;

/// 在片段末尾 SEARCH_S 秒里找最安静的窗口中点作为切点；一直有人说话且未到强制长度时返回 None。
pub fn find_cut(audio: &[f32], force: bool, min_s: f64, search_s: f64) -> Option<usize> {
    let n = audio.len();
    let lo = ((min_s - search_s) * SR as f64)
        .max(n as f64 - search_s * SR as f64)
        .max(0.0) as usize;
    let win = (QUIET_WIN_S * SR as f64) as usize;
    let hop = win / 4;
    if n < lo + win {
        return force.then_some(n);
    }
    let starts: Vec<usize> = (lo..n - win).step_by(hop).collect();
    let energy: Vec<f32> = starts
        .iter()
        .map(|&s| audio[s..s + win].iter().map(|v| v * v).sum::<f32>() / win as f32)
        .collect();
    let (best, &quiet) = energy
        .iter()
        .enumerate()
        .min_by(|a, b| a.1.total_cmp(b.1))?;
    let mut sorted = energy.clone();
    sorted.sort_by(f32::total_cmp);
    let loud = sorted[((sorted.len() - 1) as f32 * 0.9) as usize];
    let db = |e: f32| 10.0 * (e + 1e-12).log10();
    (force || db(quiet) < db(loud) - 15.0).then_some(starts[best] + win / 2)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(secs: f32, amp: f32) -> Vec<f32> {
        (0..(secs * SR as f32) as usize)
            .map(|i| amp * (i as f32 * 2.0 * std::f32::consts::PI * 440.0 / SR as f32).sin())
            .collect()
    }
    fn silence(secs: f32) -> Vec<f32> {
        vec![0.0; (secs * SR as f32) as usize]
    }

    #[test]
    fn mic_muted_while_system_active_and_before_it_starts() {
        let system = [silence(1.0), tone(1.0, 0.3), silence(2.0)].concat();
        let echo = [silence(0.95), tone(1.4, 0.15), silence(1.65)].concat(); // 回声比系统轨早 50 ms 开始
        let out = duck_mix_default(&system, &echo);
        let a = (0.95 * SR as f32) as usize..(2.6 * SR as f32) as usize;
        let leak = a
            .clone()
            .map(|i| (out[i] - system[i]).abs())
            .fold(0.0, f32::max);
        assert!(leak < 1e-3, "回声漏进来了：{leak}");
    }

    #[test]
    fn mic_passes_when_system_silent() {
        let system = [tone(1.0, 0.3), silence(3.0)].concat();
        let local = [silence(2.5), tone(1.0, 0.2), silence(0.5)].concat();
        let out = duck_mix_default(&system, &local);
        let i = (3.0 * SR as f32) as usize;
        assert!((out[i] - local[i]).abs() < 1e-4);
    }

    #[test]
    fn find_cut_picks_pause_or_waits() {
        let mut x = tone(30.0, 0.3);
        let gap = (24.0 * SR as f32) as usize;
        x[gap..gap + (0.6 * SR as f32) as usize].fill(0.0);
        let cut = find_cut(&x, false, 20.0, 10.0).unwrap();
        assert!((gap..gap + (0.6 * SR as f32) as usize).contains(&cut));
        let speech = tone(30.0, 0.3);
        assert_eq!(find_cut(&speech, false, 20.0, 10.0), None);
        assert!(find_cut(&speech, true, 20.0, 10.0).is_some());
    }

    #[test]
    fn growing_wav_reads_float_and_int16() {
        let dir = std::env::temp_dir().join(format!("jiyao-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let write = |name: &str, tag: u16, bits: u16, rate: u32, body: Vec<u8>| {
            let mut v = b"RIFF\xff\xff\xff\xffWAVEfmt ".to_vec();
            let block = bits / 8;
            v.extend(16u32.to_le_bytes());
            v.extend(tag.to_le_bytes());
            v.extend(1u16.to_le_bytes());
            v.extend(rate.to_le_bytes());
            v.extend((rate * block as u32).to_le_bytes());
            v.extend(block.to_le_bytes());
            v.extend(bits.to_le_bytes());
            v.extend(b"data\xff\xff\xff\xff");
            v.extend(body);
            let p = dir.join(name);
            std::fs::write(&p, v).unwrap();
            p
        };
        let f = write(
            "f.wav",
            3,
            32,
            48000,
            (0..48000).flat_map(|_| 0.5f32.to_le_bytes()).collect(),
        );
        let mut g = GrowingWav::new(&f);
        assert!((g.seconds() - 1.0).abs() < 1e-6);
        let x = g.read(0.25, 0.75);
        assert!((x.len() as i64 - 8000).abs() <= 2);
        assert!(x[100..x.len() - 100].iter().all(|v| (v - 0.5).abs() < 1e-2));
        let i = write(
            "i.wav",
            1,
            16,
            16000,
            (0..16000).flat_map(|_| 16384i16.to_le_bytes()).collect(),
        );
        assert!(read_wav16k(&i)
            .unwrap()
            .iter()
            .all(|v| (v - 0.5).abs() < 1e-4));
        std::fs::remove_dir_all(dir).ok();
    }
}
