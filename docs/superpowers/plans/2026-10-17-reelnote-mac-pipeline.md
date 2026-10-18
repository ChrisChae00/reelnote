# reelnote Mac Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `reelnote run`. It pulls the private data repo, turns each inbox item into an Obsidian note grouped into topic hubs, and pushes the result.

**Architecture:** A Python package under `src/reelnote/` with one module per responsibility. The modules cover: shortcode, inbox parsing, categories, SQLite state, yt-dlp fetch, extraction, the LLM provider chain, embeddings, grouping, vault writing, data-repo git sync, the pipeline, and the CLI. Every external tool is called through a function that `pipeline.Deps` can replace with a fake, so the whole pipeline runs in tests with no network, AI, or git remote.

**Tech Stack:** Python ≥3.12 managed with `uv`. Dependencies: `pyyaml`, `trafilatura`, `pypdf`, `ocrmac`, `numpy`, `scikit-learn`; `pytest` for dev. External tools: `yt-dlp`, `ffmpeg`, `whisper-cli` (whisper.cpp), and Ollama with `bge-m3`. AI CLIs: `claude`, `codex`, `grok`.

**Spec:** `docs/superpowers/specs/2026-10-17-reelnote-design.md`

This is plan A of two. Plan B (the phone share UI in Scriptable) is written after this plan's Task 0 spike and the Scriptable spike.

## Global Constraints

- **AI:** only subscription-login CLIs, in order: `claude -p`, then `codex exec`, then `grok -p`. No Gemini, no xAI API, no paid API keys.
- **AI working directory:** every AI CLI runs in an empty temporary directory, so an agentic CLI cannot touch any repo.
- **Secrets and personal data:** never written into this public repo. That includes cookies, tokens, inbox items, vault notes, the SQLite file, and media.
- **Data repo layout** (verbatim from spec §6): `vault/categories.md`, `vault/notes/<YYYY-MM-DD> <title>.md`, `vault/hubs/<topic>.md`, `vault/review.md`, `vault/.reelnote/category-counts.json`, `inbox/<id>.json`.
- **Note frontmatter keys** (verbatim): `shortcode`, `url`, `category`, `category_source` (`manual` | `auto`), `hub`, `status` (`done` | `review` | `unavailable`), `dm_links`, `saved_at`. One addition: `duplicate_of`, used for duplicate candidates.
- **Hub markers:** `<!-- reelnote:start -->` and `<!-- reelnote:end -->`. Text outside them is never modified.
- **`category_source: manual` is never overwritten,** except when its category is deleted.
- **Retries:** an item is attempted 3 times. It is then marked `failed`, its inbox file moves to `inbox/failed/`, and it is listed in `review.md`.
- **Download spacing:** at least `download_gap_seconds` (default 5) between `yt-dlp` downloads.
- **Push conflicts:** on a `categories.md` conflict, the phone's version wins.
- **Schedule:** `launchd` runs at login and every 15 minutes (`StartInterval` 900).

## Review Focus

1. **The same video sent twice before the first finishes** should produce one note with both memos. The test is in Task 10: `test_second_item_same_shortcode_merges_before_written`.
2. **A note edited by hand in Obsidian, then a later memo or DM link for the same video**, should leave the edits intact and append the new content. Tests are in Task 8: `test_append_preserves_hand_edits` and `test_update_frontmatter_keeps_unknown_keys`.
3. **All three AI providers at their limit** should leave the item `extracted`, not count a failed attempt, and keep the inbox file. The test is in Task 10: `test_all_providers_limited_keeps_item`.
4. **The phone pushes while the Mac is mid-run** should end with the Mac's push succeeding after a rebase, with the phone's `categories.md` winning. The test is in Task 9: `test_push_after_concurrent_phone_commit`.
5. **Titles with `/`, `:`, `#`, or `[[`, or two notes with the same title on the same day,** should give a valid, unique file name. The test is in Task 8: `test_note_filename_sanitizes_and_dedupes`.

---

## File Structure

```
pyproject.toml
src/reelnote/__init__.py
src/reelnote/config.py        Config dataclass + load() from ~/.config/reelnote/config.toml
src/reelnote/shortcode.py     URL -> shortcode, canonical URL
src/reelnote/inbox.py         SaveItem / RenameOp + parse_item()
src/reelnote/categories.py    parse / removed / apply_rename for categories.md
src/reelnote/store.py         SQLite: items, embeddings, cooldowns, meta
src/reelnote/fetch.py         yt-dlp wrapper
src/reelnote/extract.py       whisper transcript, frame OCR, line dedupe, DM page text
src/reelnote/llm.py           prompts, schemas, provider chain, JSON extraction
src/reelnote/embed.py         Ollama embeddings + cosine
src/reelnote/organize.py      match() for one note, regroup clustering
src/reelnote/vault.py         note/hub/review/counts writing, frontmatter edits
src/reelnote/datarepo.py      git pull/commit/push with rebase retry
src/reelnote/pipeline.py      Deps, process_save(), run_once()
src/reelnote/cli.py           reelnote run | regroup | status
launchd/com.reelnote.run.plist
tests/...                     one test file per module + tests/test_pipeline.py
docs/notes/spike-cli-output.md   findings from Task 0
```

---

### Task 0: Spike — verify yt-dlp and AI CLI output shapes (throwaway)

This task produces findings, not product code. The code later in this plan assumes the results below. Change those assumptions only if the spike proves them wrong.

**Files:**
- Create: `docs/notes/spike-cli-output.md`

- [ ] **Step 1: Install tools**

```bash
brew install yt-dlp whisper-cpp
ollama serve >/dev/null 2>&1 &   # or open the Ollama app
ollama pull bge-m3
mkdir -p ~/.cache/reelnote
curl -L -o ~/.cache/reelnote/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
```

- [ ] **Step 2: Download one public Reel, first without cookies and then with them**

```bash
mkdir -p /tmp/rn-spike && cd /tmp/rn-spike
yt-dlp --no-playlist --write-info-json -f "mp4/best" -o "%(id)s.%(ext)s" "<public reel url>"
yt-dlp --no-playlist --write-info-json -f "mp4/best" -o "%(id)s.%(ext)s" --cookies-from-browser chrome "<public reel url>"
python3 -c "import json,glob;d=json.load(open(glob.glob('*.info.json')[0]));print(d.get('id'),d.get('uploader'),d.get('channel'),repr(d.get('description'))[:200])"
```

Record in the notes file:
- Did each command succeed? Quote the exact error line if one failed.
- What is the `id` field (it should equal the shortcode)?
- Which info.json fields hold the caption (`description`) and the author (`uploader` or `channel`)?

- [ ] **Step 3: Check the stdout envelope of each AI CLI**

```bash
cd "$(mktemp -d)"
S='{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}'
claude -p 'Return JSON with a title for a note about Raycast.' --output-format json --json-schema "$S" | head -c 1500; echo
echo "$S" > s.json; codex exec --skip-git-repo-check --output-schema s.json -o out.txt 'Return JSON with a title for a note about Raycast.' >/dev/null; cat out.txt; echo
grok -p 'Return JSON with a title for a note about Raycast.' --json-schema "$S" --disable-web-search | head -c 1500; echo
```

Record for each CLI the key path where the JSON object appears (for example `structured_output`, `result`, or the top-level object). `llm.extract_json` (Task 6) walks the keys `structured_output`, `result`, `output`, `text`, `content`, `message`, and `response`. If a CLI uses any other key, add it to `_ENVELOPE_KEYS` in Task 6.

- [ ] **Step 4: Record each CLI's usage-limit message, if known**

Search each CLI's docs or changelog (`~/.grok/CHANGELOG.md`, `claude --help`, `codex --help`) for the usage-limit message. Add the wording to the notes. `LIMIT_PATTERNS` in Task 6 matches: `usage limit`, `rate limit`, `limit reached`, `quota`, `too many requests`, `429`, `hit your limit`.

- [ ] **Step 5: Commit the notes**

```bash
git add docs/notes/spike-cli-output.md
git commit -m "docs: record yt-dlp and AI CLI output spike findings"
```

---

### Task 1: Project scaffold, config, shortcode

**Files:**
- Create: `pyproject.toml`, `src/reelnote/__init__.py`, `src/reelnote/config.py`, `src/reelnote/shortcode.py`
- Test: `tests/test_shortcode.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.Config` (frozen dataclass) with fields `data_repo: Path`, `state_dir: Path`, `cookies_browser: str | None`, `providers: tuple[str, ...]`, `cooldown_minutes: int`, `dup_threshold: float`, `hub_threshold: float`, `download_gap_seconds: float`, `max_attempts: int`, `whisper_model: Path`, `ollama_url: str`, and properties `vault`, `inbox`, `db_path`, `work_dir`
  - `config.load(path: Path | None = None) -> Config`
  - `shortcode.shortcode(url: str) -> str`, which raises `shortcode.NotInstagramPost`
  - `shortcode.canonical_url(code: str) -> str`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "reelnote"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pyyaml>=6.0",
  "trafilatura>=1.12",
  "pypdf>=5.0",
  "ocrmac>=1.0",
  "numpy>=1.26",
  "scikit-learn>=1.5",
]

[project.scripts]
reelnote = "reelnote.cli:main"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reelnote"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/reelnote/__init__.py` is an empty file.

- [ ] **Step 2: Write failing tests**

`tests/test_shortcode.py`:

```python
import pytest
from reelnote.shortcode import shortcode, canonical_url, NotInstagramPost

@pytest.mark.parametrize("url", [
    "https://www.instagram.com/reel/C8xYz2Qp/",
    "https://www.instagram.com/reel/C8xYz2Qp/?igsh=MWx0b2N3",
    "https://instagram.com/reels/C8xYz2Qp",
    "https://www.instagram.com/p/C8xYz2Qp/",
    "https://m.instagram.com/reel/C8xYz2Qp/",
    "https://www.instagram.com/some.creator/reel/C8xYz2Qp/",
    "  https://www.instagram.com/reel/C8xYz2Qp/  ",
])
def test_variants_share_one_shortcode(url):
    assert shortcode(url) == "C8xYz2Qp"

@pytest.mark.parametrize("url", [
    "https://www.instagram.com/some.creator/",
    "https://example.com/reel/C8xYz2Qp/",
    "ftp://www.instagram.com/reel/C8xYz2Qp/",
    "not a url",
])
def test_rejects_non_posts(url):
    with pytest.raises(NotInstagramPost):
        shortcode(url)

def test_canonical_url():
    assert canonical_url("C8xYz2Qp") == "https://www.instagram.com/p/C8xYz2Qp/"
```

`tests/test_config.py`:

```python
from pathlib import Path
import pytest
from reelnote.config import load

def test_defaults_when_file_missing(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg.providers == ("claude", "codex", "grok")
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv sync && uv run pytest tests/test_shortcode.py tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reelnote.shortcode'`

- [ ] **Step 4: Implement**

`src/reelnote/shortcode.py`:

```python
import re
from urllib.parse import urlparse

_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)/?")


class NotInstagramPost(ValueError):
    pass


def shortcode(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or u.netloc.lower() not in _HOSTS:
        raise NotInstagramPost(url)
    m = _PATH.match(u.path)
    if not m:
        raise NotInstagramPost(url)
    return m.group(1)


def canonical_url(code: str) -> str:
    # /p/ opens both reels and photo posts
    return f"https://www.instagram.com/p/{code}/"
```

`src/reelnote/config.py`:

```python
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_repo: Path
    state_dir: Path
    cookies_browser: str | None = None
    providers: tuple[str, ...] = ("claude", "codex", "grok")
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
        raw["providers"] = tuple(raw["providers"])
    if "whisper_model" in raw:
        raw["whisper_model"] = Path(raw["whisper_model"]).expanduser()
    return Config(data_repo=data_repo, state_dir=state_dir, **raw)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_shortcode.py tests/test_config.py -v`
