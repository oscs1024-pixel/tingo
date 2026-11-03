//! 用本机已下载的 FunASR 官方文件测试转换：cargo run --example prepare -- <官方目录> <输出模型目录>
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let src = std::path::Path::new(&a[1]);
    let tmp = std::env::temp_dir().join("tingo-prepare");
    std::fs::create_dir_all(&tmp).unwrap();
    std::fs::copy(src.join("model_quant.onnx"), tmp.join("paraformer.onnx")).unwrap();
    std::fs::copy(src.join("am.mvn"), tmp.join("am.mvn")).unwrap();
    std::fs::copy(src.join("tokens.json"), tmp.join("tokens.json")).unwrap();
    tingo_engine::models::prepare_paraformer(
        &tmp,
        std::path::Path::new(&a[2]).join("paraformer").as_path(),
    )
    .unwrap();
    println!("ok");
}
