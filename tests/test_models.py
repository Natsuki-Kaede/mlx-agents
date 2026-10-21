"""Each chat model's recommended sampling: its own generation_config.json first, then its family's values."""

import json

from backend import models


def test_shipped_values_win_over_family_and_defaults(tmp_path):
    (tmp_path / "generation_config.json").write_text(json.dumps({"temperature": 0.3, "top_k": 40, "do_sample": True}))
    assert models.recommended_sampling(tmp_path, "qwen3_5") == {"temperature": 0.3, "top_p": 0.95, "top_k": 40, "repetition_penalty": 1.0}


def test_family_values_without_a_generation_config(tmp_path):
    assert models.recommended_sampling(tmp_path, "gemma4_unified")["top_k"] == 64
    assert models.recommended_sampling(tmp_path, "qwen3_5")["temperature"] == 0.6
    assert models.recommended_sampling(tmp_path, "llama") == models.DEFAULT_SAMPLING
