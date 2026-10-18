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
