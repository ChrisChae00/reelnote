import json
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

OWNED_KEYS = {"category", "category_source", "hub", "status", "dm_links", "duplicate_of"}
START, END = "<!-- reelnote:start -->", "<!-- reelnote:end -->"
_BAD = re.compile(r'[\\/:*?"<>|#^\[\]]')
_FM = re.compile(r"\A---\n(.*?)\n?---[ \t]*(?:\n|\Z)", re.S)


class BadNote(ValueError):
    pass


def safe_name(text: str) -> str:
    name = re.sub(r"\s+", " ", _BAD.sub("", text)).strip()[:80].strip()
    return name or "untitled"


def _inert(text: str) -> str:
    """Untrusted text must not embed remote images or HTML: Obsidian would fetch them on open."""
    return text.replace("![", "!\\[").replace("<", "&lt;")


def _inside(path: Path, parent: Path) -> Path:
    if path.resolve().parent != parent.resolve():
        raise ValueError(f"{path} escapes {parent}")
    return path


def note_filename(notes_dir: Path, saved_at: str, title: str) -> Path:
    base = f"{saved_at} {safe_name(title)}"
    path, n = notes_dir / f"{base}.md", 2
    while path.exists():
        path, n = notes_dir / f"{base} ({n}).md", n + 1
    return _inside(path, notes_dir)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _split(text: str) -> tuple[str | None, str]:
    m = _FM.match(text)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _join(fm_text: str | None, body: str) -> str:
    if fm_text is None:
        return body
    return "---\n" + (fm_text + "\n" if fm_text else "") + "---\n" + body


def _dump(fm: dict) -> str:
    return "---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=float("inf")) + "---\n"


def read_note(path: Path) -> tuple[dict, str]:
    raw, body = _split(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}, body
    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise BadNote(f"{path}: unparseable frontmatter: {e}") from e
    if fm is None:
        return {}, body
    if not isinstance(fm, dict):
        raise BadNote(f"{path}: frontmatter is not a mapping")
    return fm, body


def render_note(fm: dict, *, title: str, summary: str, key_facts: list[str], dm: list[tuple[str, str]],
                memos: list[str], caption: str, transcript: str, screen_text: str) -> str:
    title, summary, caption, transcript, screen_text = map(_inert, (title, summary, caption, transcript, screen_text))
    facts = "\n".join(f"- {_inert(f)}" for f in key_facts) or "-"
    dm_md = "\n\n".join(f"### {_inert(url)}\n\n{_inert(text)}" for url, text in dm)
    memo_md = "\n".join(f"- {m}" for m in memos)  # the user's own words: left as typed
    body = (f"# {title}\n\n## Summary\n\n{summary}\n\n## Key facts\n\n{facts}\n\n## DM info\n\n{dm_md}\n\n"
            f"## My memo\n\n{memo_md}\n\n## Source\n\n### Caption\n\n{caption}\n\n### Transcript\n\n{transcript}\n\n"
            f"### On-screen text\n\n{screen_text}\n")
    return _dump(fm) + body


def atomic_write(path: Path, text: str) -> None:
    _atomic_write(path, text)


def write_new_note(path: Path, text: str) -> None:
    _atomic_write(path, text)


def _set_key(fm_text: str, key: str, value) -> str:
    block = yaml.safe_dump({key: value}, allow_unicode=True, sort_keys=False, width=float("inf")).rstrip("\n")
    lines = fm_text.split("\n") if fm_text else []
    out, i, done = [], 0, False
    while i < len(lines):
        if re.match(rf"{re.escape(key)}:", lines[i]):
            if not done:
                out.append(block)
                done = True
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("- ")):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if not done:
        out.append(block)
    return "\n".join(out)


