import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_repo: Path
    state_dir: Path
    cookies_browser: str | None = None
    providers: tuple[str, ...] = ("claude", "codex")
    cooldown_minutes: int = 60
    dup_threshold: float = 0.92
    hub_threshold: float = 0.75
    download_gap_seconds: float = 5.0
    max_attempts: int = 3
    whisper_model: Path = Path("~/.cache/reelnote/ggml-large-v3-turbo.bin").expanduser()
    ollama_url: str = "http://localhost:11434"

    @property
    def vault(self) -> Path:
        return self.data_repo / "vault"

    @property
    def inbox(self) -> Path:
        return self.data_repo / "inbox"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "reelnote.sqlite"

    @property
    def work_dir(self) -> Path:
        return self.state_dir / "work"


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("REELNOTE_CONFIG", "~/.config/reelnote/config.toml")).expanduser()
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    data_repo = Path(raw.pop("data_repo", "~/reelnote-data")).expanduser()
    state_dir = Path(raw.pop("state_dir", "~/.local/state/reelnote")).expanduser()
    if "providers" in raw:
        names = raw["providers"]
        if not isinstance(names, list) or not names or not all(n in ("claude", "codex") for n in names):
            raise ValueError("providers must be a list of: claude, codex")
        raw["providers"] = tuple(names)
    if "whisper_model" in raw:
        raw["whisper_model"] = Path(raw["whisper_model"]).expanduser()
    return Config(data_repo=data_repo, state_dir=state_dir, **raw)
