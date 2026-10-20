#!/bin/zsh
# Resumable downloads: abenzerps uncensored MLX-4bit transformer + mflux-q4 companions (no transformer shards).
set -e
cd "${0:A:h}/.."
get() { # repo path dest
  mkdir -p "${3:h}"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$3" "https://huggingface.co/$1/resolve/main/$2"
}
get abenzerps/Qwen-Image-2.1-Uncensored-Quantized qwen-image-2.1-UC-MLX-4bit.safetensors dl/abenzerps/qwen-image-2.1-UC-MLX-4bit.safetensors
get abenzerps/Qwen-Image-2.1-Uncensored-Quantized SHA256SUMS dl/abenzerps/SHA256SUMS
R=mlx-community/Qwen-Image-2.1-mflux-q4
for f in $(curl -s "https://huggingface.co/api/models/$R" | .venv/bin/python -I -c "import json,sys;print('\n'.join(s['rfilename'] for s in json.load(sys.stdin)['siblings'] if not (s['rfilename'].startswith('transformer/') and s['rfilename'].endswith('.safetensors'))))"); do
  get $R "$f" "dl/mflux-q4/$f"
done
echo FETCH_OK