Expected: PASS (15 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src tests
git commit -m "feat: add project scaffold, config, and shortcode parsing"
```

---

### Task 2: Inbox item parsing

**Files:**
- Create: `src/reelnote/inbox.py`
- Test: `tests/test_inbox.py`

**Interfaces:**
- Consumes: `shortcode.shortcode`, `shortcode.NotInstagramPost`
- Produces:
  - `inbox.SaveItem` (frozen dataclass): `id: str`, `video_url: str`, `dm_url: str | None`, `memo: str | None`, `category: str`, `created_at: str`
  - `inbox.RenameOp` (frozen dataclass): `id: str`, `old: str`, `new: str`
  - `inbox.parse_item(path: Path) -> SaveItem | RenameOp`, which raises `inbox.InvalidInboxItem`
  - `inbox.AUTO = "auto"`

- [ ] **Step 1: Write failing tests**

`tests/test_inbox.py`:

```python
import json
import pytest
from reelnote.inbox import parse_item, SaveItem, RenameOp, InvalidInboxItem

def write(tmp_path, data, name="20261017-183012-a1f3.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False))
    return p

GOOD = {"op": "save", "video_url": "https://www.instagram.com/reel/C8xYz2Qp/",
        "dm_url": None, "memo": None, "category": "생산성",
        "created_at": "2026-10-17T18:30:12+09:00"}

def test_save_item(tmp_path):
    item = parse_item(write(tmp_path, GOOD))
    assert item == SaveItem(id="20261017-183012-a1f3", video_url=GOOD["video_url"], dm_url=None,
                            memo=None, category="생산성", created_at=GOOD["created_at"])

def test_rename_op(tmp_path):
    item = parse_item(write(tmp_path, {"op": "rename_category", "from": "생산성", "to": "업무툴"}))
    assert item == RenameOp(id="20261017-183012-a1f3", old="생산성", new="업무툴")

@pytest.mark.parametrize("patch", [
    {"video_url": "https://example.com/x"},
    {"video_url": None},
    {"dm_url": "javascript:alert(1)"},
    {"category": ""},
    {"memo": 5},
    {"op": "explode"},
])
def test_invalid(tmp_path, patch):
    with pytest.raises(InvalidInboxItem):
        parse_item(write(tmp_path, {**GOOD, **patch}))

def test_invalid_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{not json")
    with pytest.raises(InvalidInboxItem):
        parse_item(p)

def test_rename_same_name_invalid(tmp_path):
    with pytest.raises(InvalidInboxItem):
        parse_item(write(tmp_path, {"op": "rename_category", "from": "a", "to": "a"}))

def test_blank_memo_becomes_none(tmp_path):
    assert parse_item(write(tmp_path, {**GOOD, "memo": "   "})).memo is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reelnote.inbox'`

- [ ] **Step 3: Implement**

`src/reelnote/inbox.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from reelnote.shortcode import NotInstagramPost, shortcode

AUTO = "auto"


class InvalidInboxItem(ValueError):
    pass


@dataclass(frozen=True)
class SaveItem:
    id: str
    video_url: str
    dm_url: str | None
    memo: str | None
    category: str
    created_at: str


@dataclass(frozen=True)
class RenameOp:
    id: str
    old: str
    new: str


def _opt_str(d: dict, key: str) -> str | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise InvalidInboxItem(f"{key} must be a string")
    return v.strip() or None


def _req_str(d: dict, key: str) -> str:
    v = _opt_str(d, key)
    if v is None:
        raise InvalidInboxItem(f"{key} is required")
    return v


def parse_item(path: Path) -> SaveItem | RenameOp:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidInboxItem(f"not valid JSON: {e}") from e
    if not isinstance(d, dict):
        raise InvalidInboxItem("top level must be an object")
    op = d.get("op")
    if op == "rename_category":
        old, new = _req_str(d, "from"), _req_str(d, "to")
        if old == new:
            raise InvalidInboxItem("rename to the same name")
        return RenameOp(id=path.stem, old=old, new=new)
    if op != "save":
        raise InvalidInboxItem(f"unknown op {op!r}")
    video_url = _req_str(d, "video_url")
    try:
        shortcode(video_url)
    except NotInstagramPost as e:
        raise InvalidInboxItem(f"not an Instagram post URL: {video_url}") from e
    dm_url = _opt_str(d, "dm_url")
    if dm_url and urlparse(dm_url).scheme not in ("http", "https"):
        raise InvalidInboxItem(f"dm_url must be http(s): {dm_url}")
    return SaveItem(id=path.stem, video_url=video_url, dm_url=dm_url, memo=_opt_str(d, "memo"),
                    category=_req_str(d, "category"), created_at=_req_str(d, "created_at"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/inbox.py tests/test_inbox.py
git commit -m "feat: parse and validate inbox items"
```

---

### Task 3: categories.md handling

**Files:**
- Create: `src/reelnote/categories.py`
- Test: `tests/test_categories.py`

**Interfaces:**
- Produces:
  - `categories.parse(text: str) -> list[str]`: order preserved, `#` comments and blank lines skipped, duplicates dropped
  - `categories.removed(previous: list[str], current: list[str]) -> list[str]`
  - `categories.apply_rename(text: str, old: str, new: str) -> str`: replaces the `old` line with `new`. If `new` is already present, the `old` line is just dropped. Comments are kept.

- [ ] **Step 1: Write failing tests**

`tests/test_categories.py`:

```python
from reelnote.categories import parse, removed, apply_rename

TEXT = "# Categories, most recently created or used first. One per line.\n생산성\n\n맥 세팅\n재테크\n생산성\n"

def test_parse_skips_comments_blanks_dupes():
    assert parse(TEXT) == ["생산성", "맥 세팅", "재테크"]

def test_removed():
    assert removed(["a", "b", "c"], ["c", "a"]) == ["b"]
    assert removed([], ["a"]) == []

def test_apply_rename_keeps_comment_and_order():
    out = apply_rename(TEXT, "맥 세팅", "맥 설정")
    assert out.splitlines()[0].startswith("#")
    assert parse(out) == ["생산성", "맥 설정", "재테크"]

def test_apply_rename_when_new_already_present():
    assert parse(apply_rename("a\nb\n", "a", "b")) == ["b"]

def test_apply_rename_when_old_missing_is_noop():
    assert apply_rename("a\n", "zzz", "y") == "a\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_categories.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/categories.py`:

```python
def parse(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        name = line.strip()
        if name and not name.startswith("#") and name not in out:
            out.append(name)
    return out


def removed(previous: list[str], current: list[str]) -> list[str]:
    cur = set(current)
    return [c for c in previous if c not in cur]


def apply_rename(text: str, old: str, new: str) -> str:
    names = parse(text)
    if old not in names:
        return text
    lines = []
    for line in text.splitlines():
        if line.strip() == old:
            if new in names:
                continue
            line = new
        lines.append(line)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_categories.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/categories.py tests/test_categories.py
git commit -m "feat: parse, diff, and rename categories.md"
```

---

### Task 4: SQLite state store

**Files:**
- Create: `src/reelnote/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `store.Store(path: Path)` with these methods:
  - `get(code: str) -> Item | None`
  - `create(code: str, inbox_id: str, data: dict) -> Item`
  - `update(code: str, *, state: str | None = None, data: dict | None = None, note_path: str | None = None) -> Item`: `data` is shallow-merged into the existing data
  - `fail_attempt(code: str, error: str, max_attempts: int) -> Item`: increments `attempts` and sets `last_error`. When `attempts >= max_attempts`, it sets `state="failed"`.
  - `reset(code: str) -> Item`: sets `attempts=0`, `last_error=None`, `state="queued"`
  - `by_state(state: str) -> list[Item]`
  - `all() -> list[Item]`
  - `put_embedding(code: str, vec: list[float]) -> None`
  - `embeddings() -> list[tuple[str, list[float], str | None]]`, as `(code, vec, hub)`
  - `set_hub(code: str, hub: str | None) -> None`
  - `cooldown_until(provider: str) -> float`
  - `set_cooldown(provider: str, until: float) -> None`
  - `get_meta(key: str, default=None)` and `set_meta(key: str, value) -> None` (values are JSON-encoded)
- Produces: `store.Item` (dataclass): `code`, `inbox_id`, `state`, `attempts`, `last_error`, `note_path`, `data: dict`
- States: `queued`, `fetched`, `extracted`, `summarized`, `written`, `failed`

- [ ] **Step 1: Write failing tests**

`tests/test_store.py`:

```python
from reelnote.store import Store

def test_create_get_update_merges_data(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.create("C1", "inbox-1", {"memos": ["a"]})
    s.update("C1", state="fetched", data={"caption": "hi"})
    item = s.get("C1")
    assert item.state == "fetched"
    assert item.data == {"memos": ["a"], "caption": "hi"}
    assert s.get("missing") is None

def test_fail_attempt_marks_failed_at_max(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.create("C1", "i", {})
    assert s.fail_attempt("C1", "boom", 3).state == "queued"
    s.fail_attempt("C1", "boom", 3)
    item = s.fail_attempt("C1", "boom 3", 3)
    assert (item.state, item.attempts, item.last_error) == ("failed", 3, "boom 3")
    assert s.reset("C1").attempts == 0

def test_embeddings_and_hub(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.create("C1", "i", {})
    s.put_embedding("C1", [0.5, 0.25])
    s.set_hub("C1", "Mac apps")
    assert s.embeddings() == [("C1", [0.5, 0.25], "Mac apps")]

def test_cooldown_and_meta_persist(tmp_path):
    p = tmp_path / "db.sqlite"
    s = Store(p)
    assert s.cooldown_until("claude") == 0.0
    s.set_cooldown("claude", 123.0)
    s.set_meta("last_categories", ["a", "b"])
    s2 = Store(p)
    assert s2.cooldown_until("claude") == 123.0
    assert s2.get_meta("last_categories") == ["a", "b"]
    assert s2.get_meta("nope", []) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/store.py`:

```python
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  code TEXT PRIMARY KEY, inbox_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, note_path TEXT, data TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS embeddings (code TEXT PRIMARY KEY, vec BLOB NOT NULL, hub TEXT);
CREATE TABLE IF NOT EXISTS cooldowns (provider TEXT PRIMARY KEY, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Item:
    code: str
    inbox_id: str
    state: str
    attempts: int
    last_error: str | None
    note_path: str | None
    data: dict = field(default_factory=dict)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(_SCHEMA)
        self.db.commit()

    def _row(self, r) -> Item:
        return Item(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]))

    def get(self, code: str) -> Item | None:
        r = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items WHERE code=?",
                            (code,)).fetchone()
        return self._row(r) if r else None

    def create(self, code: str, inbox_id: str, data: dict) -> Item:
        self.db.execute("INSERT INTO items(code,inbox_id,data) VALUES(?,?,?)",
                        (code, inbox_id, json.dumps(data, ensure_ascii=False)))
        self.db.commit()
        return self.get(code)

    def update(self, code: str, *, state: str | None = None, data: dict | None = None,
               note_path: str | None = None) -> Item:
        item = self.get(code)
        merged = {**item.data, **(data or {})}
        self.db.execute("UPDATE items SET state=?, data=?, note_path=? WHERE code=?",
                        (state or item.state, json.dumps(merged, ensure_ascii=False),
                         note_path or item.note_path, code))
        self.db.commit()
        return self.get(code)

    def fail_attempt(self, code: str, error: str, max_attempts: int) -> Item:
        item = self.get(code)
        attempts = item.attempts + 1
        state = "failed" if attempts >= max_attempts else item.state
        self.db.execute("UPDATE items SET attempts=?, last_error=?, state=? WHERE code=?",
                        (attempts, error, state, code))
        self.db.commit()
        return self.get(code)

    def reset(self, code: str) -> Item:
        self.db.execute("UPDATE items SET attempts=0, last_error=NULL, state='queued' WHERE code=?", (code,))
        self.db.commit()
        return self.get(code)

    def by_state(self, state: str) -> list[Item]:
        rows = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items "
                               "WHERE state=? ORDER BY rowid", (state,)).fetchall()
        return [self._row(r) for r in rows]

    def all(self) -> list[Item]:
        rows = self.db.execute("SELECT code,inbox_id,state,attempts,last_error,note_path,data FROM items "
                               "ORDER BY rowid").fetchall()
        return [self._row(r) for r in rows]

    def put_embedding(self, code: str, vec: list[float]) -> None:
        blob = np.asarray(vec, dtype=np.float32).tobytes()
        self.db.execute("INSERT INTO embeddings(code,vec) VALUES(?,?) ON CONFLICT(code) DO UPDATE SET vec=excluded.vec",
                        (code, blob))
        self.db.commit()

    def embeddings(self) -> list[tuple[str, list[float], str | None]]:
        rows = self.db.execute("SELECT code,vec,hub FROM embeddings ORDER BY rowid").fetchall()
        return [(c, np.frombuffer(v, dtype=np.float32).tolist(), h) for c, v, h in rows]

    def set_hub(self, code: str, hub: str | None) -> None:
        self.db.execute("UPDATE embeddings SET hub=? WHERE code=?", (hub, code))
        self.db.commit()

    def cooldown_until(self, provider: str) -> float:
        r = self.db.execute("SELECT until FROM cooldowns WHERE provider=?", (provider,)).fetchone()
        return r[0] if r else 0.0

    def set_cooldown(self, provider: str, until: float) -> None:
        self.db.execute("INSERT INTO cooldowns VALUES(?,?) ON CONFLICT(provider) DO UPDATE SET until=excluded.until",
                        (provider, until))
        self.db.commit()

    def get_meta(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value, ensure_ascii=False)))
        self.db.commit()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/store.py tests/test_store.py
git commit -m "feat: add SQLite state store"
```

---

### Task 5: Fetch (yt-dlp) and extraction

**Files:**
- Create: `src/reelnote/fetch.py`, `src/reelnote/extract.py`
- Test: `tests/test_fetch.py`, `tests/test_extract.py`

**Interfaces:**
- Produces:
  - `fetch.Download` (dataclass): `video: Path`, `caption: str`, `author: str | None`
  - `fetch.download(url: str, workdir: Path, cookies_browser: str | None = None, run=subprocess.run) -> Download`, which raises `fetch.Unavailable` or `fetch.LoginRequired`
  - `extract.transcript(video: Path, model: Path, workdir: Path, run=subprocess.run) -> str`
  - `extract.frame_texts(video: Path, workdir: Path, run=subprocess.run, ocr=None) -> list[str]`: OCR lines with duplicates removed across frames
  - `extract.dedupe_lines(frames: list[list[str]]) -> list[str]`
  - `extract.page_text(url: str, fetch_bytes=None) -> str`, which raises `extract.PageUnavailable`

- [ ] **Step 1: Write failing tests**

`tests/test_fetch.py`:

```python
import json
import subprocess
import pytest
from reelnote.fetch import download, Unavailable, LoginRequired

def fake_run_factory(tmp_path, outcomes):
    """outcomes: list of 'ok' | 'login' | 'gone', consumed one per yt-dlp call."""
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        outcome = outcomes[len(calls) - 1]
        if outcome == "ok":
            (tmp_path / "C8xYz2Qp.mp4").write_bytes(b"\0")
            (tmp_path / "C8xYz2Qp.info.json").write_text(json.dumps(
                {"id": "C8xYz2Qp", "description": "앱 7개", "uploader": "abc"}))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        err = {"login": "ERROR: [Instagram] C8xYz2Qp: Requested content is not available, rate-limit reached or login required",
               "gone": "ERROR: [Instagram] C8xYz2Qp: This content isn't available"}[outcome]
        return subprocess.CompletedProcess(cmd, 1, "", err)
    return run, calls

URL = "https://www.instagram.com/reel/C8xYz2Qp/"

def test_download_ok(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["ok"])
    d = download(URL, tmp_path, run=run)
    assert d.video.name == "C8xYz2Qp.mp4" and d.caption == "앱 7개" and d.author == "abc"
    assert "--cookies-from-browser" not in calls[0]

def test_login_wall_retries_with_cookies(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["login", "ok"])
    download(URL, tmp_path, cookies_browser="chrome", run=run)
    assert calls[1][calls[1].index("--cookies-from-browser") + 1] == "chrome"

def test_login_wall_without_cookies_raises(tmp_path):
    run, _ = fake_run_factory(tmp_path, ["login"])
    with pytest.raises(LoginRequired):
        download(URL, tmp_path, run=run)

def test_gone_raises_unavailable(tmp_path):
    run, _ = fake_run_factory(tmp_path, ["gone"])
    with pytest.raises(Unavailable):
        download(URL, tmp_path, run=run)
```

`tests/test_extract.py`:

```python
import pytest
from reelnote.extract import dedupe_lines, page_text, frame_texts, PageUnavailable

def test_dedupe_lines_keeps_first_and_drops_noise():
    frames = [["Raycast", "무료  런처"], ["raycast", "무료 런처", "x"], ["Rectangle"]]
    assert dedupe_lines(frames) == ["Raycast", "무료  런처", "Rectangle"]

def test_page_text_html():
    html = b"<html><body><nav>menu</nav><article><h1>Mac apps</h1><p>" + "Raycast is a launcher. ".encode() * 20 + b"</p></article></body></html>"
    text = page_text("https://example.com/a", fetch_bytes=lambda url: (html, "text/html"))
    assert "Raycast is a launcher." in text

def test_page_text_pdf_routes_to_pdf_reader(monkeypatch):
    import reelnote.extract as ex
    monkeypatch.setattr(ex, "_pdf_text", lambda data: "PDF BODY")
    assert page_text("https://x/y.pdf", fetch_bytes=lambda url: (b"%PDF", "application/pdf")) == "PDF BODY"

def test_page_text_empty_raises():
    with pytest.raises(PageUnavailable):
        page_text("https://x", fetch_bytes=lambda url: (b"<html></html>", "text/html"))

def test_page_text_fetch_error_raises():
    def boom(url):
        raise OSError("403")
    with pytest.raises(PageUnavailable):
        page_text("https://x", fetch_bytes=boom)

def test_frame_texts_uses_ocr_per_frame(tmp_path):
    def run(cmd, **kw):
        out = tmp_path / "frames"
        for i in (1, 2):
            (out / f"{i:04d}.png").write_bytes(b"\0")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    texts = frame_texts(tmp_path / "v.mp4", tmp_path, run=run, ocr=lambda p: ["Raycast", "무료"])
    assert texts == ["Raycast", "무료"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fetch.py tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/fetch.py`:

```python
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOGIN = re.compile(r"login required|rate-limit reached|cookies", re.I)
_GONE = re.compile(r"not available|isn't available|404|private|removed", re.I)


class Unavailable(Exception):
    pass


class LoginRequired(Exception):
    pass


@dataclass
class Download:
    video: Path
    caption: str
    author: str | None


def _attempt(url: str, workdir: Path, cookies_browser: str | None, run) -> Download:
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--write-info-json", "-f", "mp4/best",
           "-o", str(workdir / "%(id)s.%(ext)s")]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    res = run(cmd + [url], capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        err = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "yt-dlp failed"
        if _LOGIN.search(res.stderr):
            raise LoginRequired(err)
        if _GONE.search(res.stderr):
            raise Unavailable(err)
        raise RuntimeError(err)
    info_path = next(workdir.glob("*.info.json"))
    info = json.loads(info_path.read_text())
    video = next(p for p in workdir.glob(f"{info['id']}.*") if not p.name.endswith(".json"))
    return Download(video=video, caption=info.get("description") or "",
                    author=info.get("uploader") or info.get("channel"))


def download(url: str, workdir: Path, cookies_browser: str | None = None, run=subprocess.run) -> Download:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        return _attempt(url, workdir, None, run)
    except LoginRequired:
        if not cookies_browser:
            raise
        return _attempt(url, workdir, cookies_browser, run)
```

`src/reelnote/extract.py`:

```python
import io
import re
import subprocess
import urllib.request
from pathlib import Path

import trafilatura


class PageUnavailable(Exception):
    pass


def transcript(video: Path, model: Path, workdir: Path, run=subprocess.run) -> str:
    wav = workdir / "audio.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-ar", "16000", "-ac", "1", str(wav)],
        check=True, timeout=600)
    run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", "auto", "-otxt", "-of", str(workdir / "audio"), "-np"],
        check=True, timeout=1800, capture_output=True)
    return (workdir / "audio.txt").read_text(encoding="utf-8").strip()


