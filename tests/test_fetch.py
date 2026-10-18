import json
import subprocess
import pytest
from reelnote.fetch import download, Unavailable, LoginRequired

def fake_run_factory(tmp_path, outcomes):
    """outcomes: list of 'ok' | 'login' | 'gone', consumed one per yt-dlp call."""
    calls = []
    def run(cmd, **kw):
        calls.append(cmd)
        outcome = outcomes[len(calls) - 1]
        if outcome == "ok":
            (tmp_path / "C8xYz2Qp.mp4").write_bytes(b"\0")
            (tmp_path / "C8xYz2Qp.info.json").write_text(json.dumps(
                {"id": "C8xYz2Qp", "description": "앱 7개", "uploader": "abc"}))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        err = {"login": "ERROR: [Instagram] C8xYz2Qp: Requested content is not available, rate-limit reached or login required",
               "gone": "ERROR: [Instagram] C8xYz2Qp: This content isn't available"}[outcome]
        return subprocess.CompletedProcess(cmd, 1, "", err)
    return run, calls

URL = "https://www.instagram.com/reel/C8xYz2Qp/"

def test_download_ok(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["ok"])
    d = download(URL, tmp_path, run=run)
    assert d.video.name == "C8xYz2Qp.mp4" and d.caption == "앱 7개" and d.author == "abc"
    assert "--cookies-from-browser" not in calls[0]

def test_login_wall_retries_with_cookies(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["login", "ok"])
    download(URL, tmp_path, cookies_browser="chrome", run=run)
    assert calls[1][calls[1].index("--cookies-from-browser") + 1] == "chrome"

def test_login_wall_without_cookies_raises(tmp_path):
    run, _ = fake_run_factory(tmp_path, ["login"])
    with pytest.raises(LoginRequired):
        download(URL, tmp_path, run=run)

def test_gone_raises_unavailable(tmp_path):
    run, _ = fake_run_factory(tmp_path, ["gone"])
    with pytest.raises(Unavailable):
        download(URL, tmp_path, run=run)


def _err_run(stderr):
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", stderr)


def test_stale_files_ignored(tmp_path):
    (tmp_path / "old.info.json").write_text(json.dumps({"id": "old", "description": "old"}))
    (tmp_path / "old.mp4").write_bytes(b"\0")
    run, _ = fake_run_factory(tmp_path, ["ok"])
    d = download(URL, tmp_path, run=run)
    assert d.caption == "앱 7개" and d.video.name == "C8xYz2Qp.mp4"
    assert not (tmp_path / "old.mp4").exists()


def test_no_info_json_raises(tmp_path):
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", "")
    with pytest.raises(RuntimeError, match="no info.json"):
        download(URL, tmp_path, run=run)


@pytest.mark.parametrize("stderr", [
    "ERROR: [Instagram] X: Requested format is not available",
    "WARNING: private stuff\nERROR: something broke",
    "ERROR: could not find chrome cookies database",
])
def test_other_errors_are_retryable_runtime(tmp_path, stderr):
    with pytest.raises(RuntimeError) as ei:
        download(URL, tmp_path, run=_err_run(stderr))
    assert not isinstance(ei.value, (Unavailable, LoginRequired))


def test_login_after_cookie_retry_stays_login_required(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["login", "login"])
    with pytest.raises(LoginRequired):
        download(URL, tmp_path, cookies_browser="chrome", run=run)
    assert len(calls) == 2


def test_url_after_double_dash(tmp_path):
    run, calls = fake_run_factory(tmp_path, ["ok"])
    download(URL, tmp_path, run=run)
    assert calls[0][-2:] == ["--", URL]
