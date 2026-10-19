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


# ---- fix round 1 ----
import subprocess


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path, categories=None, **kw):
    """Local bare remote + 'mac' clone; run_once pushes only to this tmp remote."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
    (seed / "inbox").mkdir()
    (seed / "inbox" / ".gitkeep").write_text("")
    if categories is not None:
        (seed / "vault").mkdir()
        (seed / "vault" / "categories.md").write_text(categories)
    for c in (seed,):
        _git(c, "config", "user.email", "t@t"); _git(c, "config", "user.name", "t"); _git(c, "config", "commit.gpgsign", "false")
    _git(seed, "add", "-A"); _git(seed, "commit", "-qm", "seed"); _git(seed, "push", "-q", "origin", "main")
    mac = tmp_path / "mac"
    subprocess.run(["git", "clone", "-q", str(remote), str(mac)], check=True, capture_output=True)
    _git(mac, "config", "user.email", "t@t"); _git(mac, "config", "user.name", "t"); _git(mac, "config", "commit.gpgsign", "false")
    cfg = Config(data_repo=mac, state_dir=tmp_path / "state", download_gap_seconds=0)
    (cfg.vault / "notes").mkdir(parents=True, exist_ok=True)
    store = Store(cfg.db_path)

    def default_fetch(url, workdir, cookies_browser=None):
        workdir.mkdir(parents=True, exist_ok=True)
        v = workdir / "v.mp4"; v.write_bytes(b"\0")
        return Download(video=v, caption="c", author="a")
    deps = pipeline.Deps(fetch=kw.get("fetch", default_fetch), transcript=lambda v, m, w: "t",
                         frame_texts=lambda v, w: ["x"], page_text=lambda u: "p", llm=FakeLLM(),
                         embed=lambda text: [1.0, 0.0], sleep=lambda s: None)
    from reelnote.datarepo import DataRepo
    return cfg, store, deps, DataRepo(mac), remote


def put_inbox(cfg, name, **fields):
    d = {"op": "save", "dm_url": None, "memo": None, "category": "auto", "created_at": "2026-10-17T18:30:12+09:00"}
    d.update(fields)
    (cfg.inbox / name).write_text(json.dumps(d))


def test_run_once_bad_item_does_not_block_others(tmp_path):
    def fetch(url, workdir, cookies_browser=None):
        if "BAD" in url:
            raise RuntimeError("boom")
        workdir.mkdir(parents=True, exist_ok=True)
        v = workdir / "v.mp4"; v.write_bytes(b"\0")
        return Download(video=v, caption="c", author="a")
    cfg, store, deps, repo, remote = make_repo(tmp_path, categories="# c\n생산성\n", fetch=fetch)
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/BAD1/")
    put_inbox(cfg, "002.json", video_url="https://www.instagram.com/reel/GOOD2/")
    tally = pipeline.run_once(cfg, store, deps, repo)
    assert tally["written"] == 1
    assert (cfg.inbox / "001.json").exists() and not (cfg.inbox / "002.json").exists()
    check = tmp_path / "check"
    subprocess.run(["git", "clone", "-q", str(remote), str(check)], check=True, capture_output=True)
    assert len(list((check / "vault" / "notes").glob("*.md"))) == 1
    assert not (check / "inbox" / "002.json").exists() and (check / "inbox" / "001.json").exists()


def test_run_once_isolates_unexpected_exception(tmp_path):
    cfg, store, deps, repo, remote = make_repo(tmp_path, categories="# c\n생산성\n")
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/BAD1/")
    put_inbox(cfg, "002.json", video_url="https://www.instagram.com/reel/GOOD2/")
    real = pipeline.process_save

    def flaky(op, *a):
        if "BAD1" in op.video_url:
            raise ValueError("unexpected")
        return real(op, *a)
    orig, pipeline.process_save = pipeline.process_save, flaky
    try:
        tally = pipeline.run_once(cfg, store, deps, repo)
    finally:
        pipeline.process_save = orig
    assert tally["error"] == 1 and tally["written"] == 1
    assert (cfg.inbox / "001.json").exists()
    assert ["001", "unexpected"] in store.get_meta("run_errors")
    assert "unexpected" in (cfg.vault / "review.md").read_text()


def test_merge_does_not_duplicate_memo(tmp_path):
    cfg, store, deps = make(tmp_path)
    pipeline.process_save(item(memo="같은 메모"), cfg, store, deps, [])
    assert pipeline.process_save(item(id="i2", memo="같은 메모"), cfg, store, deps, []) == "merged"
    assert pipeline.process_save(item(id="i3", memo="새 메모"), cfg, store, deps, []) == "merged"
    assert pipeline.process_save(item(id="i4", memo="새 메모"), cfg, store, deps, []) == "merged"
    _, body = vault.read_note(only_note(cfg))
    assert body.count("- 같은 메모") == 1 and body.count("- 새 메모") == 1
    assert store.get("C8xYz2Qp").data["memos"] == ["같은 메모", "새 메모"]


class HubFailLLM(FakeLLM):
    def run(self, prompt, schema):
        if schema is HUB_SCHEMA:
            raise RuntimeError("hub llm down")
        return super().run(prompt, schema)


def test_hub_error_keeps_written(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    deps = replace(deps, llm=HubFailLLM(), embed=lambda t: [0.8, 0.6])
    assert pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, []) == "written"
    rec = store.get("B2")
    assert rec.state == "written" and rec.attempts == 0
    assert "hub llm down" in rec.data["hub_error"]


def test_rename_then_save_order(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    (cfg.inbox / "002.json").write_text(json.dumps({"op": "rename_category", "from": "X", "to": "Y"}))
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "Y" and fm["category_source"] == "manual"


def test_save_after_rename_with_old_category_follows_rename(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    (cfg.inbox / "001.json").write_text(json.dumps({"op": "rename_category", "from": "X", "to": "Y"}))
    put_inbox(cfg, "002.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category_source"] == "manual" and fm["category"] == "Y"


def test_missing_categories_file_skips_reclassify(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path)
    assert not (cfg.vault / "categories.md").exists()
    pipeline.process_save(item(category="a"), cfg, store, deps, ["a"])
    store.set_meta("last_categories", ["a"])
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "a" and fm["category_source"] == "manual"
    assert store.get_meta("last_categories") == ["a"]


def test_phone_rename_keeps_manual_note(tmp_path):
    # phone flow: categories.md already shows Y (X gone) and inbox holds the X -> Y rename op
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "X" and fm["category_source"] == "manual"
    (cfg.vault / "categories.md").write_text("# c\nY\n")
    (cfg.inbox / "002.json").write_text(json.dumps({"op": "rename_category", "from": "X", "to": "Y"}))
    _git(cfg.data_repo, "add", "-A"); _git(cfg.data_repo, "commit", "-qm", "phone")
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "Y" and fm["category_source"] == "manual"
    assert store.get("A1").data["category"] == "Y"


def test_chained_renames_map_saves(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nZ\n")
    store.set_meta("last_categories", ["X"])
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    (cfg.inbox / "002.json").write_text(json.dumps({"op": "rename_category", "from": "X", "to": "Y"}))
    (cfg.inbox / "003.json").write_text(json.dumps({"op": "rename_category", "from": "Y", "to": "Z"}))
    pipeline.run_once(cfg, store, deps, repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "Z" and fm["category_source"] == "manual"


def test_merge_ignores_other_note_at_stale_path(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    a = cfg.vault / store.get("A1").note_path
    a.unlink()  # user deletes A in Obsidian
    deps = replace(deps, embed=lambda t: [0.0, 1.0])
    pipeline.process_save(item(id="i2", code="C3"), cfg, store, deps, [])
    c = cfg.vault / store.get("C3").note_path
    assert c == a  # C reused A's old filename
    before = c.read_text()
    pipeline.process_save(item(id="i3", code="A1", memo="memo for A1", dm="https://n.site/a1"), cfg, store, deps, [])
    assert c.read_text() == before
    fresh = cfg.vault / store.get("A1").note_path
    assert fresh != c and vault.read_note(fresh)[0]["shortcode"] == "A1"
    assert "memo for A1" in fresh.read_text()


def test_merge_finds_hand_renamed_note(tmp_path):
    cfg, store, deps = make(tmp_path)
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    a = cfg.vault / store.get("A1").note_path
    moved = a.with_name("2026-10-17 my own title.md")
    a.rename(moved)
    assert pipeline.process_save(item(id="i2", code="A1", memo="later"), cfg, store, deps, []) == "merged"
    assert "- later" in moved.read_text()
    assert store.get("A1").note_path == "notes/2026-10-17 my own title.md"


def test_merge_without_note_data_records_error_once(tmp_path):
    def gone(url, workdir, cookies_browser=None):
        raise Unavailable("removed")
    cfg, store, deps = make(tmp_path, fetch=gone)
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    (cfg.vault / store.get("A1").note_path).unlink()
    assert pipeline.process_save(item(id="i2", code="A1", memo="m"), cfg, store, deps, []) == "merged"
    assert [e[0] for e in store.get_meta("run_errors", [])] == ["i2"]


def test_hub_refresh_skips_missing_member(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [0.8, 0.6])
    pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, [])
    (cfg.vault / store.get("A1").note_path).unlink()
    other = {"title": "다른 제목", "summary": "s", "key_facts": [], "keywords": [], "category": None}
    deps = replace(deps, embed=lambda t: [0.9, 0.43], llm=FakeLLM(note=other))
    pipeline.process_save(item(id="i3", code="C3"), cfg, store, deps, [])
    c = store.get("C3")
    assert "hub_error" not in c.data
    assert vault.read_note(cfg.vault / c.note_path)[0]["hub"] == "[[맥 앱]]"


def test_no_ai_cli_on_path_burns_no_attempts(tmp_path):
    from reelnote.llm import Chain
    cfg, store, deps = make(tmp_path)
    def runner(cmd, **kw):
        raise FileNotFoundError(cmd[0])  # launchd PATH without claude/codex
    deps = replace(deps, llm=Chain(store, ("claude", "codex"), 60, runner=runner))
    for i in range(3):
        assert pipeline.process_save(item(id=f"i{i}"), cfg, store, deps, []) == "extracted"
    rec = store.get("C8xYz2Qp")
    assert rec.state == "extracted" and rec.attempts == 0
    errors = store.get_meta("run_errors", [])
    assert len(errors) == 1 and "claude: not found" in errors[0][1]


def test_missing_ffmpeg_does_not_fail_item(tmp_path):
    def no_ffmpeg(v, m, w):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")
    cfg, store, deps = make(tmp_path)
    deps = replace(deps, transcript=no_ffmpeg)
    for i in range(3):
        with pytest.raises(pipeline.ToolUnavailable):
            pipeline.process_save(item(id=f"i{i}"), cfg, store, deps, [])
    rec = store.get("C8xYz2Qp")
    assert rec.state == "fetched" and rec.attempts == 0


def test_ollama_down_stops_run_without_attempts(tmp_path):
    import urllib.error
    def down(text):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    cfg, store, deps, repo, remote = make_repo(tmp_path)
    deps = replace(deps, embed=down)
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/")
    put_inbox(cfg, "002.json", video_url="https://www.instagram.com/reel/B2/")
    pipeline.run_once(cfg, store, deps, repo)
    a = store.get("A1")
    assert a.state == "summarized" and a.attempts == 0
    assert store.get("B2") is None  # run stopped before the next item
    assert (cfg.inbox / "001.json").exists() and (cfg.inbox / "002.json").exists()
    assert any("Connection refused" in e for _, e in store.get_meta("run_errors"))
    assert "Connection refused" in (cfg.vault / "review.md").read_text()
    assert _git(remote, "log", "-1", "--format=%s") == "reelnote: reports\n"


def test_invalid_inbox_item_shows_in_review(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path)
    (cfg.inbox / "001.json").write_text("{not json")
    pipeline.run_once(cfg, store, deps, repo)
    assert (cfg.inbox / "failed" / "001.json").exists()
    review = (cfg.vault / "review.md").read_text()
    assert "- 001: not valid JSON" in review


def test_merged_dm_text_is_inert(tmp_path):
    cfg, store, deps = make(tmp_path, page=lambda url: "<img src=https://evil> ![x](https://evil)")
    pipeline.process_save(item(), cfg, store, deps, [])
    pipeline.process_save(item(id="i2", dm="https://n.site/y"), cfg, store, deps, [])
    body = vault.read_note(only_note(cfg))[1]
    assert "&lt;img" in body and "<img" not in body and "![" not in body


def test_new_hub_never_takes_over_pinned_hub(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    (cfg.vault / "hubs").mkdir()
    pinned = cfg.vault / "hubs" / "맥 앱.md"
    pinned.write_text("---\ncategory: null\npinned: true\n---\n# 맥 앱\n\nmine\n")
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [0.8, 0.6])
    pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, [])
    assert pinned.read_text() == "---\ncategory: null\npinned: true\n---\n# 맥 앱\n\nmine\n"
    assert {h for _, _, h in store.embeddings()} == {"맥 앱 2"}
    assert (cfg.vault / "hubs" / "맥 앱 2.md").exists()


def test_new_hub_with_taken_name_gets_suffix(tmp_path):
    cfg, store, deps = make(tmp_path, vec=(1.0, 0.0))
    pipeline.process_save(item(code="A1"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [0.8, 0.6])
    pipeline.process_save(item(id="i2", code="B2"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [-1.0, 0.0])
    pipeline.process_save(item(id="i3", code="C3"), cfg, store, deps, [])
    deps = replace(deps, embed=lambda t: [-0.8, -0.6])
    pipeline.process_save(item(id="i4", code="D4"), cfg, store, deps, [])
    hubs = {c: h for c, _, h in store.embeddings()}
    assert hubs == {"A1": "맥 앱", "B2": "맥 앱", "C3": "맥 앱 2", "D4": "맥 앱 2"}


def test_handle_error_tolerates_moved_inbox_file(tmp_path):
    cfg, store, deps = make(tmp_path)
    op = item()
    cfg.inbox.mkdir()
    store.create("C8xYz2Qp", op.id, {})
    for _ in range(cfg.max_attempts - 1):
        store.fail_attempt("C8xYz2Qp", "x", cfg.max_attempts)
    pipeline._handle_error(op, RuntimeError("boom"), cfg.inbox / "gone.json", cfg, store)
    assert store.get("C8xYz2Qp").state == "failed"


def test_apply_rename_rewrites_only_matching_pending_saves(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    put_inbox(cfg, "002.json", video_url="https://www.instagram.com/reel/A2/", category="Z")
    (cfg.inbox / "003.json").write_text("{broken")
    (cfg.inbox / "failed").mkdir(exist_ok=True)
    put_inbox(cfg, "failed/004.json", video_url="https://www.instagram.com/reel/A4/", category="X")
    before = {n: (cfg.inbox / n).read_text() for n in ("002.json", "003.json", "failed/004.json")}
    pipeline.apply_rename(RenameOp(id="r", old="X", new="Y"), cfg, store)
    assert json.loads((cfg.inbox / "001.json").read_text())["category"] == "Y"
    assert {n: (cfg.inbox / n).read_text() for n in before} == before


def test_rename_survives_stalled_save_across_runs(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    deps = replace(deps, llm=FakeLLM(limited=True))
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    pipeline.run_once(cfg, store, deps, repo)
    (cfg.vault / "categories.md").write_text("# c\nY\n")
    (cfg.inbox / "002.json").write_text(json.dumps({"op": "rename_category", "from": "X", "to": "Y"}))
    _git(cfg.data_repo, "add", "-A"); _git(cfg.data_repo, "commit", "-qm", "phone")
    pipeline.run_once(cfg, store, deps, repo)
    assert json.loads((cfg.inbox / "001.json").read_text())["category"] == "Y"
    pipeline.run_once(cfg, store, replace(deps, llm=FakeLLM()), repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "Y" and fm["category_source"] == "manual"


def test_chained_rename_rewrites_pending_save(tmp_path):
    cfg, store, deps, repo, _ = make_repo(tmp_path, categories="# c\nX\n")
    deps = replace(deps, llm=FakeLLM(limited=True))
    put_inbox(cfg, "001.json", video_url="https://www.instagram.com/reel/A1/", category="X")
    pipeline.run_once(cfg, store, deps, repo)
    (cfg.vault / "categories.md").write_text("# c\nZ\n")
    for n, a, b in (("002.json", "X", "Y"), ("003.json", "Y", "Z")):
        (cfg.inbox / n).write_text(json.dumps({"op": "rename_category", "from": a, "to": b}))
    _git(cfg.data_repo, "add", "-A"); _git(cfg.data_repo, "commit", "-qm", "phone")
    pipeline.run_once(cfg, store, deps, repo)
    assert json.loads((cfg.inbox / "001.json").read_text())["category"] == "Z"
    pipeline.run_once(cfg, store, replace(deps, llm=FakeLLM()), repo)
    fm, _ = vault.read_note(only_note(cfg))
    assert fm["category"] == "Z" and fm["category_source"] == "manual"


def test_apply_rename_writes_categories_md_atomic_utf8(tmp_path):
    cfg, store, deps = make(tmp_path)
    (cfg.vault / "categories.md").write_bytes("# c\n생산성\n".encode("utf-8"))
    pipeline.apply_rename(RenameOp(id="r1", old="생산성", new="업무툴"), cfg, store)
    assert "업무툴" in (cfg.vault / "categories.md").read_bytes().decode("utf-8")
    assert not list(cfg.vault.glob(".categories.md.*.tmp"))