def _vision_ocr(path: Path) -> list[str]:
    from ocrmac import ocrmac
    return [t for t, _conf, _box in ocrmac.OCR(str(path), language_preference=["ko-KR", "en-US"]).recognize()]


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().casefold()


def dedupe_lines(frames: list[list[str]]) -> list[str]:
    seen, out = set(), []
    for lines in frames:
        for line in lines:
            key = _norm(line)
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            out.append(line)
    return out


def frame_texts(video: Path, workdir: Path, run=subprocess.run, ocr=None) -> list[str]:
    ocr = ocr or _vision_ocr
    frames = workdir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=1", str(frames / "%04d.png")],
        check=True, timeout=600)
    return dedupe_lines([ocr(p) for p in sorted(frames.glob("*.png"))])


def _fetch_bytes(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) reelnote"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), r.headers.get("Content-Type", "")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def page_text(url: str, fetch_bytes=None) -> str:
    try:
        data, ctype = (fetch_bytes or _fetch_bytes)(url)
    except Exception as e:
        raise PageUnavailable(f"{url}: {e}") from e
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        text = _pdf_text(data)
    else:
        text = trafilatura.extract(data.decode("utf-8", errors="replace")) or ""
    if not text.strip():
        raise PageUnavailable(f"{url}: no readable text (login wall or empty page)")
    return text.strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fetch.py tests/test_extract.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Manual check against the spike video**

Run: `uv run python -c "from pathlib import Path; from reelnote.extract import transcript, frame_texts; import glob; v=Path(glob.glob('/tmp/rn-spike/*.mp4')[0]); w=Path('/tmp/rn-spike/w'); w.mkdir(exist_ok=True); print(transcript(v, Path('~/.cache/reelnote/ggml-large-v3-turbo.bin').expanduser(), w)[:300]); print(frame_texts(v, w)[:20])"`
Expected: Korean or English transcript text and a list of on-screen lines.

- [ ] **Step 6: Commit**

```bash
git add src/reelnote/fetch.py src/reelnote/extract.py tests/test_fetch.py tests/test_extract.py
git commit -m "feat: download reels and extract transcript, on-screen text, DM page text"
```

---

### Task 6: LLM provider chain

**Files:**
- Create: `src/reelnote/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `store.Store.cooldown_until`, `store.Store.set_cooldown`
- Produces:
  - `llm.NOTE_SCHEMA`, `llm.CLASSIFY_SCHEMA`, `llm.HUB_SCHEMA` (dicts)
  - `llm.note_prompt(*, caption: str, transcript: str, screen_text: str, dm_texts: list[str], memos: list[str], categories: list[str], fixed_category: str | None) -> str`
  - `llm.classify_prompt(*, title: str, summary: str, categories: list[str]) -> str`
  - `llm.hub_prompt(*, titles: list[str]) -> str`
  - `llm.extract_json(raw: str, schema: dict) -> dict`, which raises `llm.BadOutput`
  - `llm.Chain(store, providers: tuple[str, ...], cooldown_minutes: int, runner=subprocess.run, now=time.time)` with method `.run(prompt: str, schema: dict) -> dict`, which raises `llm.AllProvidersLimited` or `llm.BadOutput`

- [ ] **Step 1: Write failing tests**

`tests/test_llm.py`:

```python
import json
import subprocess
import pytest
from reelnote.llm import Chain, extract_json, BadOutput, AllProvidersLimited, NOTE_SCHEMA, CLASSIFY_SCHEMA
from reelnote.store import Store

NOTE = {"title": "맥북 생산성 앱 7개", "summary": "s", "key_facts": ["Raycast: 런처"], "keywords": ["mac"], "category": None}

def cp(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)

