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


def test_cli_sync_error_exit_1(tmp_path, monkeypatch, capsys, notified):
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
    assert notified == ["sync failed: push failed: nope"]  # recorder, not osascript


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


def test_notify_passes_message_as_argument(monkeypatch, real_notify):
    from reelnote import cli
    seen = []
    monkeypatch.delenv("REELNOTE_NO_NOTIFY")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd))
    real_notify('a "quoted" msg')
    assert seen and seen[0][0] == "osascript" and seen[0][-1] == 'a "quoted" msg'
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    seen.clear(); real_notify("x")
    assert seen == []
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setenv("REELNOTE_NO_NOTIFY", "1")
    real_notify("x")
    assert seen == []


def test_cli_regroup_limited_exit_1(tmp_path, monkeypatch, capsys):
    from reelnote import pipeline
    from reelnote.datarepo import DataRepo
    from reelnote.llm import AllProvidersLimited
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f'data_repo = "{tmp_path / "data"}"\nstate_dir = "{tmp_path / "state"}"\n')
    monkeypatch.setenv("REELNOTE_CONFIG", str(cfgfile))
    monkeypatch.setattr(DataRepo, "pull", lambda self: None)
    def limited(*a, **k):
        raise AllProvidersLimited("claude: limited; codex: limited")
    monkeypatch.setattr(pipeline, "regroup", limited)
    assert main(["regroup"]) == 1
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "claude: limited" in err


def _run_setup(tmp_path, monkeypatch, head):
    from reelnote import pipeline
    from reelnote.datarepo import DataRepo
    cfgfile = tmp_path / "c.toml"
    cfgfile.write_text(f'data_repo = "{tmp_path / "data"}"\nstate_dir = "{tmp_path / "state"}"\n')
    monkeypatch.setenv("REELNOTE_CONFIG", str(cfgfile))
    monkeypatch.setattr(DataRepo, "remote_head", lambda self: head)
    monkeypatch.setattr(DataRepo, "local_head", lambda self: "after-push")
    runs = []
    monkeypatch.setattr(pipeline, "run_once", lambda *a: runs.append(1) or {})
    return runs


def test_run_offline_is_quiet(tmp_path, monkeypatch, notified):
    runs = _run_setup(tmp_path, monkeypatch, None)
    assert main(["run"]) == 0
    assert runs == [] and notified == []


def test_run_skips_when_remote_unchanged(tmp_path, monkeypatch):
    from reelnote import cli
    runs = _run_setup(tmp_path, monkeypatch, "abc")
    assert main(["run"]) == 0 and runs == [1]
    assert main(["run"]) == 0 and runs == [1, 1]  # our own push moved HEAD to "after-push", not "abc"
    _run_setup(tmp_path, monkeypatch, "after-push")
    runs2 = []
    from reelnote import pipeline
    monkeypatch.setattr(pipeline, "run_once", lambda *a: runs2.append(1) or {})
    assert main(["run"]) == 0 and runs2 == []  # unchanged: no full run
    monkeypatch.setattr(cli.time, "time", lambda: 10**12)  # FULL_RUN_EVERY passed: retry stuck items
    assert main(["run"]) == 0 and runs2 == [1]


def test_run_offline_mid_run_does_not_notify(tmp_path, monkeypatch, notified):
    from reelnote import pipeline
    from reelnote.datarepo import SyncError
    _run_setup(tmp_path, monkeypatch, "abc")
    def boom(*a):
        raise SyncError("pull failed: fatal: unable to access 'https://github.com/x/y.git/': Could not resolve host: github.com")
    monkeypatch.setattr(pipeline, "run_once", boom)
    assert main(["run"]) == 1
    assert notified == []
