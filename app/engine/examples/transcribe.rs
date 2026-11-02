//! 分段转写一个 16 kHz wav：jiyao-engine 的端到端检查。
//! cargo run --release --example transcribe -- <模型目录> <wav> [输出 transcript.json]
use jiyao_engine::{asr, audio, chunked};
use std::path::Path;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let t = Instant::now();
    let engine = asr::Engine::load(&asr::ModelPaths::new(&args[1]), 4).expect("加载模型");
    eprintln!("加载 {:.2}s", t.elapsed().as_secs_f64());
    let wav = Path::new(&args[2]);
    let samples = audio::read_wav16k(wav).expect("读 wav");
    let t = Instant::now();
    let tr = chunked::transcribe_samples(&engine, &samples, wav, |_| {});
    let secs = samples.len() as f64 / asr::SR as f64;
    let el = t.elapsed().as_secs_f64();
    eprintln!(
        "音频 {:.0}s，转写 {:.1}s（{:.0}x），{} 段发言，{} 位说话人",
        secs,
        el,
        secs / el,
        tr.turns().len(),
        tr.speakers_in_order().len()
    );
    if let Some(out) = args.get(3) {
        tr.save(Path::new(out)).unwrap();
    } else {
        println!("{}", tr.to_prompt_text());
    }
}