def update_frontmatter(path: Path, **changes) -> None:
    bad = set(changes) - OWNED_KEYS
    if bad:
        raise ValueError(f"not owned by reelnote: {sorted(bad)}")
    read_note(path)  # refuse to touch a note whose frontmatter is broken
    raw, body = _split(path.read_text(encoding="utf-8"))
    fm_text = raw or ""
    for key, value in changes.items():
        fm_text = _set_key(fm_text, key, value)
    _atomic_write(path, _join(fm_text, body))


def append_to_section(path: Path, heading: str, text: str) -> None:
    raw, body = _split(path.read_text(encoding="utf-8"))
    m = re.search(rf"(?m)^## {re.escape(heading)}[ \t]*$", body)
    if not m:
        body = body.rstrip("\n") + f"\n\n## {heading}\n\n{text}\n"
    else:
        nxt = re.compile(r"(?m)^## ").search(body, m.end())
        end = nxt.start() if nxt else len(body)
        keep = body[m.end():end].rstrip("\n") or "\n"
        rest = body[end:]
        body = body[:m.end()] + keep + "\n" + text + "\n" + ("\n" + rest if rest else "")
    _atomic_write(path, _join(raw, body))


def _hub_block(description: str, members: list[tuple[str, list[str]]]) -> str:
    counts: dict[str, list] = {}
    for _stem, facts in members:
        for f in facts:
            f = _inert(f)
            key = f.strip().casefold()  # shortcut: exact-text fact merge, upgrade to embedding merge if hubs get noisy
            counts.setdefault(key, [f.strip(), 0])[1] += 1
    facts_md = "\n".join(f"- {text} ({n} notes)" if n > 1 else f"- {text}" for text, n in counts.values())
    notes_md = "\n".join(f"- [[{stem}]]" for stem, _ in members)
    return f"{START}\n## Description\n\n{_inert(description)}\n\n## Key facts across notes\n\n{facts_md}\n\n## Notes\n\n{notes_md}\n{END}"


def upsert_hub(hubs_dir: Path, name: str, *, category: str | None, description: str,
               members: list[tuple[str, list[str]]]) -> Path:
    path = _inside(hubs_dir / f"{safe_name(name)}.md", hubs_dir)
    block = _hub_block(description, members)
    if not path.exists():
        _atomic_write(path, _dump({"category": category, "pinned": False}) + f"# {_inert(name)}\n\n{block}\n")
        return path
    raw, body = _split(path.read_text(encoding="utf-8"))
    s, e = body.find(START), body.find(END)
    if s != -1 and e != -1 and s < e:
        body = body[:s] + block + body[e + len(END):]
    else:
        body = body + ("\n" if body.endswith("\n") else "\n\n") + block + "\n"
    _atomic_write(path, _join(raw, body))
    return path


def write_review(path: Path, *, duplicates, failed, unavailable, dm_errors) -> None:
    lines = ["# Review", "", "Generated by reelnote on every run. Edits here are overwritten.", "",
             "## Duplicate candidates", ""]
    lines += [f"- [[{a}]] ≈ [[{b}]]" for a, b in duplicates] or ["None."]
    lines += ["", "## Failed", ""] + ([f"- {_inert(url)}: {_inert(err)}" for url, err in failed] or ["None."])
    lines += ["", "## Unavailable videos", ""] + ([f"- [[{s}]]" for s in unavailable] or ["None."])
    lines += ["", "## DM pages that could not be read", ""] + ([f"- [[{s}]]: {_inert(e)}" for s, e in dm_errors] or ["None."])
    _atomic_write(path, "\n".join(lines) + "\n")


def write_counts(path: Path, counts: dict[str, int]) -> None:
    _atomic_write(path, json.dumps(counts, ensure_ascii=False, indent=2) + "\n")


def notes_with_category(notes_dir: Path, names: set[str]) -> list[Path]:
    out = []
    for p in sorted(notes_dir.glob("*.md")):
        try:
            cat = read_note(p)[0].get("category")
        except BadNote:
            continue
        if isinstance(cat, str) and cat in names:
            out.append(p)
    return out
