//! 录音与音频文件：启停 Swift 录音组件、解码任意音视频、录完后混音并压成 m4a。
//! 全部依赖 macOS 自带能力（ScreenCaptureKit、AVFoundation、afconvert），不需要 ffmpeg。

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::time::{Duration, Instant};

use crate::asr::SR;
use crate::audio::{duck_mix_default, read_wav16k};
use crate::i18n::tr;

pub struct Recorder {
    child: Child,
    stdout: Option<BufReader<ChildStdout>>,
    pub dir: PathBuf,
    pub sources: Vec<String>,
    pub started: Instant,
}

impl Recorder {
    /// 启动录音组件，等它报告 READY（或出错）。
    pub fn start(bin: &Path, dir: &Path, system: bool, mic: bool) -> Result<Self, String> {
        if !system && !mic {
            return Err(tr(
                "系统声音和麦克风至少开一个",
                "Turn on at least one of system audio and microphone",
            )
            .into());
        }
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        let mut cmd = Command::new(bin);
        cmd.arg(dir);
        if !mic {
            cmd.arg("--no-mic");
        }
        if !system {
            cmd.arg("--no-system");
        }
        let mut child = cmd
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| {
                tr(&format!("启动录音组件失败：{e}"), &format!("Couldn't start the recorder: {e}")).to_string()
            })?;
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).map_err(|e| e.to_string())?;
        let line = line.trim();
        if line != "READY" {
            let _ = child.wait();
            return Err(line
                .strip_prefix("ERROR: ")
                .unwrap_or(tr("录音组件意外退出", "The recorder exited unexpectedly"))
                .to_string());
        }
        let sources = [("system", system), ("mic", mic)]
            .iter()
            .filter(|(_, on)| *on)
            .map(|(s, _)| s.to_string())
            .collect();
        Ok(Self {
            child,
            stdout: Some(stdout),
            dir: dir.to_path_buf(),
            sources,
            started: Instant::now(),
        })
    }

    pub fn elapsed(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }

    /// 录音组件是否中途退出了（比如权限被收回）；退出时返回它最后报的错。
    pub fn exited(&mut self) -> Option<String> {
        match self.child.try_wait() {
            Ok(Some(_)) => {
                let mut rest = String::new();
                if let Some(mut out) = self.stdout.take() {
                    let _ = std::io::Read::read_to_string(&mut out, &mut rest);
                }
                Some(
                    rest.lines()
                        .find_map(|l| l.strip_prefix("ERROR: "))
                        .unwrap_or(tr("录音组件意外退出", "The recorder exited unexpectedly"))
                        .to_string(),
                )
            }
            _ => None,
        }
    }

    /// 停止录音：发 SIGINT 让它写完文件头，15 秒内没退出就强杀。原始音轨留着。
    pub fn stop(&mut self) {
        if self.child.try_wait().ok().flatten().is_some() {
            return;
        }
        unsafe {
            libc::kill(self.child.id() as i32, libc::SIGINT);
        }
        let deadline = Instant::now() + Duration::from_secs(15);
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Recorder {
    fn drop(&mut self) {
        self.stop();
    }
}

/// 任意音视频 → 16 kHz 单声道样本（经录音组件里的 AVFoundation 解码）。
/// HTML → PDF：交给录音组件用系统 WebKit 排版（A4 分页）。
pub fn html_to_pdf(bin: &Path, html: &str, out: &Path) -> Result<(), String> {
    let tmp = std::env::temp_dir().join(format!("tingo-pdf-{}-{}.html", std::process::id(), rand_suffix()));
    std::fs::write(&tmp, html).map_err(|e| e.to_string())?;
    let res = Command::new(bin).arg("pdf").arg(&tmp).arg(out).output();
    let _ = std::fs::remove_file(&tmp);
    let res = res.map_err(|e| {
        tr(&format!("启动排版失败：{e}"), &format!("Couldn't start the PDF renderer: {e}")).to_string()
    })?;
    if res.status.success() && out.is_file() {
        return Ok(());
    }
    let msg = String::from_utf8_lossy(&res.stdout);
    Err(msg
        .lines()
        .find_map(|l| l.strip_prefix("ERROR: "))
        .unwrap_or(tr("导出 PDF 失败", "PDF export failed"))
        .to_string())
}

pub const TEXTUTIL: &str = "/usr/bin/textutil";

/// HTML → Word：macOS 自带的 textutil。没有 textutil（非 macOS）时报错。
pub fn html_to_docx(html: &str, out: &Path) -> Result<(), String> {
    if !Path::new(TEXTUTIL).is_file() {
        return Err(tr("导出 Word 只支持 macOS", "Word export is only available on macOS").into());
    }
    let tmp = std::env::temp_dir().join(format!("tingo-docx-{}-{}.html", std::process::id(), rand_suffix()));
    std::fs::write(&tmp, html).map_err(|e| e.to_string())?;
    // 不指定输入编码时，HTML 里没写 charset 的话中文会变成乱码
    let res = Command::new(TEXTUTIL)
        .args(["-inputencoding", "UTF-8", "-convert", "docx", "-output"])
        .arg(out)
        .arg(&tmp)
        .output();
    let _ = std::fs::remove_file(&tmp);
    let res = res.map_err(|e| {
        tr(&format!("启动 textutil 失败：{e}"), &format!("Couldn't start textutil: {e}")).to_string()
    })?;
    if res.status.success() && out.is_file() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&res.stderr).trim().to_string();
    Err(tr(&format!("导出 Word 失败：{err}"), &format!("Word export failed: {err}")).into())
}

