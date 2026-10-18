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
