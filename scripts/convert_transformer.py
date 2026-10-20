"""Rename the abenzerps UC MLX-4bit transformer to mflux key names and tag it as an mflux q4 save.

Only 9 keys differ from mflux's layout; tensors are copied unchanged (no re-quantization).
"""
import sys
from pathlib import Path

import mlx.core as mx

RENAMES = {
    "modulation.0.": "modulation.layers.1.",
    "time_text_embed.linear_1.": "time_text_embed.timestep_embedder.linear_1.",
    "time_text_embed.linear_2.": "time_text_embed.timestep_embedder.linear_2.",
}


def rename(key: str) -> str:
    for old, new in RENAMES.items():
        if key.startswith(old):
            return new + key[len(old):]
    return key


src, dst = Path(sys.argv[1]), Path(sys.argv[2])
weights = mx.load(str(src))
out = {rename(k): v for k, v in weights.items()}
renamed = sum(1 for k in weights if rename(k) != k)
assert renamed == 9 and len(out) == len(weights), (renamed, len(out), len(weights))
dst.parent.mkdir(parents=True, exist_ok=True)
mx.save_safetensors(str(dst), out, metadata={"quantization_level": "4", "mflux_version": "0.23.0"})
print(f"wrote {dst} tensors={len(out)} renamed={renamed}")
