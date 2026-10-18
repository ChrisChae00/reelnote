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