def test_extract_json_envelopes():
    assert extract_json(json.dumps(NOTE), NOTE_SCHEMA) == NOTE
    assert extract_json(json.dumps({"type": "result", "structured_output": NOTE}), NOTE_SCHEMA) == NOTE
    assert extract_json(json.dumps({"result": "```json\n" + json.dumps(NOTE) + "\n```"}), NOTE_SCHEMA) == NOTE

def test_extract_json_rejects_wrong_types():
    with pytest.raises(BadOutput):
        extract_json(json.dumps({**NOTE, "key_facts": "not a list"}), NOTE_SCHEMA)
    with pytest.raises(BadOutput):
        extract_json("I can't help with that", NOTE_SCHEMA)

def test_first_provider_success(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0]); return cp(out=json.dumps({"structured_output": NOTE}))
    assert Chain(Store(tmp_path / "db"), ("claude", "codex", "grok"), 60, runner=runner).run("p", NOTE_SCHEMA) == NOTE
    assert calls == ["claude"]

def test_limit_falls_back_and_sets_cooldown(tmp_path):
    store = Store(tmp_path / "db")
    def runner(cmd, **kw):
        if cmd[0] == "claude":
            return cp(1, err="Claude usage limit reached. Your limit will reset at 5pm")
        return cp(out=json.dumps({"category": "생산성"}))
    out = Chain(store, ("claude", "grok"), 60, runner=runner, now=lambda: 1000.0).run("p", CLASSIFY_SCHEMA)
    assert out == {"category": "생산성"}
    assert store.cooldown_until("claude") == 1000.0 + 3600

def test_cooled_down_provider_skipped_until_reset(tmp_path):
    store = Store(tmp_path / "db"); store.set_cooldown("claude", 2000.0)
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0]); return cp(out=json.dumps({"category": "a"}))
    Chain(store, ("claude", "grok"), 60, runner=runner, now=lambda: 1500.0).run("p", CLASSIFY_SCHEMA)
    Chain(store, ("claude", "grok"), 60, runner=runner, now=lambda: 2500.0).run("p", CLASSIFY_SCHEMA)
    assert calls == ["grok", "claude"]

def test_all_limited(tmp_path):
    runner = lambda cmd, **kw: cp(1, err="429 Too Many Requests")
    with pytest.raises(AllProvidersLimited):
        Chain(Store(tmp_path / "db"), ("claude", "codex", "grok"), 60, runner=runner).run("p", CLASSIFY_SCHEMA)

def test_bad_output_retried_once_then_next_provider(tmp_path):
    calls = []
    def runner(cmd, **kw):
        calls.append(cmd[0])
        return cp(out="not json") if cmd[0] == "claude" else cp(out=json.dumps({"category": "a"}))
    Chain(Store(tmp_path / "db"), ("claude", "grok"), 60, runner=runner).run("p", CLASSIFY_SCHEMA)
    assert calls == ["claude", "claude", "grok"]

def test_codex_reads_output_file(tmp_path):
    def runner(cmd, **kw):
        out = cmd[cmd.index("-o") + 1]
        open(out, "w").write(json.dumps({"category": "a"}))
        assert kw["cwd"] and "--output-schema" in cmd
        return cp(out="log noise")
    assert Chain(Store(tmp_path / "db"), ("codex",), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}

def test_missing_cli_skipped(tmp_path):
    def runner(cmd, **kw):
        if cmd[0] == "claude":
            raise FileNotFoundError("claude")
        return cp(out=json.dumps({"category": "a"}))
    assert Chain(Store(tmp_path / "db"), ("claude", "grok"), 60, runner=runner).run("p", CLASSIFY_SCHEMA) == {"category": "a"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/llm.py`:

```python
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

NOTE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "key_facts", "keywords", "category"],
    "properties": {
        "title": {"type": "string"}, "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "category": {"type": ["string", "null"]},
    },
}
CLASSIFY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["category"],
                   "properties": {"category": {"type": ["string", "null"]}}}
HUB_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name", "description"],
              "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}

LIMIT_PATTERNS = re.compile(r"usage limit|rate limit|limit reached|quota|too many requests|\b429\b|hit your limit", re.I)
_ENVELOPE_KEYS = ("structured_output", "result", "output", "text", "content", "message", "response")
_MAX_SOURCE_CHARS = 12000
_PY_TYPES = {"string": str, "array": list, "null": type(None), "object": dict}


class BadOutput(Exception):
    pass


class LimitReached(Exception):
    pass


class AllProvidersLimited(Exception):
    pass


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_SOURCE_CHARS else text[:_MAX_SOURCE_CHARS] + "\n[...truncated]"


def note_prompt(*, caption: str, transcript: str, screen_text: str, dm_texts: list[str], memos: list[str],
                categories: list[str], fixed_category: str | None) -> str:
    cat_rule = (f'Set "category" to null; the user already chose "{fixed_category}".' if fixed_category else
                f"Set \"category\" to exactly one of {json.dumps(categories, ensure_ascii=False)}, "
                "or null if none fits.")
    return f"""You turn an Instagram Reel into a note the user can search later.
Only use facts present in the sources below. Do not invent links, prices, or names.
Write in the main language of the sources (usually Korean).
- title: short and specific, under 60 characters.
- summary: 2-4 sentences.
- key_facts: every concrete item the video gives (names, steps, amounts, links), one per entry.
- keywords: 3-8 search terms.
{cat_rule}
Reply with JSON only.

<caption>
{_clip(caption)}
</caption>
<transcript>
{_clip(transcript)}
</transcript>
<on_screen_text>
{_clip(screen_text)}
</on_screen_text>
<dm_pages>
{_clip(chr(10).join(dm_texts))}
</dm_pages>
<user_memo>
{_clip(chr(10).join(memos))}
</user_memo>"""


def classify_prompt(*, title: str, summary: str, categories: list[str]) -> str:
    return (f"Pick the category for this note: exactly one of {json.dumps(categories, ensure_ascii=False)}, "
            f"or null if none fits. Reply with JSON only.\n\nTitle: {title}\nSummary: {summary}")


def hub_prompt(*, titles: list[str]) -> str:
    listed = "\n".join(f"- {t}" for t in titles)
    return ("These notes cover one topic. Give the topic a short name (under 40 characters, same language as "
            f"the titles) and a one-sentence description. Reply with JSON only.\n\n{listed}")


def _types_ok(obj: dict, schema: dict) -> bool:
    if not isinstance(obj, dict) or any(k not in obj for k in schema["required"]):
        return False
    for key, spec in schema["properties"].items():
        allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if key in obj and not isinstance(obj[key], tuple(_PY_TYPES[t] for t in allowed)):
            return False
        if spec.get("type") == "array" and key in obj and not all(isinstance(x, str) for x in obj[key]):
            return False
    return True


def extract_json(raw: str, schema: dict) -> dict:
    def walk(o, depth=0):
        if depth > 5:
            return None
        if isinstance(o, dict) and _types_ok(o, schema):
            return {k: o[k] for k in schema["properties"] if k in o}
        if isinstance(o, str):
            s = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", o)
            try:
                return walk(json.loads(s), depth + 1)
            except ValueError:
                return None
        if isinstance(o, dict):
            for k in _ENVELOPE_KEYS:
                if k in o and (r := walk(o[k], depth + 1)) is not None:
                    return r
        if isinstance(o, list):
            for x in reversed(o):
                if (r := walk(x, depth + 1)) is not None:
                    return r
        return None

    result = walk(raw)
    if result is None:
        raise BadOutput(raw[:500])
    return result


def _command(provider: str, prompt: str, schema: dict, tmp: Path) -> tuple[list[str], Path | None]:
    s = json.dumps(schema)
    if provider == "claude":
        return ["claude", "-p", prompt, "--output-format", "json", "--json-schema", s], None
    if provider == "codex":
        (tmp / "schema.json").write_text(s)
        out = tmp / "out.txt"
        return ["codex", "exec", "--skip-git-repo-check", "--output-schema", str(tmp / "schema.json"),
                "-o", str(out), prompt], out
    if provider == "grok":
        return ["grok", "-p", prompt, "--json-schema", s, "--disable-web-search"], None
    raise ValueError(f"unknown provider {provider}")


