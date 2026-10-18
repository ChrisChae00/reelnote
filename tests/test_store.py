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

def test_unknown_code_raises_keyerror(tmp_path):
    import pytest
    s = Store(tmp_path / "db.sqlite")
    with pytest.raises(KeyError):
        s.update("nope", state="fetched")
    with pytest.raises(KeyError):
        s.fail_attempt("nope", "boom", 3)
    with pytest.raises(KeyError):
        s.reset("nope")
    with pytest.raises(KeyError):
        s.set_hub("nope", "Mac apps")
