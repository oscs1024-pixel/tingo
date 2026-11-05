//! Bridge to the bundled Swift helper. System assets are never deleted by Tingo.
use std::{path::{Path, PathBuf}, process::{Command, Stdio}, io::Read, time::{Duration, Instant}};
use serde::Deserialize;

#[derive(Deserialize)]
pub struct Status { pub available: bool, pub ready: bool }
#[derive(Deserialize)]
pub struct Span { pub start: f64, pub end: f64, pub text: String }

pub fn run(bin: &Path, action: &str, language: &str, audio: Option<&Path>) -> Result<String, String> {
    let mut cmd = Command::new(bin);
    cmd.args([action, language]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
    if let Some(p) = audio { cmd.arg(p); }
    let mut child = cmd.spawn().map_err(|e| format!("Apple speech: {e}"))?;
    let mut stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || { let mut s = String::new(); stdout.read_to_string(&mut s).map(|_| s) });
    let deadline = Instant::now() + Duration::from_secs(if action == "speech-install" { 900 } else { 120 });
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            other => {
                let _ = child.kill(); let _ = child.wait(); let _ = reader.join();
                return Err(format!("Apple speech timed out or exited: {other:?}"));
            }
        }
    };
    let text = reader.join().map_err(|_| "Apple speech reader failed")?.map_err(|e| e.to_string())?;
    if !status.success() { return Err(text.trim().strip_prefix("ERROR: ").unwrap_or(text.trim()).to_string()); }
    Ok(text)
}
pub fn status(bin: &Path, language: &str) -> Result<Status, String> {
    serde_json::from_str(&run(bin, "speech-status", language, None)?).map_err(|e| e.to_string())
}
struct Temp(PathBuf);
impl Drop for Temp { fn drop(&mut self) { let _ = std::fs::remove_file(&self.0); } }
pub fn transcribe(bin: &Path, language: &str, samples: &[f32]) -> Result<Vec<Span>, String> {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let p = Temp(std::env::temp_dir().join(format!("tingo-apple-{}-{}.wav", std::process::id(), NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed))));
    crate::record::write_wav16(&p.0, samples).map_err(|e| e.to_string())?;
    let spans: Vec<Span> = serde_json::from_str(&run(bin, "speech-transcribe", language, Some(&p.0))?).map_err(|e| e.to_string())?;
    let duration = samples.len() as f64 / crate::asr::SR as f64;
    if spans.iter().any(|s| !s.start.is_finite() || !s.end.is_finite() || s.start < 0.0 || s.end < s.start || s.end > duration + 0.5) { return Err("Apple speech returned invalid timestamps".into()); }
    Ok(spans)
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    fn helper(script: &str) -> Temp {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let p = Temp(std::env::temp_dir().join(format!("tingo-apple-test-{}-{}", std::process::id(), NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed))));
        std::fs::write(&p.0, format!("#!/bin/sh\n{script}\n")).unwrap();
        std::fs::set_permissions(&p.0, std::fs::Permissions::from_mode(0o700)).unwrap(); p
    }
    #[test]
    fn missing_assets_are_not_ready() {
        let bin = helper("echo '{\"available\":true,\"ready\":false}'");
        let s = status(&bin.0, "en").unwrap(); assert!(s.available); assert!(!s.ready);
    }
    #[test]
    fn failures_reach_the_caller() {
        let bin = helper("echo 'ERROR: system speech is unavailable'; exit 1");
        assert_eq!(run(&bin.0, "speech-status", "zh", None).unwrap_err(), "system speech is unavailable");
    }
    #[test]
    fn out_of_range_audio_is_rejected() {
        let bin = helper("echo '[{\"start\":0,\"end\":99,\"text\":\"bad\"}]'");
        assert!(transcribe(&bin.0, "zh", &[0.0; 16000]).is_err());
    }
}
