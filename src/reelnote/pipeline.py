import shutil
import time
from dataclasses import dataclass, replace
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


class ToolUnavailable(Exception):
    """A local tool (yt-dlp, ffmpeg, whisper-cli, Ollama) is missing or down: stop the run, count no attempt."""


def _tool(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except FileNotFoundError as e:
        # a bare program name means subprocess could not find the binary; a path means a missing file
        if e.filename and "/" not in str(e.filename):
            raise ToolUnavailable(f"{e.filename} not found on PATH") from e
        raise


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


def _note_path(cfg: Config, store: Store, code: str) -> Path | None:
    """The note that really belongs to code; the user may have renamed or deleted it in Obsidian."""
    rec = store.get(code)
    if rec is None:
        return None

    def owns(p: Path) -> bool:
        try:
            return p.is_file() and vault.read_note(p)[0].get("shortcode") == code
        except (vault.BadNote, UnicodeDecodeError):
            return False

    if rec.note_path and owns(cfg.vault / rec.note_path):
        return cfg.vault / rec.note_path
    for p in sorted(_notes_dir(cfg).glob("*.md")):
        if owns(p):
            store.update(code, note_path=str(p.relative_to(cfg.vault)))
            return p
    return None


def _stem(cfg: Config, store: Store, code: str) -> str | None:
    path = _note_path(cfg, store, code)
    return path.stem if path else None


def _merge_into_written(item: SaveItem, path: Path, cfg: Config, store: Store, deps: Deps) -> str:
    rec = store.get(shortcode(item.video_url))
    if item.memo and item.memo not in rec.data["memos"]:
        vault.append_to_section(path, "My memo", f"- {item.memo}")
        rec = store.update(rec.code, data={"memos": rec.data["memos"] + [item.memo]})
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
        dl = _tool(deps.fetch, rec.data["video_url"], workdir, cookies_browser=cfg.cookies_browser)
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
        rec = store.update(rec.code, data={"transcript": _tool(deps.transcript, video, cfg.whisper_model, workdir),
                                           "screen_text": "\n".join(_tool(deps.frame_texts, video, workdir))})
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


def _hub_members(cfg: Config, store: Store, hub: str) -> list[tuple[str, list[str]]]:
    out = []
    for code, _, h in store.embeddings():
        if h == hub and (path := _note_path(cfg, store, code)):  # a note deleted by hand drops out
            out.append((path.stem, store.get(code).data.get("note", {}).get("key_facts", [])))
    return out


def _refresh_hub(cfg, store, name, description=None, category=None):
    existing = cfg.vault / "hubs" / f"{vault.safe_name(name)}.md"
    if description is None and existing.exists():
        body = vault.read_note(existing)[1]
        description = body.split("## Description\n\n", 1)[1].split("\n\n", 1)[0] if "## Description" in body else ""
    members = _hub_members(cfg, store, name)
    vault.upsert_hub(cfg.vault / "hubs", name, category=category, description=description or "",
                     members=members)
    for stem, _ in members:
        vault.update_frontmatter(_notes_dir(cfg) / f"{stem}.md", hub=f"[[{vault.safe_name(name)}]]")


def _hub_work(rec, m, cfg, store, deps, category):
    note = rec.data["note"]
    if m.kind in ("hub", "duplicate") and m.hub:
        store.set_hub(rec.code, m.hub)
        _refresh_hub(cfg, store, m.hub, category=category)
    elif m.kind == "new_hub":
        other = store.get(m.other)
        named = deps.llm.run(hub_prompt(titles=[note["title"], other.data["note"]["title"]]), HUB_SCHEMA)
        for code in (rec.code, m.other):
            store.set_hub(code, named["name"])
        _refresh_hub(cfg, store, named["name"], description=named["description"], category=category)


def _step_write(rec, cfg, store, deps, categories):
    d, note = rec.data, rec.data["note"]
    if d["category"] != AUTO and d["category"] in categories:
        category, source = d["category"], "manual"
    else:  # auto, or a manual category that no longer exists
        category, source = (note["category"] if note["category"] in categories else None), "auto"
    try:
        vec = deps.embed(f"{note['title']}\n{note['summary']}\n{' '.join(note['keywords'])}")
    except OSError as e:  # URLError is an OSError: Ollama is not running
        raise ToolUnavailable(f"Ollama embed failed: {e}") from e
    m = organize.match(vec, [e for e in store.embeddings() if e[0] != rec.code], cfg.dup_threshold, cfg.hub_threshold)
    status = "review" if d.get("dm_errors") else "done"
    dup = _stem(cfg, store, m.other) if m.kind == "duplicate" else None
    fm = _fm(rec, category=category, category_source=source, status=status, duplicate_of=dup)
    path = vault.note_filename(_notes_dir(cfg), fm["saved_at"], note["title"])
    vault.write_new_note(path, vault.render_note(
        fm, title=note["title"], summary=note["summary"], key_facts=note["key_facts"],
        dm=[(u, t) for u, t in d["dm"]], memos=d["memos"], caption=d["caption"], transcript=d["transcript"],
        screen_text=d["screen_text"]))
    rec = store.update(rec.code, state="written", note_path=str(path.relative_to(cfg.vault)),
                       data={"duplicate_of": dup})
    store.put_embedding(rec.code, vec)
    try:  # the note is already written: hub trouble must never fail the item
        _hub_work(rec, m, cfg, store, deps, category)
    except Exception as e:
        rec = store.update(rec.code, data={"hub_error": str(e) or type(e).__name__})
    return rec


def process_save(item: SaveItem, cfg: Config, store: Store, deps: Deps, categories: list[str]) -> str:
    code = shortcode(item.video_url)
    existing = store.get(code)
    if existing and existing.state == "written":
        path = _note_path(cfg, store, code)
        if path:
            return _merge_into_written(item, path, cfg, store, deps)
        if "note" not in existing.data:
            _record_error(store, item.id, f"note for {code} is gone and cannot be rebuilt; save dropped")
            return "merged"  # removes the inbox file so it stops looping
        existing = store.update(code, state="summarized")  # note deleted by hand: write it again
    if existing and existing.state == "failed":
        store.reset(code)  # file reappeared in inbox/: the user asked for a retry
    rec = _ensure_record(item, store)
    try:
        while rec.state != "written":
            before = rec.state
            if before == "queued":
                rec = _step_fetch(rec, cfg, store, deps)
            elif before == "fetched":
                rec = _step_extract(rec, cfg, store, deps)
            elif before == "extracted":
                rec = _step_summarize(rec, cfg, store, deps, categories)
            elif before == "summarized":
                rec = _step_write(rec, cfg, store, deps, categories)
            else:
                raise RuntimeError(f"unknown state {before}")
            if rec.state != "queued":
                store.clear_attempts(code)  # progress: earlier failures no longer count
    except AllProvidersLimited as e:
        err = ["llm", str(e)]
        if str(e) and err not in store.get_meta("run_errors", []):
            _record_error(store, *err)  # once per run, e.g. "claude: not found; codex: not logged in"
        return store.get(code).state
    except ToolUnavailable:
        raise
    except Exception as e:  # any step failure counts one attempt; review.md shows the error
        if store.get(code).state == "written":
            return "written"
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
        failed=[(r.data.get("video_url", r.code), r.last_error or "") for r in items if r.state == "failed"]
        + [(i, e) for i, e in store.get_meta("run_errors", [])],
        unavailable=[stems[r.code] for r in items if r.data.get("unavailable") and r.code in stems],
        dm_errors=[(stems[r.code], err) for r in items if r.code in stems for _, err in r.data.get("dm_errors", [])])
    counts: dict[str, int] = {}
    for path in sorted(_notes_dir(cfg).glob("*.md")):
        try:
            cat = vault.read_note(path)[0].get("category")
        except vault.BadNote:
            continue  # a note the user broke by hand must not stop the run
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    vault.write_counts(cfg.vault / ".reelnote" / "category-counts.json", counts)


