#!/bin/zsh
# One 1024x1024 generation with timing, peak memory and swap sampling.
cd "${0:A:h}/.."
mkdir -p outputs logs
ts=$(date +%Y%m%d-%H%M%S)
log=logs/run-$ts.log
{
  echo "swap before: $(sysctl -n vm.swapusage)"
  ( while true; do echo "$(date +%T) $(sysctl -n vm.swapusage) | $(memory_pressure | tail -1)"; sleep 5; done ) > logs/mem-$ts.log &
  sampler=$!
  /usr/bin/time -l .venv/bin/mflux-generate-qwen-2.1 \
    --model models/qwen-image-2.1-uc-mflux-q4 --base-model qwen-image-2.1 \
    --low-ram --width 1024 --height 1024 --seed 42 \
    --prompt "A red fox sitting in a snowy pine forest at sunrise, photorealistic, soft golden light" \
    --output outputs/test-$ts.png "$@"
  rc=$?
  kill $sampler
  echo "exit $rc"
  echo "swap after: $(sysctl -n vm.swapusage)"
} > $log 2>&1
echo $log