class Chain:
    def __init__(self, store, providers: tuple[str, ...], cooldown_minutes: int,
                 runner=subprocess.run, now=time.time):
        self.store, self.providers, self.cooldown = store, providers, cooldown_minutes * 60
        self.runner, self.now = runner, now

    def _call(self, provider: str, prompt: str, schema: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:  # empty cwd: agentic CLIs cannot touch any repo
            cmd, out_file = _command(provider, prompt, schema, Path(d))
            res = self.runner(cmd, cwd=d, capture_output=True, text=True, timeout=900)
            text = out_file.read_text() if out_file and out_file.exists() else res.stdout
            if res.returncode != 0:
                if LIMIT_PATTERNS.search(res.stdout + res.stderr):
                    raise LimitReached(provider)
                raise BadOutput((res.stderr or res.stdout)[-500:])
            try:
                return extract_json(text, schema)
            except BadOutput:
                if LIMIT_PATTERNS.search(res.stdout + res.stderr):
                    raise LimitReached(provider)
                raise

    def run(self, prompt: str, schema: dict) -> dict:
        limited = 0
        for p in self.providers:
            if self.store.cooldown_until(p) > self.now():
                limited += 1
                continue
            for attempt in range(2):
                try:
                    return self._call(p, prompt, schema)
                except LimitReached:
                    # shortcut: fixed cooldown, CLIs do not report a parseable reset time; parse it if Task 0 finds one
                    self.store.set_cooldown(p, self.now() + self.cooldown)
                    limited += 1
                    break
                except FileNotFoundError:
                    break
                except BadOutput:
                    if attempt == 1:
                        break
        if limited == len(self.providers):
            raise AllProvidersLimited()
        raise BadOutput("no provider produced valid JSON")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_llm.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/llm.py tests/test_llm.py
git commit -m "feat: add AI provider chain with schema output and limit fallback"
```

---

### Task 7: Embeddings and grouping

**Files:**
- Create: `src/reelnote/embed.py`, `src/reelnote/organize.py`
- Test: `tests/test_organize.py`

**Interfaces:**
- Produces:
  - `embed.embed(text: str, url: str, model: str = "bge-m3", post=None) -> list[float]`
  - `embed.cosine(a: list[float], b: list[float]) -> float`
  - `organize.Match` (dataclass): `kind: str` (`"duplicate"` | `"hub"` | `"new_hub"` | `"none"`), `other: str | None` (shortcode), `hub: str | None`, `score: float`
  - `organize.match(vec: list[float], others: list[tuple[str, list[float], str | None]], dup_t: float, hub_t: float) -> Match`
  - `organize.clusters(items: list[tuple[str, list[float]]], min_size: int = 2) -> list[list[str]]`

- [ ] **Step 1: Write failing tests**

`tests/test_organize.py`:

```python
import json
from reelnote.embed import cosine, embed
from reelnote.organize import match, clusters

A, A2, B = [1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.0, 1.0, 0.0]

def test_cosine():
    assert round(cosine(A, A), 6) == 1.0
    assert round(cosine(A, B), 6) == 0.0

def test_embed_posts_to_ollama():
    seen = {}
    def post(url, body):
        seen["url"], seen["body"] = url, body
        return {"embeddings": [[0.1, 0.2]]}
    assert embed("hello", "http://localhost:11434", post=post) == [0.1, 0.2]
    assert seen["url"].endswith("/api/embed") and seen["body"]["model"] == "bge-m3"

def test_match_duplicate():
    m = match(A2, [("C1", A, "Mac apps")], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.other, m.hub) == ("duplicate", "C1", "Mac apps")

def test_match_existing_hub():
    m = match([0.8, 0.6, 0.0], [("C1", A, "Mac apps")], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.hub) == ("hub", "Mac apps")

def test_match_new_hub_when_neighbor_unassigned():
    m = match([0.8, 0.6, 0.0], [("C1", A, None)], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.other) == ("new_hub", "C1")

def test_match_none_and_empty():
    assert match(B, [("C1", A, "x")], 0.98, 0.75).kind == "none"
    assert match(B, [], 0.98, 0.75).kind == "none"

def test_clusters_groups_close_vectors():
    items = [("a", [1, 0, 0]), ("b", [0.98, 0.05, 0]), ("c", [0, 1, 0]), ("d", [0.02, 0.99, 0]), ("e", [0, 0, 1])]
    groups = sorted(sorted(g) for g in clusters(items))
    assert groups == [["a", "b"], ["c", "d"]]

def test_clusters_too_few():
    assert clusters([("a", [1, 0])]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_organize.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/embed.py`:

```python
import json
import urllib.request

import numpy as np


def _post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def embed(text: str, url: str, model: str = "bge-m3", post=None) -> list[float]:
    return (post or _post)(f"{url}/api/embed", {"model": model, "input": text})["embeddings"][0]


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb) / denom if denom else 0.0
```

`src/reelnote/organize.py`:

```python
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import HDBSCAN

from reelnote.embed import cosine


@dataclass
class Match:
    kind: str
    other: str | None
    hub: str | None
    score: float


def match(vec, others, dup_t: float, hub_t: float) -> Match:
    if not others:
        return Match("none", None, None, 0.0)
    score, code, hub = max((cosine(vec, v), c, h) for c, v, h in others)
    if score >= dup_t:
        return Match("duplicate", code, hub, score)
    if score >= hub_t:
        return Match("hub", code, hub, score) if hub else Match("new_hub", code, None, score)
    return Match("none", None, None, score)


def clusters(items: list[tuple[str, list[float]]], min_size: int = 2) -> list[list[str]]:
    if len(items) < min_size + 1:
        return []
    x = np.asarray([v for _, v in items], dtype=np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)  # euclidean on unit vectors ranks like cosine
    labels = HDBSCAN(min_cluster_size=min_size).fit_predict(x)
    groups: dict[int, list[str]] = {}
    for (code, _), label in zip(items, labels):
        if label >= 0:
            groups.setdefault(int(label), []).append(code)
    return list(groups.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_organize.py -v`
Expected: PASS (8 tests). If `test_clusters_groups_close_vectors` puts `e` into a cluster, check the scikit-learn version (≥1.5) before changing `min_size`.

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/embed.py src/reelnote/organize.py tests/test_organize.py
git commit -m "feat: add embeddings, similarity match, and clustering"
```

---

### Task 8: Vault writing

**Files:**
- Create: `src/reelnote/vault.py`
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces:
  - `vault.OWNED_KEYS = {"category", "category_source", "hub", "status", "dm_links", "duplicate_of"}`
  - `vault.note_filename(notes_dir: Path, saved_at: str, title: str) -> Path`
  - `vault.render_note(fm: dict, *, title: str, summary: str, key_facts: list[str], dm: list[tuple[str, str]], memos: list[str], caption: str, transcript: str, screen_text: str) -> str`. Here `dm` is a list of `(url, text)` pairs.
  - `vault.write_new_note(path: Path, text: str) -> None`
  - `vault.read_note(path: Path) -> tuple[dict, str]`
  - `vault.update_frontmatter(path: Path, **changes) -> None`, which raises `ValueError` for keys not in `OWNED_KEYS`
  - `vault.append_to_section(path: Path, heading: str, text: str) -> None`
  - `vault.upsert_hub(hubs_dir: Path, name: str, *, category: str | None, description: str, members: list[tuple[str, list[str]]]) -> Path`. Here `members` is a list of `(note_stem, key_facts)` pairs.
  - `vault.write_review(path: Path, *, duplicates: list[tuple[str, str]], failed: list[tuple[str, str]], unavailable: list[str], dm_errors: list[tuple[str, str]]) -> None`
  - `vault.write_counts(path: Path, counts: dict[str, int]) -> None`
  - `vault.notes_with_category(notes_dir: Path, names: set[str]) -> list[Path]`
  - `vault.safe_name(text: str) -> str`

- [ ] **Step 1: Write failing tests**

`tests/test_vault.py`:

```python
import json
import pytest
from reelnote import vault

FM = {"shortcode": "C1", "url": "https://www.instagram.com/p/C1/", "category": "생산성", "category_source": "manual",
      "hub": None, "status": "done", "dm_links": [], "saved_at": "2026-10-17"}

def make_note(tmp_path):
    text = vault.render_note(FM, title="맥 앱", summary="요약", key_facts=["Raycast"], dm=[], memos=["참고"],
                             caption="cap", transcript="tr", screen_text="Raycast")
    p = tmp_path / "n.md"
    vault.write_new_note(p, text)
    return p

def test_note_filename_sanitizes_and_dedupes(tmp_path):
    p1 = vault.note_filename(tmp_path, "2026-10-17", 'Mac: 앱/툴 #1 [[best]] "7"')
    assert p1.name == "2026-10-17 Mac 앱툴 1 best 7.md"
    p1.write_text("x")
    assert vault.note_filename(tmp_path, "2026-10-17", 'Mac: 앱/툴 #1 [[best]] "7"').name == "2026-10-17 Mac 앱툴 1 best 7 (2).md"
    assert vault.note_filename(tmp_path, "2026-10-17", "///").name == "2026-10-17 untitled.md"

def test_render_and_read_roundtrip(tmp_path):
    fm, body = vault.read_note(make_note(tmp_path))
    assert fm == FM
    assert "## Summary\n\n요약" in body and "- Raycast" in body and "## My memo" in body

def test_update_frontmatter_keeps_unknown_keys(tmp_path):
    p = make_note(tmp_path)
    p.write_text(p.read_text().replace("saved_at:", "my_rating: 5\nsaved_at:"))
    vault.update_frontmatter(p, hub="[[Mac apps]]", status="review")
    fm, _ = vault.read_note(p)
    assert fm["my_rating"] == 5 and fm["hub"] == "[[Mac apps]]" and fm["status"] == "review"
    with pytest.raises(ValueError):
        vault.update_frontmatter(p, saved_at="x")

def test_append_preserves_hand_edits(tmp_path):
    p = make_note(tmp_path)
    p.write_text(p.read_text().replace("요약", "요약 (내가 고침)"))
    vault.append_to_section(p, "My memo", "- 두 번째 메모")
    vault.append_to_section(p, "DM info", "### https://x\n\nDM text")
    _, body = vault.read_note(p)
    assert "요약 (내가 고침)" in body
    memo = body.split("## My memo")[1].split("\n## ")[0]
    assert "- 참고" in memo and "- 두 번째 메모" in memo
    assert body.index("DM text") < body.index("## My memo")

def test_append_creates_missing_section(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\nshortcode: C1\n---\n# t\n")
    vault.append_to_section(p, "My memo", "- m")
    assert vault.read_note(p)[1].rstrip().endswith("## My memo\n\n- m")

def test_upsert_hub_keeps_user_text_and_pinned(tmp_path):
    p = vault.upsert_hub(tmp_path, "Mac apps", category="생산성", description="맥 앱 모음",
                         members=[("2026-10-17 a", ["Raycast", "Rectangle"]), ("2026-10-18 b", ["raycast"])])
    text = p.read_text()
    assert "- Raycast (2 notes)" in text and "[[2026-10-17 a]]" in text
    p.write_text(text.replace("pinned: false", "pinned: true") + "\nMy own thoughts\n")
    vault.upsert_hub(tmp_path, "Mac apps", category="생산성", description="새 설명", members=[("2026-10-17 a", ["Raycast"])])
    text = p.read_text()
    assert "pinned: true" in text and "My own thoughts" in text and "새 설명" in text and "(2 notes)" not in text

def test_review_and_counts(tmp_path):
    vault.write_review(tmp_path / "review.md", duplicates=[("2026-10-18 b", "2026-10-17 a")],
                       failed=[("https://www.instagram.com/p/C9/", "yt-dlp failed")], unavailable=["2026-10-16 c"],
                       dm_errors=[("2026-10-17 a", "https://x: no readable text")])
    text = (tmp_path / "review.md").read_text()
    assert "[[2026-10-18 b]] ≈ [[2026-10-17 a]]" in text and "yt-dlp failed" in text
    vault.write_counts(tmp_path / ".reelnote" / "category-counts.json", {"생산성": 2})
    assert json.loads((tmp_path / ".reelnote" / "category-counts.json").read_text()) == {"생산성": 2}

def test_notes_with_category(tmp_path):
    p = make_note(tmp_path)
    assert vault.notes_with_category(tmp_path, {"생산성"}) == [p]
    assert vault.notes_with_category(tmp_path, {"요리"}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_vault.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/vault.py`:

```python
import json
import re
from pathlib import Path

import yaml

OWNED_KEYS = {"category", "category_source", "hub", "status", "dm_links", "duplicate_of"}
START, END = "<!-- reelnote:start -->", "<!-- reelnote:end -->"
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]]')


def safe_name(text: str) -> str:
    name = re.sub(r"\s+", " ", _BAD.sub("", text)).strip()[:80].strip()
    return name or "untitled"


def note_filename(notes_dir: Path, saved_at: str, title: str) -> Path:
    base = f"{saved_at} {safe_name(title)}"
    path, n = notes_dir / f"{base}.md", 2
    while path.exists():
        path, n = notes_dir / f"{base} ({n}).md", n + 1
    return path


def _dump(fm: dict) -> str:
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n"


def read_note(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        return yaml.safe_load(head) or {}, body
    return {}, text


def _write(path: Path, fm: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(fm) + body, encoding="utf-8")


def render_note(fm: dict, *, title: str, summary: str, key_facts: list[str], dm: list[tuple[str, str]],
                memos: list[str], caption: str, transcript: str, screen_text: str) -> str:
    facts = "\n".join(f"- {f}" for f in key_facts) or "-"
    dm_md = "\n\n".join(f"### {url}\n\n{text}" for url, text in dm)
    memo_md = "\n".join(f"- {m}" for m in memos)
    body = (f"# {title}\n\n## Summary\n\n{summary}\n\n## Key facts\n\n{facts}\n\n## DM info\n\n{dm_md}\n\n"
            f"## My memo\n\n{memo_md}\n\n## Source\n\n### Caption\n\n{caption}\n\n### Transcript\n\n{transcript}\n\n"
            f"### On-screen text\n\n{screen_text}\n")
    return _dump(fm) + body


def write_new_note(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def update_frontmatter(path: Path, **changes) -> None:
    bad = set(changes) - OWNED_KEYS
    if bad:
        raise ValueError(f"not owned by reelnote: {sorted(bad)}")
    fm, body = read_note(path)
    fm.update(changes)
    _write(path, fm, body)


def append_to_section(path: Path, heading: str, text: str) -> None:
    fm, body = read_note(path)
    marker = f"## {heading}\n"
    if marker not in body:
        body = body.rstrip("\n") + f"\n\n{marker}\n{text}\n"
    else:
        start = body.index(marker) + len(marker)
        nxt = body.find("\n## ", start)
        end = len(body) if nxt == -1 else nxt
        section = body[start:end].rstrip("\n")
        body = body[:start] + section + "\n" + text + "\n" + body[end:]
    _write(path, fm, body)


def _hub_block(description: str, members: list[tuple[str, list[str]]]) -> str:
    counts: dict[str, list] = {}
    for _stem, facts in members:
        for f in facts:
            key = f.strip().casefold()  # shortcut: exact-text fact merge, upgrade to embedding merge if hubs get noisy
            counts.setdefault(key, [f.strip(), 0])[1] += 1
    facts_md = "\n".join(f"- {text} ({n} notes)" if n > 1 else f"- {text}" for text, n in counts.values())
    notes_md = "\n".join(f"- [[{stem}]]" for stem, _ in members)
    return f"{START}\n## Description\n\n{description}\n\n## Key facts across notes\n\n{facts_md}\n\n## Notes\n\n{notes_md}\n{END}"


def upsert_hub(hubs_dir: Path, name: str, *, category: str | None, description: str,
               members: list[tuple[str, list[str]]]) -> Path:
    path = hubs_dir / f"{safe_name(name)}.md"
    block = _hub_block(description, members)
    if not path.exists():
        _write(path, {"category": category, "pinned": False}, f"# {name}\n\n{block}\n")
        return path
    fm, body = read_note(path)
    if START in body and END in body:
        body = body[:body.index(START)] + block + body[body.index(END) + len(END):]
    else:
        body = body.rstrip("\n") + f"\n\n{block}\n"
    _write(path, fm, body)
    return path


def write_review(path: Path, *, duplicates, failed, unavailable, dm_errors) -> None:
    lines = ["# Review", "", "Generated by reelnote on every run. Edits here are overwritten.", "",
             "## Duplicate candidates", ""]
    lines += [f"- [[{a}]] ≈ [[{b}]]" for a, b in duplicates] or ["None."]
    lines += ["", "## Failed", ""] + ([f"- {url}: {err}" for url, err in failed] or ["None."])
    lines += ["", "## Unavailable videos", ""] + ([f"- [[{s}]]" for s in unavailable] or ["None."])
    lines += ["", "## DM pages that could not be read", ""] + ([f"- [[{s}]]: {e}" for s, e in dm_errors] or ["None."])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_counts(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def notes_with_category(notes_dir: Path, names: set[str]) -> list[Path]:
    return [p for p in sorted(notes_dir.glob("*.md")) if read_note(p)[0].get("category") in names]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_vault.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/vault.py tests/test_vault.py
git commit -m "feat: write notes, hubs, review, and counts without clobbering hand edits"
```

---

### Task 9: Data repo git sync

**Files:**
- Create: `src/reelnote/datarepo.py`
- Test: `tests/test_datarepo.py`

**Interfaces:**
- Produces: `datarepo.DataRepo(path: Path)` with:
  - `pull() -> None`: `pull --rebase`, with the phone's version winning a `vault/categories.md` conflict
  - `inbox_files() -> list[Path]`: `inbox/*.json`, sorted, excluding `inbox/failed/`
  - `commit(message: str) -> bool`: stages everything; returns `False` when nothing changed
  - `push() -> None`: on rejection, `pull()` and retry, up to 3 times. Raises `datarepo.SyncError`.

- [ ] **Step 1: Write failing tests**

`tests/test_datarepo.py`:

```python
import subprocess
from pathlib import Path
import pytest
from reelnote.datarepo import DataRepo

def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)

@pytest.fixture
def remote_and_clones(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True)
    for c in (seed,):
        git(c, "config", "user.email", "t@t"); git(c, "config", "user.name", "t")
    (seed / "inbox").mkdir(); (seed / "inbox" / ".gitkeep").write_text("")
    (seed / "vault").mkdir(); (seed / "vault" / "categories.md").write_text("# c\na\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "seed"); git(seed, "push", "-q", "origin", "main")
    mac, phone = tmp_path / "mac", tmp_path / "phone"
    for c in (mac, phone):
        subprocess.run(["git", "clone", "-q", str(remote), str(c)], check=True)
        git(c, "config", "user.email", "t@t"); git(c, "config", "user.name", "t")
    return mac, phone

def test_inbox_files_excludes_failed(remote_and_clones):
    mac, _ = remote_and_clones
    (mac / "inbox" / "b.json").write_text("{}"); (mac / "inbox" / "a.json").write_text("{}")
    (mac / "inbox" / "failed").mkdir(); (mac / "inbox" / "failed" / "c.json").write_text("{}")
    assert [p.name for p in DataRepo(mac).inbox_files()] == ["a.json", "b.json"]

def test_commit_nothing_returns_false(remote_and_clones):
    mac, _ = remote_and_clones
    assert DataRepo(mac).commit("noop") is False

def test_push_after_concurrent_phone_commit(remote_and_clones):
    mac, phone = remote_and_clones
    (phone / "vault" / "categories.md").write_text("# c\nb\na\n")
    (phone / "inbox" / "new.json").write_text("{}")
    git(phone, "add", "-A"); git(phone, "commit", "-qm", "phone"); git(phone, "push", "-q", "origin", "main")
    repo = DataRepo(mac)
    (mac / "vault" / "categories.md").write_text("# c\nz\n")
    (mac / "vault" / "note.md").write_text("n")
    assert repo.commit("mac") is True
    repo.push()
    git(phone, "pull", "-q", "--rebase")
    assert (phone / "vault" / "note.md").read_text() == "n"
    assert (phone / "vault" / "categories.md").read_text() == "# c\nb\na\n"
    assert (mac / "inbox" / "new.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_datarepo.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/datarepo.py`:

```python
import os
import subprocess
from pathlib import Path

CATEGORIES = "vault/categories.md"


class SyncError(Exception):
    pass


class DataRepo:
    def __init__(self, path: Path):
        self.path = path

    def _git(self, *args, check=True) -> subprocess.CompletedProcess:
        env = {**os.environ, "GIT_EDITOR": "true"}
        res = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, env=env)
        if check and res.returncode != 0:
            raise SyncError(f"git {' '.join(args)}: {res.stderr.strip()}")
        return res

    def pull(self) -> None:
        res = self._git("pull", "--rebase", "--autostash", "-q", check=False)
        while res.returncode != 0:
            conflicts = self._git("diff", "--name-only", "--diff-filter=U").stdout.split()
            if conflicts != [CATEGORIES]:
                self._git("rebase", "--abort", check=False)
                raise SyncError(f"pull conflict in {conflicts or res.stderr.strip()}")
            # during rebase "ours" is the remote side: the phone's categories.md wins
            self._git("checkout", "--ours", "--", CATEGORIES)
            self._git("add", CATEGORIES)
            res = self._git("rebase", "--continue", check=False)

    def inbox_files(self) -> list[Path]:
        return sorted((self.path / "inbox").glob("*.json"))

    def commit(self, message: str) -> bool:
        self._git("add", "-A")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._git("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        for _ in range(3):
            if self._git("push", "-q", "origin", "HEAD", check=False).returncode == 0:
                return
            self.pull()
        raise SyncError("push rejected 3 times")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_datarepo.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/reelnote/datarepo.py tests/test_datarepo.py
git commit -m "feat: sync data repo with rebase retry, phone wins categories conflicts"
```

---

### Task 10: Pipeline

**Files:**
- Create: `src/reelnote/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: all of Tasks 1–9
- Produces:
  - `pipeline.Deps` (dataclass): `fetch`, `transcript`, `frame_texts`, `page_text`, `llm` (an object with `.run(prompt, schema)`), `embed` (callable `text -> list[float]`), and `sleep`. `Deps.real(cfg, store)` builds the production deps.
  - `pipeline.process_save(item: SaveItem, cfg: Config, store: Store, deps: Deps, categories: list[str]) -> str`. Returns the final state: `"written"`, `"merged"`, `"failed"`, or `"extracted"` (all providers limited).
  - `pipeline.apply_rename(op: RenameOp, cfg: Config, store: Store) -> None`
  - `pipeline.reclassify_removed(cfg: Config, store: Store, deps: Deps, current: list[str]) -> None`
  - `pipeline.regroup(cfg: Config, store: Store, deps: Deps) -> int`: returns the number of new hubs
  - `pipeline.write_reports(cfg: Config, store: Store) -> None`
  - `pipeline.run_once(cfg: Config, store: Store, deps: Deps, repo: DataRepo) -> dict[str, int]`

**Data kept in `store.Item.data`:** `video_url`, `memos` (list), `dm_urls` (list), `category` (as sent), `created_at`, `caption`, `author`, `video` (path str), `transcript`, `screen_text`, `dm` (list of `[url, text]`), `dm_errors` (list of `[url, error]`), `note` (the LLM JSON), `unavailable` (bool), `duplicate_of` (note stem or null), `hub` (name or null).

- [ ] **Step 1: Write failing tests**

`tests/test_pipeline.py`:

```python
import json
from dataclasses import replace
from pathlib import Path
import pytest
from reelnote.config import Config
from reelnote.store import Store
from reelnote.inbox import SaveItem, RenameOp
from reelnote.fetch import Download, Unavailable
from reelnote.extract import PageUnavailable
from reelnote.llm import AllProvidersLimited, NOTE_SCHEMA, HUB_SCHEMA, CLASSIFY_SCHEMA
from reelnote import pipeline, vault

class FakeLLM:
    def __init__(self, note=None, limited=False):
        self.limited = limited
        self.note = note or {"title": "맥북 생산성 앱 7개", "summary": "앱 소개", "key_facts": ["Raycast: 런처"],
                             "keywords": ["mac"], "category": "생산성"}
        self.calls = []
    def run(self, prompt, schema):
        self.calls.append(schema)
        if self.limited:
            raise AllProvidersLimited()
        if schema is NOTE_SCHEMA:
            return self.note
        if schema is HUB_SCHEMA:
            return {"name": "맥 앱", "description": "맥 앱 모음"}
        return {"category": "생산성"}

def make(tmp_path, llm=None, vec=(1.0, 0.0), fetch=None, page=None):
    cfg = Config(data_repo=tmp_path / "data", state_dir=tmp_path / "state", download_gap_seconds=0)
    (cfg.vault / "notes").mkdir(parents=True)
    store = Store(cfg.db_path)
    def default_fetch(url, workdir, cookies_browser=None):
        workdir.mkdir(parents=True, exist_ok=True)
        v = workdir / "v.mp4"; v.write_bytes(b"\0")
        return Download(video=v, caption="캡션", author="abc")
    deps = pipeline.Deps(fetch=fetch or default_fetch, transcript=lambda v, m, w: "받아쓰기",
                         frame_texts=lambda v, w: ["Raycast"], page_text=page or (lambda url: "DM 본문"),
                         llm=llm or FakeLLM(), embed=lambda text: list(vec), sleep=lambda s: None)
    return cfg, store, deps

def item(id="i1", code="C8xYz2Qp", dm=None, memo=None, category="생산성"):
    return SaveItem(id=id, video_url=f"https://www.instagram.com/reel/{code}/", dm_url=dm, memo=memo,
                    category=category, created_at="2026-10-17T18:30:12+09:00")

def only_note(cfg):
    notes = list((cfg.vault / "notes").glob("*.md"))
    assert len(notes) == 1
    return notes[0]

def test_happy_path_writes_note(tmp_path):
    cfg, store, deps = make(tmp_path)
    assert pipeline.process_save(item(dm="https://n.site/x", memo="참고"), cfg, store, deps, ["생산성"]) == "written"
    p = only_note(cfg)
    fm, body = vault.read_note(p)
    assert p.name == "2026-10-17 맥북 생산성 앱 7개.md"
    assert fm["category"] == "생산성" and fm["category_source"] == "manual" and fm["status"] == "done"
    assert fm["dm_links"] == ["https://n.site/x"] and fm["url"] == "https://www.instagram.com/p/C8xYz2Qp/"
    assert "DM 본문" in body and "- 참고" in body and "받아쓰기" in body
    assert not Path(store.get("C8xYz2Qp").data["video"]).exists()

def test_auto_category_from_llm_and_unknown_dropped(tmp_path):
    cfg, store, deps = make(tmp_path, llm=FakeLLM(note={"title": "t", "summary": "s", "key_facts": [], "keywords": [], "category": "없는카테고리"}))
    pipeline.process_save(item(category="auto"), cfg, store, deps, ["생산성"])
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] is None and fm["category_source"] == "auto"

def test_known_shortcode_appends_memo_and_dm(tmp_path):
    cfg, store, deps = make(tmp_path)
    pipeline.process_save(item(), cfg, store, deps, ["생산성"])
    assert pipeline.process_save(item(id="i2", memo="나중 메모", dm="https://n.site/y"), cfg, store, deps, ["생산성"]) == "merged"
    fm, body = vault.read_note(only_note(cfg))
    assert "- 나중 메모" in body and fm["dm_links"] == ["https://n.site/y"]

def test_second_item_same_shortcode_merges_before_written(tmp_path):
    cfg, store, deps = make(tmp_path, llm=FakeLLM(limited=True))
    assert pipeline.process_save(item(memo="첫 메모"), cfg, store, deps, []) == "extracted"
    deps = replace(deps, llm=FakeLLM())
    assert pipeline.process_save(item(id="i2", memo="둘째 메모"), cfg, store, deps, []) == "written"
    _, body = vault.read_note(only_note(cfg))
    assert "- 첫 메모" in body and "- 둘째 메모" in body

def test_all_providers_limited_keeps_item(tmp_path):
    cfg, store, deps = make(tmp_path, llm=FakeLLM(limited=True))
    assert pipeline.process_save(item(), cfg, store, deps, []) == "extracted"
    rec = store.get("C8xYz2Qp")
    assert rec.state == "extracted" and rec.attempts == 0

def test_unavailable_writes_stub(tmp_path):
    def gone(url, workdir, cookies_browser=None):
        raise Unavailable("removed")
    cfg, store, deps = make(tmp_path, fetch=gone)
    assert pipeline.process_save(item(memo="m"), cfg, store, deps, []) == "written"
    fm, body = vault.read_note(only_note(cfg))
    assert fm["status"] == "unavailable" and "- m" in body

def test_failure_counts_attempts_then_fails(tmp_path):
    def broken(url, workdir, cookies_browser=None):
        raise RuntimeError("yt-dlp failed")
    cfg, store, deps = make(tmp_path, fetch=broken)
    states = [pipeline.process_save(item(), cfg, store, deps, []) for _ in range(3)]
    assert states == ["queued", "queued", "failed"]
    assert store.get("C8xYz2Qp").last_error == "yt-dlp failed"

def test_dm_page_failure_marks_review(tmp_path):
    def bad_page(url):
        raise PageUnavailable(f"{url}: no readable text")
    cfg, store, deps = make(tmp_path, page=bad_page)
    pipeline.process_save(item(dm="https://n.site/x"), cfg, store, deps, [])
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["status"] == "review" and fm["dm_links"] == ["https://n.site/x"]

def test_similar_notes_form_hub(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [0.8, 0.6])
    pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, [])
    hubs = list((cfg.vault / "hubs").glob("*.md"))
    assert [h.name for h in hubs] == ["맥 앱.md"]
    assert {h for _, _, h in store.embeddings()} == {"맥 앱"}

