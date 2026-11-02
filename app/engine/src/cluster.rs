//! 说话人聚类：移植 FunASR（来自 SpeechBrain）的谱聚类 + 按余弦合并。
//!
//! 和 Python 版一样，声纹多时均匀抽 `LIMIT` 个做谱聚类，其余按余弦相似度归到最近的说话人：
//! FunASR 在声纹 ≥ 2048 个时会改用 UMAP+HDBSCAN，又慢又容易把一个人拆成几个。

use faer::{Mat, Side};

const MIN_SPKS: usize = 1;
const MAX_SPKS: usize = 15;
const PVAL: f64 = 0.022;
const MERGE_THR: f32 = 0.78;
pub const LIMIT: usize = 1500;

/// 每个声纹的说话人编号（0 开始，按首次出现的先后不保证有序）。
pub fn cluster(emb: &[Vec<f32>]) -> Vec<usize> {
    let n = emb.len();
    if n < 20 {
        return vec![0; n];
    }
    let unit: Vec<Vec<f32>> = emb.iter().map(|e| normalize(e)).collect();
    let labels = if n <= LIMIT {
        spectral(&unit)
    } else {
        let idx: Vec<usize> = (0..LIMIT)
            .map(|i| (i * (n - 1) + (LIMIT - 1) / 2) / (LIMIT - 1))
            .collect();
        let sub: Vec<Vec<f32>> = idx.iter().map(|&i| unit[i].clone()).collect();
        let sub_labels = spectral(&sub);
        let k = sub_labels.iter().max().map_or(0, |m| m + 1);
        let centers: Vec<Vec<f32>> = (0..k)
            .map(|c| {
                normalize(&mean(
                    sub.iter()
                        .zip(&sub_labels)
                        .filter(|(_, &l)| l == c)
                        .map(|(e, _)| e.as_slice()),
                ))
            })
            .collect();
        unit.iter()
            .map(|e| argmax(centers.iter().map(|c| dot(e, c))))
            .collect()
    };
    merge_by_cos(labels, emb, MERGE_THR)
}

fn spectral(unit: &[Vec<f32>]) -> Vec<usize> {
    let n = unit.len();
    // 余弦相似度
    let mut a = Mat::<f64>::from_fn(n, n, |i, j| dot(&unit[i], &unit[j]) as f64);
    // p 剪枝：每行只留最相似的一小部分
    let pval = if (n as f64) * PVAL < 6.0 {
        6.0 / n as f64
    } else {
        PVAL
    };
    let n_zero = ((1.0 - pval) * n as f64) as usize;
    for i in 0..n {
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&x, &y| a[(i, x)].total_cmp(&a[(i, y)]));
        for &j in &order[..n_zero] {
            a[(i, j)] = 0.0;
        }
    }
    // 对称化、去对角、拉普拉斯矩阵 L = D - A
    let sym = Mat::<f64>::from_fn(n, n, |i, j| {
        if i == j {
            0.0
        } else {
            0.5 * (a[(i, j)] + a[(j, i)])
        }
    });
    let degree: Vec<f64> = (0..n)
        .map(|i| (0..n).map(|j| sym[(i, j)].abs()).sum())
        .collect();
    let lap = Mat::<f64>::from_fn(n, n, |i, j| if i == j { degree[i] } else { -sym[(i, j)] });
    let evd = lap
        .self_adjoint_eigen(Side::Lower)
        .expect("拉普拉斯矩阵特征分解失败");
    let s = evd.S().column_vector();
    let u = evd.U();
    // 特征值从小到大；看前 MAX_SPKS+1 个之间的间隔，最大的间隔处就是说话人数
    let top = (MAX_SPKS + 1).min(n);
    let lambdas: Vec<f64> = (MIN_SPKS - 1..top).map(|i| s[i]).collect();
    let gaps: Vec<f64> = lambdas.windows(2).map(|w| w[1] - w[0]).collect();
    let k = argmax(gaps.iter().copied()) + MIN_SPKS;
    let spec: Vec<Vec<f64>> = (0..n)
        .map(|i| (0..k).map(|j| u[(i, j)]).collect())
        .collect();
    kmeans(&spec, k)
}

/// k-means（k-means++ 初始化，固定随机种子，跑 10 次取最好）：结果可复现。
fn kmeans(x: &[Vec<f64>], k: usize) -> Vec<usize> {
    if k <= 1 {
        return vec![0; x.len()];
    }
    let mut rng = 0x9E37_79B9_7F4A_7C15_u64;
    let mut next = || {
        rng ^= rng << 13;
        rng ^= rng >> 7;
        rng ^= rng << 17;
        (rng >> 11) as f64 / (1u64 << 53) as f64
    };
    let dist = |a: &[f64], b: &[f64]| a.iter().zip(b).map(|(p, q)| (p - q) * (p - q)).sum::<f64>();
    let mut best = (f64::INFINITY, vec![0; x.len()]);
    for _ in 0..10 {
        let mut centers = vec![x[(next() * x.len() as f64) as usize % x.len()].clone()];
        while centers.len() < k {
            let d: Vec<f64> = x
                .iter()
                .map(|p| {
                    centers
                        .iter()
                        .map(|c| dist(p, c))
                        .fold(f64::INFINITY, f64::min)
                })
                .collect();
            let total: f64 = d.iter().sum();
            let mut r = next() * total;
            let mut pick = x.len() - 1;
            for (i, di) in d.iter().enumerate() {
                r -= di;
                if r <= 0.0 {
                    pick = i;
                    break;
                }
            }
            centers.push(x[pick].clone());
        }
        let mut labels = vec![0; x.len()];
        for _ in 0..100 {
            let mut changed = false;
            for (i, p) in x.iter().enumerate() {
                let l = argmin(centers.iter().map(|c| dist(p, c)));
                changed |= l != labels[i];
                labels[i] = l;
            }
            for (c, center) in centers.iter_mut().enumerate() {
                let members: Vec<&Vec<f64>> = x
                    .iter()
                    .zip(&labels)
                    .filter(|(_, &l)| l == c)
                    .map(|(p, _)| p)
                    .collect();
                if !members.is_empty() {
                    *center = (0..center.len())
                        .map(|d| members.iter().map(|m| m[d]).sum::<f64>() / members.len() as f64)
                        .collect();
                }
            }
            if !changed {
                break;
            }
        }
        let inertia: f64 = x
            .iter()
            .zip(&labels)
            .map(|(p, &l)| dist(p, &centers[l]))
            .sum();
        if inertia < best.0 {
            best = (inertia, labels);
        }
    }
    best.1
}

