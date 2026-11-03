//! 单独跑 Rust 后端（不带窗口），用来测接口和配合 `pnpm dev` 开发界面：
//! cargo run --release --example serve -- <模型目录> <录音组件> [端口]
#[tokio::main]
async fn main() {
    let a: Vec<String> = std::env::args().collect();
    let opt = tingo_engine::server::Options {
        root: tingo_engine::meetings::default_root(),
        config_path: tingo_engine::config::default_path(),
        models_dir: a[1].clone().into(),
        recorder_bin: a[2].clone().into(),
        version: env!("CARGO_PKG_VERSION").into(),
    };
    let port = a.get(3).and_then(|p| p.parse().ok()).unwrap_or(0);
    let (port, server) = tingo_engine::server::serve(opt, port)
        .await
        .expect("启动服务");
    println!("TINGO_PORT={port}");
    server.await;
}
