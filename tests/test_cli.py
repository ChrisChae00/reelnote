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


def test_cli_sync_error_exit_1(tmp_path, monkeypatch, capsys):
    from reelnote import pipeline
    from reelnote.datarepo import SyncError
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f'data_repo = "{tmp_path / "data"}"\nstate_dir = "{tmp_path / "state"}"\n')
    monkeypatch.setenv("REELNOTE_CONFIG", str(cfgfile))
    def boom(*a, **k):
        raise SyncError("push failed: nope")
    monkeypatch.setattr(pipeline, "run_once", boom)
    assert main(["run"]) == 1
    assert "sync failed" in capsys.readouterr().err


def test_cli_sync_error_notifies(tmp_path, monkeypatch):
    from reelnote import cli, pipeline
    from reelnote.datarepo import SyncError
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f'data_repo = "{tmp_path / "data"}"\nstate_dir = "{tmp_path / "state"}"\n')
    monkeypatch.setenv("REELNOTE_CONFIG", str(cfgfile))
    def boom(*a, **k):
        raise SyncError("pull conflict in ['vault/notes/a.md']")
    monkeypatch.setattr(pipeline, "run_once", boom)
    sent = []
    monkeypatch.setattr(cli, "notify", sent.append)
    assert main(["run"]) == 1
    assert sent and "pull conflict" in sent[0]


def test_notify_passes_message_as_argument(monkeypatch):
    from reelnote import cli
    seen = []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd))
    cli.notify('a "quoted" msg')
    assert seen and seen[0][0] == "osascript" and seen[0][-1] == 'a "quoted" msg'
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    seen.clear(); cli.notify("x")
    assert seen == []