def _read_categories(cfg: Config) -> list[str] | None:
    f = cfg.vault / "categories.md"
    return cats_mod.parse(f.read_text()) if f.exists() else None


def _record_error(store: Store, inbox_id: str, error: str) -> None:
    store.set_meta("run_errors", store.get_meta("run_errors", []) + [[inbox_id, error]])


def _handle_error(op, e: Exception, path: Path, cfg: Config, store: Store) -> None:
    err = str(e) or type(e).__name__
    rec = store.get(shortcode(op.video_url)) if isinstance(op, SaveItem) else None
    if rec is None or rec.state == "written":
        _record_error(store, op.id, err)  # written notes never accumulate failed attempts
        return
    if store.fail_attempt(rec.code, err, cfg.max_attempts).state == "failed":
        (cfg.inbox / "failed").mkdir(exist_ok=True)
        path.rename(cfg.inbox / "failed" / path.name)


def run_once(cfg: Config, store: Store, deps: Deps, repo: DataRepo) -> dict[str, int]:
    tally: dict[str, int] = {}
    store.set_meta("run_errors", [])
    repo.pull()
    parsed = []
    for path in repo.inbox_files():  # sorted by name = arrival order
        try:
            parsed.append((path, parse_item(path)))
        except InvalidInboxItem as e:
            failed_dir = cfg.inbox / "failed"
            failed_dir.mkdir(exist_ok=True)
            path.rename(failed_dir / path.name)
            (failed_dir / f"{path.stem}.error.txt").write_text(str(e))
            tally["invalid"] = tally.get("invalid", 0) + 1
    renames: dict[str, str] = {}  # old -> final new name, chained across ops
    for path, op in [(p, o) for p, o in parsed if isinstance(o, RenameOp)]:
        try:  # renames first: the phone already removed the old name from categories.md
            apply_rename(op, cfg, store)
            for k, v in renames.items():
                if v == op.old:
                    renames[k] = op.new
            renames[op.old] = op.new
            path.unlink()
            repo.commit(f"rename category {op.old} -> {op.new}")
        except Exception as e:
            tally["error"] = tally.get("error", 0) + 1
            _handle_error(op, e, path, cfg, store)
    current = _read_categories(cfg)
    if current is not None:  # no categories.md: do not treat every category as deleted
        try:
            reclassify_removed(cfg, store, deps, current)
        except AllProvidersLimited:
            pass  # retried next run because last_categories was not updated
        except Exception as e:
            _record_error(store, "reclassify", str(e) or type(e).__name__)
    for path, op in [(p, o) for p, o in parsed if isinstance(o, SaveItem)]:
        op = replace(op, category=renames.get(op.category, op.category))
        try:
            state = process_save(op, cfg, store, deps, current or [])
            tally[state] = tally.get(state, 0) + 1
            if state in ("written", "merged"):
                path.unlink()
            elif state == "failed":
                (cfg.inbox / "failed").mkdir(exist_ok=True)
                path.rename(cfg.inbox / "failed" / path.name)
            write_reports(cfg, store)
            repo.commit(f"reelnote: {op.id} {state}")
        except ToolUnavailable as e:  # every later item would hit the same wall
            _record_error(store, op.id, str(e))
            break
        except Exception as e:  # one bad item must not block the rest of the inbox
            tally["error"] = tally.get("error", 0) + 1
            _handle_error(op, e, path, cfg, store)
    write_reports(cfg, store)
    repo.commit("reelnote: reports")
    repo.push()
    return tally