def test_duplicate_flagged_not_merged(tmp_path):
    cfg, store, deps = make(tmp_path)
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, [])
    assert len(list((cfg.vault / "notes").glob("*.md"))) == 2
    assert store.get("B2").data["duplicate_of"] is not None

def test_apply_rename_updates_notes_and_file(tmp_path):
    cfg, store, deps = make(tmp_path)
    (cfg.vault / "categories.md").write_text("# c\n생산성\n")
    store.set_meta("last_categories", ["생산성"])
    pipeline.process_save(item(), cfg, store, deps, ["생산성"])
    pipeline.apply_rename(RenameOp(id="r1", old="생산성", new="업무툴"), cfg, store)
    assert vault.read_note(only_note(cfg))[0]["category"] == "업무툴"
    assert "업무툴" in (cfg.vault / "categories.md").read_text()
    assert store.get_meta("last_categories") == ["업무툴"]

def test_reclassify_removed_category(tmp_path):
    cfg, store, deps = make(tmp_path)
    store.set_meta("last_categories", ["생산성", "요리"])
    pipeline.process_save(item(category="요리"), cfg, store, deps, ["생산성", "요리"])
    pipeline.reclassify_removed(cfg, store, deps, ["생산성"])
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "생산성" and fm["category_source"] == "auto"
    assert store.get_meta("last_categories") == ["생산성"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reelnote.pipeline'`

- [ ] **Step 3: Implement**

`src/reelnote/pipeline.py`:

```python
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from reelnote import categories as cats_mod
from reelnote import embed as embed_mod
from reelnote import extract, fetch, organize, vault
from reelnote.config import Config
from reelnote.datarepo import DataRepo
from reelnote.inbox import AUTO, InvalidInboxItem, RenameOp, SaveItem, parse_item
from reelnote.llm import (CLASSIFY_SCHEMA, HUB_SCHEMA, NOTE_SCHEMA, AllProvidersLimited, Chain,
                          classify_prompt, hub_prompt, note_prompt)
from reelnote.shortcode import canonical_url, shortcode
from reelnote.store import Store


@dataclass
class Deps:
    fetch: Callable
    transcript: Callable
    frame_texts: Callable
    page_text: Callable
    llm: object
    embed: Callable
    sleep: Callable

    @classmethod
    def real(cls, cfg: Config, store: Store) -> "Deps":
        return cls(fetch=fetch.download, transcript=extract.transcript, frame_texts=extract.frame_texts,
                   page_text=extract.page_text, llm=Chain(store, cfg.providers, cfg.cooldown_minutes),
                   embed=lambda text: embed_mod.embed(text, cfg.ollama_url), sleep=time.sleep)


def _notes_dir(cfg: Config) -> Path:
    return cfg.vault / "notes"


def _stem(store: Store, code: str) -> str | None:
    rec = store.get(code)
    return Path(rec.note_path).stem if rec and rec.note_path else None


def _merge_into_written(item: SaveItem, cfg: Config, store: Store, deps: Deps) -> str:
    rec = store.get(shortcode(item.video_url))
    path = cfg.vault / rec.note_path
    if item.memo:
        vault.append_to_section(path, "My memo", f"- {item.memo}")
    if item.dm_url and item.dm_url not in rec.data["dm_urls"]:
        fm, _ = vault.read_note(path)
        try:
            text = deps.page_text(item.dm_url)
            vault.append_to_section(path, "DM info", f"### {item.dm_url}\n\n{text}")
            changes = {}
        except extract.PageUnavailable as e:
            store.update(rec.code, data={"dm_errors": rec.data.get("dm_errors", []) + [[item.dm_url, str(e)]]})
            changes = {"status": "review"}
        vault.update_frontmatter(path, dm_links=list(fm.get("dm_links") or []) + [item.dm_url], **changes)
        store.update(rec.code, data={"dm_urls": rec.data["dm_urls"] + [item.dm_url]})
    return "merged"


def _ensure_record(item: SaveItem, store: Store):
    code = shortcode(item.video_url)
    rec = store.get(code)
    if rec is None:
        return store.create(code, item.id, {
            "video_url": item.video_url, "memos": [item.memo] if item.memo else [],
            "dm_urls": [item.dm_url] if item.dm_url else [], "category": item.category,
            "created_at": item.created_at})
    data = {}
    if item.memo and item.memo not in rec.data["memos"]:
        data["memos"] = rec.data["memos"] + [item.memo]
    if item.dm_url and item.dm_url not in rec.data["dm_urls"]:
        data["dm_urls"] = rec.data["dm_urls"] + [item.dm_url]
        if rec.state in ("extracted", "summarized"):
            rec = store.update(code, state="fetched")  # re-extract so the new DM page reaches the note
    if item.category != AUTO:
        data["category"] = item.category
    return store.update(code, data=data) if data else rec


def _fm(rec, *, category, category_source, status, hub=None, duplicate_of=None) -> dict:
    d = rec.data
    return {"shortcode": rec.code, "url": canonical_url(rec.code), "category": category,
            "category_source": category_source, "hub": f"[[{hub}]]" if hub else None, "status": status,
            "dm_links": d["dm_urls"], "saved_at": d["created_at"][:10],
            **({"duplicate_of": f"[[{duplicate_of}]]"} if duplicate_of else {})}


def _write_stub(rec, cfg: Config, store: Store) -> None:
    d = rec.data
    manual = d["category"] != AUTO
    fm = _fm(rec, category=d["category"] if manual else None,
             category_source="manual" if manual else "auto", status="unavailable")
    path = vault.note_filename(_notes_dir(cfg), fm["saved_at"], f"unavailable {rec.code}")
    vault.write_new_note(path, vault.render_note(fm, title=f"Unavailable video {rec.code}",
                         summary="The video was deleted or made private before reelnote could read it.",
                         key_facts=[], dm=[], memos=d["memos"], caption="", transcript="", screen_text=""))
    store.update(rec.code, state="written", data={"unavailable": True},
                 note_path=str(path.relative_to(cfg.vault)))


def _step_fetch(rec, cfg, store, deps):
    deps.sleep(cfg.download_gap_seconds)
    workdir = cfg.work_dir / rec.code
    try:
        dl = deps.fetch(rec.data["video_url"], workdir, cookies_browser=cfg.cookies_browser)
    except fetch.Unavailable:
        _write_stub(rec, cfg, store)
        return store.get(rec.code)
    return store.update(rec.code, state="fetched",
                        data={"caption": dl.caption, "author": dl.author, "video": str(dl.video)})


def _step_extract(rec, cfg, store, deps):
    video = Path(rec.data["video"])
    workdir = cfg.work_dir / rec.code
    if "transcript" not in rec.data:
        if not video.exists():
            return store.update(rec.code, state="queued")
        rec = store.update(rec.code, data={"transcript": deps.transcript(video, cfg.whisper_model, workdir),
                                           "screen_text": "\n".join(deps.frame_texts(video, workdir))})
    dm, errors = [], []
    for url in rec.data["dm_urls"]:
        try:
            dm.append([url, deps.page_text(url)])
        except extract.PageUnavailable as e:
            errors.append([url, str(e)])
    shutil.rmtree(workdir, ignore_errors=True)
    return store.update(rec.code, state="extracted", data={"dm": dm, "dm_errors": errors})


def _step_summarize(rec, cfg, store, deps, categories):
    d = rec.data
    fixed = d["category"] if d["category"] != AUTO else None
    note = deps.llm.run(note_prompt(caption=d["caption"], transcript=d["transcript"], screen_text=d["screen_text"],
                                    dm_texts=[t for _, t in d["dm"]], memos=d["memos"], categories=categories,
                                    fixed_category=fixed), NOTE_SCHEMA)
    return store.update(rec.code, state="summarized", data={"note": note})


def _hub_members(store: Store, hub: str) -> list[tuple[str, list[str]]]:
    out = []
    for code, _, h in store.embeddings():
        rec = store.get(code)
        if h == hub and rec.note_path:
            out.append((Path(rec.note_path).stem, rec.data.get("note", {}).get("key_facts", [])))
    return out


def _refresh_hub(cfg, store, name, description=None, category=None):
    existing = cfg.vault / "hubs" / f"{vault.safe_name(name)}.md"
    if description is None and existing.exists():
        body = vault.read_note(existing)[1]
        description = body.split("## Description\n\n", 1)[1].split("\n\n", 1)[0] if "## Description" in body else ""
    vault.upsert_hub(cfg.vault / "hubs", name, category=category, description=description or "",
                     members=_hub_members(store, name))
    for stem, _ in _hub_members(store, name):
        vault.update_frontmatter(_notes_dir(cfg) / f"{stem}.md", hub=f"[[{vault.safe_name(name)}]]")


def _step_write(rec, cfg, store, deps, categories):
    d, note = rec.data, rec.data["note"]
    if d["category"] != AUTO:
        category, source = d["category"], "manual"
    else:
        category, source = (note["category"] if note["category"] in categories else None), "auto"
    vec = deps.embed(f"{note['title']}\n{note['summary']}\n{' '.join(note['keywords'])}")
    m = organize.match(vec, [e for e in store.embeddings() if e[0] != rec.code], cfg.dup_threshold, cfg.hub_threshold)
    status = "review" if d.get("dm_errors") else "done"
    dup = _stem(store, m.other) if m.kind == "duplicate" else None
    fm = _fm(rec, category=category, category_source=source, status=status, duplicate_of=dup)
    path = vault.note_filename(_notes_dir(cfg), fm["saved_at"], note["title"])
    vault.write_new_note(path, vault.render_note(
        fm, title=note["title"], summary=note["summary"], key_facts=note["key_facts"],
        dm=[(u, t) for u, t in d["dm"]], memos=d["memos"], caption=d["caption"], transcript=d["transcript"],
        screen_text=d["screen_text"]))
    rec = store.update(rec.code, state="written", note_path=str(path.relative_to(cfg.vault)),
                       data={"duplicate_of": dup})
    store.put_embedding(rec.code, vec)
    if m.kind in ("hub", "duplicate") and m.hub:
        store.set_hub(rec.code, m.hub)
        _refresh_hub(cfg, store, m.hub, category=category)
    elif m.kind == "new_hub":
        other = store.get(m.other)
        named = deps.llm.run(hub_prompt(titles=[note["title"], other.data["note"]["title"]]), HUB_SCHEMA)
        for code in (rec.code, m.other):
            store.set_hub(code, named["name"])
        _refresh_hub(cfg, store, named["name"], description=named["description"], category=category)
    return rec


def process_save(item: SaveItem, cfg: Config, store: Store, deps: Deps, categories: list[str]) -> str:
    code = shortcode(item.video_url)
    existing = store.get(code)
    if existing and existing.state == "written":
        return _merge_into_written(item, cfg, store, deps)
    if existing and existing.state == "failed":
        store.reset(code)  # file reappeared in inbox/: the user asked for a retry
    rec = _ensure_record(item, store)
    try:
        while rec.state != "written":
            if rec.state == "queued":
                rec = _step_fetch(rec, cfg, store, deps)
                if rec.state == "queued":
                    continue
            elif rec.state == "fetched":
                rec = _step_extract(rec, cfg, store, deps)
            elif rec.state == "extracted":
                rec = _step_summarize(rec, cfg, store, deps, categories)
            elif rec.state == "summarized":
                rec = _step_write(rec, cfg, store, deps, categories)
    except AllProvidersLimited:
        return store.get(code).state
    except Exception as e:  # any step failure counts one attempt; review.md shows the error
        return store.fail_attempt(code, str(e) or type(e).__name__, cfg.max_attempts).state
    return "written"


def apply_rename(op: RenameOp, cfg: Config, store: Store) -> None:
    for path in vault.notes_with_category(_notes_dir(cfg), {op.old}):
        vault.update_frontmatter(path, category=op.new)
    for rec in store.all():
        if rec.data.get("category") == op.old:
            store.update(rec.code, data={"category": op.new})
    cat_file = cfg.vault / "categories.md"
    if cat_file.exists():
        cat_file.write_text(cats_mod.apply_rename(cat_file.read_text(), op.old, op.new))
    renamed: list[str] = []
    for c in store.get_meta("last_categories", []):
        c = op.new if c == op.old else c
        if c not in renamed:
            renamed.append(c)
    store.set_meta("last_categories", renamed)  # so the next diff does not treat the rename as a delete


def reclassify_removed(cfg: Config, store: Store, deps: Deps, current: list[str]) -> None:
    gone = set(cats_mod.removed(store.get_meta("last_categories", []), current))
    for path in vault.notes_with_category(_notes_dir(cfg), gone):
        fm, body = vault.read_note(path)
        title = body.splitlines()[0].lstrip("# ") if body else path.stem
        summary = body.split("## Summary\n\n", 1)[1].split("\n\n", 1)[0] if "## Summary" in body else ""
        out = deps.llm.run(classify_prompt(title=title, summary=summary, categories=current), CLASSIFY_SCHEMA)
        new = out["category"] if out["category"] in current else None
        vault.update_frontmatter(path, category=new, category_source="auto")
    for rec in store.all():
        if rec.data.get("category") in gone:
            store.update(rec.code, data={"category": AUTO})
    store.set_meta("last_categories", current)


def regroup(cfg: Config, store: Store, deps: Deps) -> int:
    unassigned = [(c, v) for c, v, h in store.embeddings() if h is None]
    made = 0
    for group in organize.clusters(unassigned):
        titles = [store.get(c).data["note"]["title"] for c in group]
        named = deps.llm.run(hub_prompt(titles=titles), HUB_SCHEMA)
        for c in group:
            store.set_hub(c, named["name"])
        _refresh_hub(cfg, store, named["name"], description=named["description"])
        made += 1
    return made


def write_reports(cfg: Config, store: Store) -> None:
    items = store.all()
    stems = {r.code: Path(r.note_path).stem for r in items if r.note_path}
    vault.write_review(
        cfg.vault / "review.md",
        duplicates=[(stems[r.code], r.data["duplicate_of"]) for r in items if r.data.get("duplicate_of") and r.code in stems],
        failed=[(r.data.get("video_url", r.code), r.last_error or "") for r in items if r.state == "failed"],
        unavailable=[stems[r.code] for r in items if r.data.get("unavailable") and r.code in stems],
        dm_errors=[(stems[r.code], err) for r in items if r.code in stems for _, err in r.data.get("dm_errors", [])])
    counts: dict[str, int] = {}
    for path in sorted(_notes_dir(cfg).glob("*.md")):
        cat = vault.read_note(path)[0].get("category")
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    vault.write_counts(cfg.vault / ".reelnote" / "category-counts.json", counts)


def run_once(cfg: Config, store: Store, deps: Deps, repo: DataRepo) -> dict[str, int]:
    tally: dict[str, int] = {}
    repo.pull()
    parsed = []
    for path in repo.inbox_files():
        try:
            parsed.append((path, parse_item(path)))
        except InvalidInboxItem as e:
            failed_dir = cfg.inbox / "failed"
            failed_dir.mkdir(exist_ok=True)
            path.rename(failed_dir / path.name)
            (failed_dir / f"{path.stem}.error.txt").write_text(str(e))
            tally["invalid"] = tally.get("invalid", 0) + 1
    for path, op in parsed:
        if isinstance(op, RenameOp):
            apply_rename(op, cfg, store)
            path.unlink()
            repo.commit(f"rename category {op.old} -> {op.new}")
    current = cats_mod.parse((cfg.vault / "categories.md").read_text()) if (cfg.vault / "categories.md").exists() else []
    try:
        reclassify_removed(cfg, store, deps, current)
    except AllProvidersLimited:
        pass  # retried next run because last_categories was not updated
    for path, op in parsed:
        if not isinstance(op, SaveItem):
            continue
        state = process_save(op, cfg, store, deps, current)
        tally[state] = tally.get(state, 0) + 1
        if state in ("written", "merged"):
            path.unlink()
        elif state == "failed":
            (cfg.inbox / "failed").mkdir(exist_ok=True)
            path.rename(cfg.inbox / "failed" / path.name)
        write_reports(cfg, store)
        repo.commit(f"reelnote: {op.id} {state}")
    write_reports(cfg, store)
    repo.commit("reelnote: reports")
    repo.push()
    return tally
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/reelnote/pipeline.py tests/test_pipeline.py
git commit -m "feat: add pipeline that turns inbox items into grouped notes"
```

---

### Task 11: CLI, run lock, launchd, setup docs

**Files:**
- Create: `src/reelnote/cli.py`, `launchd/com.reelnote.run.plist`, `config.example.toml`
- Modify: `README.md` (append a "Setup" section)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `pipeline.run_once`, `pipeline.regroup`, `pipeline.write_reports`, `pipeline.Deps.real`, `config.load`, `store.Store`, `datarepo.DataRepo`
- Produces: `cli.main(argv: list[str] | None = None) -> int`, and `cli.run_lock(state_dir: Path)` (a context manager that yields `True` if the lock was acquired)

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:

```python
from reelnote.cli import run_lock, main

def test_second_lock_not_acquired(tmp_path):
    with run_lock(tmp_path) as first:
        with run_lock(tmp_path) as second:
            assert first is True and second is False

def test_status_prints_counts(tmp_path, monkeypatch, capsys):
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f'data_repo = "{tmp_path / "data"}"\nstate_dir = "{tmp_path / "state"}"\n')
    monkeypatch.setenv("REELNOTE_CONFIG", str(cfgfile))
    from reelnote.store import Store
    s = Store(tmp_path / "state" / "reelnote.sqlite"); s.create("C1", "i", {})
    assert main(["status"]) == 0
    assert "queued: 1" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/reelnote/cli.py`:

```python
import argparse
import fcntl
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from reelnote import pipeline
from reelnote.config import load
from reelnote.datarepo import DataRepo
from reelnote.store import Store


@contextmanager
def run_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "run.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reelnote")
    parser.add_argument("command", choices=["run", "regroup", "status"])
    args = parser.parse_args(argv)
    cfg = load()
    store = Store(cfg.db_path)
    if args.command == "status":
        counts: dict[str, int] = {}
        for item in store.all():
            counts[item.state] = counts.get(item.state, 0) + 1
        for state, n in sorted(counts.items()):
            print(f"{state}: {n}")
        for p in cfg.providers:
            until = store.cooldown_until(p)
            if until > time.time():
                print(f"{p}: limited until {time.strftime('%H:%M', time.localtime(until))}")
        return 0
    with run_lock(cfg.state_dir) as got:
        if not got:
            print("another reelnote run is in progress", file=sys.stderr)
            return 0
        repo = DataRepo(cfg.data_repo)
        deps = pipeline.Deps.real(cfg, store)
        if args.command == "run":
            print(pipeline.run_once(cfg, store, deps, repo))
        else:
            repo.pull()
            print(f"new hubs: {pipeline.regroup(cfg, store, deps)}")
            pipeline.write_reports(cfg, store)
            repo.commit("reelnote: regroup")
            repo.push()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`config.example.toml`:

```toml
# Copy to ~/.config/reelnote/config.toml
data_repo = "~/reelnote-data"
state_dir = "~/.local/state/reelnote"
# cookies_browser = "chrome"   # only if yt-dlp hits Instagram's login wall
providers = ["claude", "codex", "grok"]
cooldown_minutes = 60
dup_threshold = 0.92
hub_threshold = 0.75
download_gap_seconds = 5
```

`launchd/com.reelnote.run.plist` (`__REPO__` and `__HOME__` are replaced during setup):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.reelnote.run</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>cd "__REPO__" &amp;&amp; uv run reelnote run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>900</integer>
  <key>StandardOutPath</key><string>__HOME__/Library/Logs/reelnote.log</string>
  <key>StandardErrorPath</key><string>__HOME__/Library/Logs/reelnote.log</string>
</dict>
</plist>
```

Append to `README.md`:

````markdown
## Setup (Mac)

```bash
brew install yt-dlp whisper-cpp ffmpeg
ollama pull bge-m3
mkdir -p ~/.cache/reelnote && curl -L -o ~/.cache/reelnote/ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
git clone https://github.com/<you>/reelnote-data ~/reelnote-data
mkdir -p ~/.config/reelnote && cp config.example.toml ~/.config/reelnote/config.toml
uv sync
uv run reelnote run          # first run by hand; check ~/reelnote-data/vault
sed -e "s#__REPO__#$PWD#" -e "s#__HOME__#$HOME#" launchd/com.reelnote.run.plist > ~/Library/LaunchAgents/com.reelnote.run.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.reelnote.run.plist
```

Log in to `claude`, `codex`, and `grok` once in a terminal before enabling launchd. `uv run reelnote status` shows item states and which AI providers are cooling down. `uv run reelnote regroup` groups unassigned notes into new hubs.
````

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Step 5: Smoke test against a scratch data repo (manual)**

```bash
git clone https://github.com/ChrisChae00/reelnote-data /tmp/rn-data
printf '{"op":"save","video_url":"<public reel url>","dm_url":null,"memo":"smoke test","category":"auto","created_at":"2026-10-17T18:30:12+09:00"}' > /tmp/rn-data/inbox/20261017-183012-0001.json
printf 'data_repo = "/tmp/rn-data"\nstate_dir = "/tmp/rn-state"\n' > /tmp/rn.toml
REELNOTE_CONFIG=/tmp/rn.toml uv run reelnote run
ls /tmp/rn-data/vault/notes && cat /tmp/rn-data/vault/review.md
```

Expected: one note in `vault/notes/` with a summary and key facts, and the inbox file removed. The run pushes to the real `reelnote-data` repo. Delete the smoke-test note and push again afterwards, or point `data_repo` at a throwaway clone of a test repo.

- [ ] **Step 6: Commit**

```bash
git add src/reelnote/cli.py tests/test_cli.py launchd config.example.toml README.md
git commit -m "feat: add reelnote CLI, run lock, launchd agent, and setup docs"
```
