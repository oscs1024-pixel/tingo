//! 录音与音频文件：启停 Swift 录音组件、解码任意音视频、录完后混音并压成 m4a。
//! 全部依赖 macOS 自带能力（ScreenCaptureKit、AVFoundation、afconvert），不需要 ffmpeg。

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::time::{Duration, Instant};

use crate::asr::SR;
use crate::audio::{duck_mix_default, read_wav16k};

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
            return Err("系统声音和麦克风至少开一个".into());
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
            .map_err(|e| format!("启动录音组件失败：{e}"))?;
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).map_err(|e| e.to_string())?;
        let line = line.trim();
        if line != "READY" {
            let _ = child.wait();
            return Err(line
                .strip_prefix("ERROR: ")
                .unwrap_or("录音组件意外退出")
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
                        .unwrap_or("录音组件意外退出")
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
        "jiyao-decode-{}-{}.wav",
        std::process::id(),
        rand_suffix()
    ));
    let out = Command::new(bin)
        .arg("decode")
        .arg(input)
        .arg(&tmp)
        .output()
        .map_err(|e| format!("启动解码失败：{e}"))?;
    let result = if out.status.success() {
        read_wav16k(&tmp).ok_or_else(|| "解码结果读不出来".to_string())
    } else {
        let msg = String::from_utf8_lossy(&out.stdout);
        Err(msg
            .lines()
            .find_map(|l| l.strip_prefix("ERROR: "))
            .unwrap_or("解码失败")
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
        (None, None) => return Err("没有录到任何声音".into()),
    };
    let wav = dir.join("mixed.wav");
    write_wav16(&wav, &mixed).map_err(|e| e.to_string())?;
    let out = Command::new("/usr/bin/afconvert")
        .args(["-f", "m4af", "-d", "aac", "-b", "48000"]) // 16 kHz 单声道 AAC 最高 48 kbps
        .arg(&wav)
        .arg(dest)
        .output()
        .map_err(|e| format!("压缩录音失败：{e}"))?;
    if !out.status.success() {
        return Err(format!(
            "压缩录音失败：{}",
            String::from_utf8_lossy(&out.stderr)
        ));
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
        let dir = std::env::temp_dir().join(format!("jiyao-mix-{}", std::process::id()));
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
        let dir = std::env::temp_dir().join(format!("jiyao-mix-empty-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(mixdown(&dir, &dir.join("录音.m4a")).is_err());
        std::fs::remove_dir_all(dir).ok();
    }
}
