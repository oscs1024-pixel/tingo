"""把 FunASR 官方的 Paraformer ONNX 补上 sherpa-onnx 需要的元数据，让 sherpa-onnx（含 Rust/C 接口）能直接加载。

sherpa-onnx 读的元数据：lfr_window_size / lfr_window_shift（帧拼接）、neg_mean / inv_stddev（特征归一化，
来自 am.mvn 的 AddShift / Rescale）、vocab_size；词表用 "符号 编号" 一行一个的 tokens.txt。
"""

import json
import re
import sys
from pathlib import Path

import onnx
import yaml

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)

conf = yaml.safe_load((src / "config.yaml").read_text())
fe = conf.get("frontend_conf", {})
mvn = (src / "am.mvn").read_text()


def vec(tag: str) -> list[float]:
    block = mvn.split(tag, 1)[1]
    nums = re.search(r"\[([^\]]+)\]", block).group(1)
    return [float(x) for x in nums.split()]


tokens = json.loads((src / "tokens.json").read_text(encoding="utf-8"))
(dst / "tokens.txt").write_text("".join(f"{t} {i}\n" for i, t in enumerate(tokens)), encoding="utf-8")

model = onnx.load(str(src / "model_quant.onnx"))
meta = {
    "lfr_window_size": str(fe.get("lfr_m", 7)),
    "lfr_window_shift": str(fe.get("lfr_n", 6)),
    "neg_mean": ",".join(map(str, vec("<AddShift>"))),
    "inv_stddev": ",".join(map(str, vec("<Rescale>"))),
    "vocab_size": str(len(tokens)),
    "model_type": "paraformer",
    "version": "1",
}
del model.metadata_props[:]
for k, v in meta.items():
    model.metadata_props.add(key=k, value=v)
onnx.save(model, str(dst / "model.int8.onnx"))
print("写入", dst, {k: (v if len(v) < 30 else f"{v[:20]}…({len(v.split(','))} 个)") for k, v in meta.items()})
