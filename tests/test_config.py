from pathlib import Path
import pytest
from reelnote.config import load

def test_defaults_when_file_missing(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg.providers == ("claude", "codex")
    assert cfg.max_attempts == 3
    assert cfg.vault == cfg.data_repo / "vault"

def test_file_overrides(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('data_repo = "/tmp/data"\nproviders = ["codex"]\nhub_threshold = 0.8\n')
    cfg = load(p)
    assert cfg.data_repo == Path("/tmp/data")
    assert cfg.providers == ("codex",)
    assert cfg.hub_threshold == 0.8

def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('typo_key = 1\n')
    with pytest.raises(TypeError):
        load(p)

def test_providers_string_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('providers = "codex"\n')
    with pytest.raises(ValueError, match="providers must be a list of: claude, codex"):
        load(p)

def test_unknown_provider_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('providers = ["grok"]\n')
    with pytest.raises(ValueError, match="providers must be a list of: claude, codex"):
        load(p)


def test_empty_providers_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('providers = []\n')
    with pytest.raises(ValueError):
        load(p)
