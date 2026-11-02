//! 读 numpy 存的声纹（float32，二维），跑聚类，把编号逐行打印出来，用来和 Python 版对比。
use std::time::Instant;

fn main() {
    let path = std::env::args().nth(1).expect("npy 文件");
    let bytes = std::fs::read(path).unwrap();
    let header_len = u16::from_le_bytes([bytes[8], bytes[9]]) as usize;
    let header = std::str::from_utf8(&bytes[10..10 + header_len]).unwrap();
    let shape: Vec<usize> = header
        .split("shape': (")
        .nth(1)
        .unwrap()
        .split(')')
        .next()
        .unwrap()
        .split(',')
        .filter_map(|s| s.trim().parse().ok())
        .collect();
    assert!(header.contains("<f4"), "只支持 float32：{header}");
    let data: Vec<f32> = bytes[10 + header_len..]
        .chunks_exact(4)
        .map(|b| f32::from_le_bytes(b.try_into().unwrap()))
        .collect();
    let emb: Vec<Vec<f32>> = data.chunks(shape[1]).map(|c| c.to_vec()).collect();
    let t = Instant::now();
    let labels = jiyao_engine::cluster::cluster(&emb);
    eprintln!(
        "{} 个声纹，聚类 {:.2}s，{} 人",
        emb.len(),
        t.elapsed().as_secs_f32(),
        labels.iter().max().unwrap() + 1
    );
    for l in labels {
        println!("{l}");
    }
}
