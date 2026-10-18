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

import re

def _with_user_lines(p):
    p.write_text(p.read_text().replace("saved_at:", "related: [[Foo]]  # my link\n# keep me\nsaved_at:"))

def test_update_frontmatter_preserves_user_text(tmp_path):
    p = make_note(tmp_path)
    _with_user_lines(p)
    vault.update_frontmatter(p, hub="[[X]]")
    text = p.read_text()
    assert "related: [[Foo]]  # my link\n" in text and "# keep me\n" in text
    assert vault.read_note(p)[0]["hub"] == "[[X]]"

def test_append_preserves_frontmatter_text(tmp_path):
    p = make_note(tmp_path)
    _with_user_lines(p)
    before = vault._split(p.read_text())[0]
    vault.append_to_section(p, "My memo", "- m")
    after = p.read_text()
    assert vault._split(after)[0] == before
    assert "related: [[Foo]]  # my link\n# keep me\n" in after

def test_append_ignores_h3_lookalike(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\nshortcode: C1\n---\n# t\n\n### My memo\n\nold\n\n## My memo\n\n- real\n")
    vault.append_to_section(p, "My memo", "- new")
    body = vault.read_note(p)[1]
    real = body[re.search(r"(?m)^## My memo$", body).start():]
    assert "- new" in real and "- real" in real
    assert "- new" not in body[:re.search(r"(?m)^## My memo$", body).start()]

def test_append_finds_heading_at_eof_without_newline(tmp_path):
    p = tmp_path / "n.md"
    p.write_text("---\nshortcode: C1\n---\n# t\n\n## My memo")
    vault.append_to_section(p, "My memo", "- m")
    body = vault.read_note(p)[1]
    assert body.count("## My memo") == 1 and body.endswith("## My memo\n\n- m\n")

def test_notes_with_category_skips_broken_yaml(tmp_path):
    (tmp_path / "bad.md").write_text("---\ncategory: [unclosed\n---\n# b\n")
    p = make_note(tmp_path)
    assert vault.notes_with_category(tmp_path, {"생산성"}) == [p]

def test_upsert_hub_with_only_start_marker_keeps_text(tmp_path):
    p = tmp_path / "Hub.md"
    p.write_text("---\ncategory: x\n---\n# Hub\n\n<!-- reelnote:start -->\nmy text\n")
    vault.upsert_hub(tmp_path, "Hub", category="x", description="d", members=[("a", ["f"])])
    assert p.read_text().startswith("---\ncategory: x\n---\n# Hub\n\n<!-- reelnote:start -->\nmy text\n")
    assert "## Notes" in p.read_text()


def test_note_filename_stays_in_notes_dir(tmp_path):
    notes = tmp_path / "notes"
    assert vault.note_filename(notes, "2026-10-17", "../../x").parent == notes
    with pytest.raises(ValueError):
        vault.note_filename(notes, "../../../escape", "t")


def test_hub_name_stays_in_hubs_dir(tmp_path):
    hubs = tmp_path / "hubs"
    p = vault.upsert_hub(hubs, "../../evil", category=None, description="d", members=[])
    assert p.parent == hubs


EVIL = ["![x](https://evil/?d=1)", "<img src=https://evil>", "<iframe src=https://evil></iframe>"]


def test_untrusted_text_renders_inert(tmp_path):
    text = vault.render_note(FM, title=EVIL[1], summary=EVIL[0], key_facts=EVIL, dm=[("https://n.site", EVIL[2])],
                             memos=[], caption=EVIL[0], transcript=EVIL[1], screen_text=EVIL[2])
    body = vault._split(text)[1]
    assert "![" not in body and "<img" not in body and "<iframe" not in body
    assert "!\\[x](https://evil/?d=1)" in body and "&lt;img src=https://evil>" in body
    hub = vault.upsert_hub(tmp_path, "<img src=x>", category=None, description=EVIL[0],
                           members=[("2026-10-17 a", EVIL)]).read_text()
    assert "![" not in hub and "<img" not in hub
    vault.write_review(tmp_path / "review.md", duplicates=[], failed=[(EVIL[1], EVIL[0])], unavailable=[],
                       dm_errors=[("a", EVIL[2])])
    review = (tmp_path / "review.md").read_text()
    assert "![" not in review and "<img" not in review and "<iframe" not in review


def test_plain_links_survive_inert():
    assert vault._inert("see [docs](https://x.com) and https://y.com") == "see [docs](https://x.com) and https://y.com"
