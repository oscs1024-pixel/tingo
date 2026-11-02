//! 试跑：sherpa-onnx 加载 FunASR Paraformer-large + Silero VAD + 标点 + CAM++ 声纹。
use sherpa_onnx::*;
use std::time::Instant;

fn main() {
    let home = std::env::var("HOME").unwrap();
    let m = format!("{home}/.cache/jiyao-dev/sherpa");
    let wav = Wave::read(&std::env::args().nth(1).expect("wav")).expect("read wav");
    let samples = wav.samples();
    println!(
        "音频 {:.1}s @ {}Hz",
        samples.len() as f32 / wav.sample_rate() as f32,
        wav.sample_rate()
    );

    let t = Instant::now();
    let mut cfg = OfflineRecognizerConfig::default();
    cfg.model_config.paraformer.model =
        Some(format!("{m}/funasr-paraformer-large-int8/model.int8.onnx"));
    cfg.model_config.tokens = Some(format!("{m}/funasr-paraformer-large-int8/tokens.txt"));
    cfg.model_config.num_threads = 4;
    let rec = OfflineRecognizer::create(&cfg).expect("recognizer");

    let mut vcfg = VadModelConfig::default();
    vcfg.silero_vad.model = Some(format!("{m}/silero_vad.onnx"));
    vcfg.silero_vad.threshold = 0.5;
    vcfg.silero_vad.min_silence_duration = 0.4;
    vcfg.silero_vad.min_speech_duration = 0.25;
    vcfg.silero_vad.window_size = 512;
    vcfg.silero_vad.max_speech_duration = 20.0;
    vcfg.sample_rate = 16000;
    vcfg.num_threads = 1;
    let vad = VoiceActivityDetector::create(&vcfg, 60.0).expect("vad");

    let pcfg = OfflinePunctuationConfig {
        model: OfflinePunctuationModelConfig {
            ct_transformer: Some(format!("{m}/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8/model.int8.onnx")),
            num_threads: 2,
            ..Default::default()
        },
    };
    let punct = OfflinePunctuation::create(&pcfg).expect("punct");
    let spk = SpeakerEmbeddingExtractor::create(&SpeakerEmbeddingExtractorConfig {
        model: Some(format!("{m}/campplus_zh.onnx")),
        num_threads: 2,
        ..Default::default()
    })
    .expect("speaker");
    println!("加载 {:.2}s", t.elapsed().as_secs_f32());

    let t = Instant::now();
    for chunk in samples.chunks(512) {
        vad.accept_waveform(chunk);
    }
    vad.flush();
    let mut n = 0;
    while let Some(seg) = vad.front() {
        let s = rec.create_stream();
        s.accept_waveform(16000, seg.samples());
        rec.decode(&s);
        let r = s.get_result().unwrap();
        let es = spk.create_stream().unwrap();
        es.accept_waveform(16000, seg.samples());
        es.input_finished();
        let emb = spk.compute(&es).unwrap_or_default();
        if n < 3 {
            println!(
                "段 {:.2}s~{:.2}s 文本「{}」 标点后「{}」 tokens {} 时间戳 {:?} 声纹维度 {}",
                seg.start() as f32 / 16000.0,
                (seg.start() + seg.n() as i64) as f32 / 16000.0,
                r.text,
                punct.add_punctuation(&r.text).unwrap_or_default(),
                r.tokens.len(),
                r.timestamps.as_ref().map(|t| &t[..t.len().min(6)]),
                emb.len()
            );
        }
        n += 1;
        vad.pop();
    }
    println!("共 {n} 段，识别 {:.2}s", t.elapsed().as_secs_f32());
}