/// 试录 `secs` 秒（录进临时目录，录完就删），返回每条音轨（system / mic）最响那段的音量（0～1）；
/// 音轨没录出来时是 None。录音组件起不来或中途退出时返回它报的错。
pub fn probe(bin: &Path, system: bool, mic: bool, secs: u64) -> Result<Vec<(String, Option<f32>)>, String> {
    let dir = std::env::temp_dir().join(format!("tingo-rectest-{}-{}", std::process::id(), rand_suffix()));
    let result = (|| {
        let mut rec = Recorder::start(bin, &dir, system, mic)?;
        let deadline = Instant::now() + Duration::from_secs(secs);
        while Instant::now() < deadline {
            if let Some(e) = rec.exited() {
                return Err(e);
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        rec.stop();
        Ok(rec
            .sources
            .iter()
            .map(|s| (s.clone(), crate::audio::max_level(&dir.join(format!("{s}.wav")), 0.15)))
            .collect())
    })();
    let _ = std::fs::remove_dir_all(&dir);
    result
}

/// 录音组件报的错是不是没有权限（录屏与系统录音、麦克风）
pub fn is_permission_error(msg: &str) -> bool {
    let m = msg.to_lowercase();
    ["权限", "permission", "declined", "not authorized", "not permitted", "tcc"].iter().any(|k| m.contains(k))
}

pub fn decode(bin: &Path, input: &Path) -> Result<Vec<f32>, String> {
    if input
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("wav"))
    {
        if let Some(x) = read_wav16k(input) {
            return Ok(x);
        }
    }
    let tmp = std::env::temp_dir().join(format!(
        "tingo-decode-{}-{}.wav",
        std::process::id(),
        rand_suffix()
    ));
    let out = Command::new(bin)
        .arg("decode")
        .arg(input)
        .arg(&tmp)
        .output()
        .map_err(|e| tr(&format!("启动解码失败：{e}"), &format!("Couldn't start the decoder: {e}")).to_string())?;
    let result = if out.status.success() {
        read_wav16k(&tmp).ok_or_else(|| tr("解码结果读不出来", "Couldn't read the decoded audio").to_string())
    } else {
        let msg = String::from_utf8_lossy(&out.stdout);
        Err(msg
            .lines()
            .find_map(|l| l.strip_prefix("ERROR: "))
            .unwrap_or(tr("解码失败", "Decoding failed"))
            .to_string())
    };
    let _ = std::fs::remove_file(&tmp);
    result
}

fn rand_suffix() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

/// 16 kHz 单声道 16 位 wav。
pub fn write_wav16(path: &Path, samples: &[f32]) -> std::io::Result<()> {
    let data: Vec<u8> = samples
        .iter()
        .flat_map(|s| ((s.clamp(-1.0, 1.0) * 32767.0) as i16).to_le_bytes())
        .collect();
    let mut v = Vec::with_capacity(44 + data.len());
    v.extend(b"RIFF");
    v.extend((36 + data.len() as u32).to_le_bytes());
    v.extend(b"WAVEfmt ");
    v.extend(16u32.to_le_bytes());
    v.extend(1u16.to_le_bytes());
    v.extend(1u16.to_le_bytes());
    v.extend((SR as u32).to_le_bytes());
    v.extend((SR as u32 * 2).to_le_bytes());
    v.extend(2u16.to_le_bytes());
    v.extend(16u16.to_le_bytes());
    v.extend(b"data");
    v.extend((data.len() as u32).to_le_bytes());
    v.extend(data);
    std::fs::write(path, v)
}

/// 录完后：系统声音和麦克风按"让路"规则合成一条，压成 m4a（macOS 自带 afconvert，AAC 48 kbps），删掉原始轨道。
pub fn mixdown(dir: &Path, dest: &Path) -> Result<(), String> {
    let (sys, mic) = (dir.join("system.wav"), dir.join("mic.wav"));
    let read = |p: &Path| if p.is_file() { read_wav16k(p) } else { None };
    let mixed = match (read(&sys), read(&mic)) {
        (Some(s), Some(m)) => duck_mix_default(&s, &m),
        (Some(x), None) | (None, Some(x)) => x,
        (None, None) => return Err(tr("没有录到任何声音", "Nothing was recorded").into()),
    };
    let wav = dir.join("mixed.wav");
    write_wav16(&wav, &mixed).map_err(|e| e.to_string())?;
    let out = Command::new("/usr/bin/afconvert")
        .args(["-f", "m4af", "-d", "aac", "-b", "48000"]) // 16 kHz 单声道 AAC 最高 48 kbps
        .arg(&wav)
        .arg(dest)
        .output()
        .map_err(|e| tr(&format!("压缩录音失败：{e}"), &format!("Compressing the recording failed: {e}")).to_string())?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(tr(
            &format!("压缩录音失败：{err}"),
            &format!("Compressing the recording failed: {err}"),
        )
        .into());
    }
    for p in [&wav, &sys, &mic] {
        let _ = std::fs::remove_file(p);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixdown_writes_m4a_and_cleans_tracks() {
        let dir = std::env::temp_dir().join(format!("tingo-mix-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let tone: Vec<f32> = (0..SR).map(|i| 0.3 * (i as f32 * 0.05).sin()).collect();
        write_wav16(&dir.join("system.wav"), &tone).unwrap();
        write_wav16(&dir.join("mic.wav"), &vec![0.0; SR / 2]).unwrap();
        let dest = dir.join("录音.m4a");
        mixdown(&dir, &dest).unwrap();
        assert!(std::fs::metadata(&dest).unwrap().len() > 1000);
        assert!(!dir.join("system.wav").exists() && !dir.join("mixed.wav").exists());
        std::fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn mixdown_without_audio_fails() {
        let dir = std::env::temp_dir().join(format!("tingo-mix-empty-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(mixdown(&dir, &dir.join("录音.m4a")).is_err());
        std::fs::remove_dir_all(dir).ok();
    }
}
