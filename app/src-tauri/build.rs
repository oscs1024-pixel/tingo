use std::path::Path;
use std::process::Command;

fn main() {
    // 把 Swift 录音组件（录系统声音、麦克风，解码音视频）编译好，作为资源打进 App
    let src = Path::new("../../src/tingo/recorder.swift");
    let out = Path::new("bin/tingo-recorder");
    println!("cargo:rerun-if-changed={}", src.display());
    std::fs::create_dir_all("bin").unwrap();
    let fresh = out.exists()
        && out.metadata().and_then(|m| m.modified()).ok()
            >= src.metadata().and_then(|m| m.modified()).ok();
    if !fresh {
        let status = Command::new("swiftc")
            .args(["-O", src.to_str().unwrap(), "-o", out.to_str().unwrap()])
            .status()
            .expect("需要 Xcode Command Line Tools（swiftc）来编译录音组件");
        assert!(status.success(), "录音组件编译失败");
    }
    tauri_build::build()
}
