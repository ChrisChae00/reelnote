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
    {"tags": "AI"},
    {"tags": [1]},
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


@pytest.mark.parametrize("created_at", ["../../../escape", "yesterday", 5])
def test_created_at_must_be_iso(tmp_path, created_at):
    with pytest.raises(InvalidInboxItem):
        parse_item(write(tmp_path, {**GOOD, "created_at": created_at}))


def test_save_tags_normalized(tmp_path):
    item = parse_item(write(tmp_path, {**GOOD, "tags": ["Job Hunting", "#AI", "AI", " "]}))
    assert item.tags == ("Job_Hunting", "AI")
