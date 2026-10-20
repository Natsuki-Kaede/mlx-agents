#!/bin/zsh
# Resumable download of the uncensored Qwen3.5-9B MLX 4-bit VLM.
set -e
cd "${0:A:h}/.."
R=vanch007/Huihui-Qwen3.5-9B-abliterated-mlx-4bit
D=models/qwen3.5-9b-abliterated-mlx-4bit
for f in $(curl -s "https://huggingface.co/api/models/$R" | .venv/bin/python -I -c "import json,sys;print('\n'.join(s['rfilename'] for s in json.load(sys.stdin)['siblings']))"); do
  mkdir -p "$D/${f:h}"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$D/$f" "https://huggingface.co/$R/resolve/main/$f"
done
echo FETCH_OK