/// 质心余弦相似度超过阈值的两个说话人合并成一个，反复直到没有可合并的。
fn merge_by_cos(mut labels: Vec<usize>, emb: &[Vec<f32>], thr: f32) -> Vec<usize> {
    loop {
        let k = labels.iter().max().map_or(0, |m| m + 1);
        if k <= 1 {
            return labels;
        }
        let centers: Vec<Vec<f32>> = (0..k)
            .map(|c| {
                normalize(&mean(
                    emb.iter()
                        .zip(&labels)
                        .filter(|(_, &l)| l == c)
                        .map(|(e, _)| e.as_slice()),
                ))
            })
            .collect();
        let mut best = (f32::NEG_INFINITY, 0, 0);
        for i in 0..k {
            for j in i + 1..k {
                let s = dot(&centers[i], &centers[j]);
                if s > best.0 {
                    best = (s, i, j);
                }
            }
        }
        if best.0 < thr {
            return labels;
        }
        let (keep, drop) = (best.1, best.2);
        for l in labels.iter_mut() {
            if *l == drop {
                *l = keep;
            } else if *l > drop {
                *l -= 1;
            }
        }
    }
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

fn normalize(v: &[f32]) -> Vec<f32> {
    let n = dot(v, v).sqrt().max(1e-9);
    v.iter().map(|x| x / n).collect()
}

fn mean<'a>(rows: impl Iterator<Item = &'a [f32]>) -> Vec<f32> {
    let mut sum: Vec<f32> = Vec::new();
    let mut count = 0;
    for r in rows {
        if sum.is_empty() {
            sum = vec![0.0; r.len()];
        }
        for (s, x) in sum.iter_mut().zip(r) {
            *s += x;
        }
        count += 1;
    }
    sum.iter().map(|s| s / count.max(1) as f32).collect()
}

fn argmax<T: PartialOrd>(it: impl Iterator<Item = T>) -> usize {
    let mut best: Option<(usize, T)> = None;
    for (i, v) in it.enumerate() {
        if best.as_ref().is_none_or(|(_, b)| v > *b) {
            best = Some((i, v));
        }
    }
    best.map_or(0, |(i, _)| i)
}

fn argmin(it: impl Iterator<Item = f64>) -> usize {
    argmax(it.map(|v| -v))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// k 个说话人，每人 n 个带噪声的声纹。
    fn speakers(k: usize, n: usize, noise: f32) -> (Vec<Vec<f32>>, Vec<usize>) {
        let mut seed = 42u64;
        let mut rnd = move || {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((seed >> 33) as f32 / (1u64 << 31) as f32) - 0.5
        };
        let centers: Vec<Vec<f32>> = (0..k).map(|_| (0..192).map(|_| rnd()).collect()).collect();
        let mut emb = Vec::new();
        let mut truth = Vec::new();
        for i in 0..k * n {
            let c = i % k;
            emb.push(centers[c].iter().map(|x| x + noise * rnd()).collect());
            truth.push(c);
        }
        (emb, truth)
    }

    /// 两种编号是否描述同一个划分（不要求编号相同）。
    fn same_partition(a: &[usize], b: &[usize]) -> bool {
        let mut map = std::collections::HashMap::new();
        a.iter()
            .zip(b)
            .all(|(x, y)| *map.entry(*x).or_insert(*y) == *y)
            && {
                let mut rev = std::collections::HashMap::new();
                b.iter()
                    .zip(a)
                    .all(|(x, y)| *rev.entry(*x).or_insert(*y) == *y)
            }
    }

    #[test]
    fn few_voiceprints_are_one_speaker() {
        let (emb, _) = speakers(3, 5, 0.1);
        assert_eq!(cluster(&emb), vec![0; 15]);
    }

    #[test]
    fn finds_speaker_count_and_groups() {
        for k in [2, 3, 5] {
            let (emb, truth) = speakers(k, 40, 0.3);
            let labels = cluster(&emb);
            assert!(same_partition(&labels, &truth), "k={k}");
        }
    }

    #[test]
    fn large_input_subsamples_then_assigns() {
        let (emb, truth) = speakers(4, 800, 0.3); // 3200 个，超过 LIMIT
        let labels = cluster(&emb);
        assert!(same_partition(&labels, &truth));
    }

    #[test]
    fn near_identical_speakers_are_merged() {
        let mut emb: Vec<Vec<f32>> = speakers(1, 60, 0.05).0;
        let tweak: Vec<Vec<f32>> = emb
            .iter()
            .map(|e| e.iter().map(|x| x * 1.01).collect())
            .collect();
        let labels = merge_by_cos(
            [vec![0; 60], vec![1; 60]].concat(),
            &[emb.clone(), tweak].concat(),
            MERGE_THR,
        );
        assert!(labels.iter().all(|&l| l == 0));
        emb.truncate(1);
    }
}
