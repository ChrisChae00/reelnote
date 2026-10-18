import pytest
from reelnote.extract import dedupe_lines, page_text, frame_texts, PageUnavailable

def test_dedupe_lines_keeps_first_and_drops_noise():
    frames = [["Raycast", "무료  런처"], ["raycast", "무료 런처", "x"], ["Rectangle"]]
    assert dedupe_lines(frames) == ["Raycast", "무료  런처", "Rectangle"]

def test_page_text_html():
    html = b"<html><body><nav>menu</nav><article><h1>Mac apps</h1><p>" + "Raycast is a launcher. ".encode() * 20 + b"</p></article></body></html>"
    text = page_text("https://example.com/a", fetch_bytes=lambda url: (html, "text/html"))
    assert "Raycast is a launcher." in text

def test_page_text_pdf_routes_to_pdf_reader(monkeypatch):
    import reelnote.extract as ex
    monkeypatch.setattr(ex, "_pdf_text", lambda data: "PDF BODY")
    assert page_text("https://x/y.pdf", fetch_bytes=lambda url: (b"%PDF", "application/pdf")) == "PDF BODY"

def test_page_text_empty_raises():
    with pytest.raises(PageUnavailable):
        page_text("https://x", fetch_bytes=lambda url: (b"<html></html>", "text/html"))

def test_page_text_fetch_error_raises():
    def boom(url):
        raise OSError("403")
    with pytest.raises(PageUnavailable):
        page_text("https://x", fetch_bytes=boom)

def test_frame_texts_uses_ocr_per_frame(tmp_path):
    def run(cmd, **kw):
        out = tmp_path / "frames"
        for i in (1, 2):
            (out / f"{i:04d}.png").write_bytes(b"\0")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    texts = frame_texts(tmp_path / "v.mp4", tmp_path, run=run, ocr=lambda p: ["Raycast", "무료"])
    assert texts == ["Raycast", "무료"]


import socket
import subprocess
import urllib.request
from reelnote import extract as ex


def _resolve_to(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 0))])


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/"])
def test_check_url_rejects(url):
    with pytest.raises(PageUnavailable):
        ex._check_url(url)


def test_check_url_rejects_private_resolution(monkeypatch):
    _resolve_to(monkeypatch, "10.0.0.5")
    with pytest.raises(PageUnavailable):
        ex._check_url("http://evil.example/")


def test_check_url_accepts_public(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    ex._check_url("https://example.com/")


def test_check_url_accepts_public_ipv6(monkeypatch):
    _resolve_to(monkeypatch, "2606:2800:220:1:248:1893:25c8:1946")
    ex._check_url("https://example.com/")


def test_redirect_to_metadata_rejected():
    h = ex._CheckedRedirect()
    req = urllib.request.Request("https://example.com/")
    with pytest.raises(PageUnavailable):
        h.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/")


def test_pdf_parse_error_is_page_unavailable(monkeypatch):
    def bad(data):
        raise ValueError("corrupt")
    monkeypatch.setattr(ex, "_pdf_text", bad)
    with pytest.raises(PageUnavailable):
        page_text("https://x/y.pdf", fetch_bytes=lambda url: (b"x", "application/pdf"))


def _failing(stderr):
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", stderr)


def test_transcript_no_audio_returns_empty(tmp_path):
    run = _failing("Output file #0 does not contain any stream")
    assert ex.transcript(tmp_path / "v.mp4", tmp_path / "m.bin", tmp_path, run=run) == ""


def test_transcript_ffmpeg_failure_raises(tmp_path):
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        ex.transcript(tmp_path / "v.mp4", tmp_path / "m.bin", tmp_path, run=_failing("boom"))


def test_frame_texts_ignores_stale_frames(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "9999.png").write_bytes(b"\0")
    seen = []
    def run(cmd, **kw):
        (tmp_path / "frames" / "0001.png").write_bytes(b"\0")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    ex.frame_texts(tmp_path / "v.mp4", tmp_path, run=run, ocr=lambda p: seen.append(p.name) or ["Hello"])
    assert seen == ["0001.png"]


def test_connect_time_rebinding_blocked(monkeypatch):
    answers = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (next(answers), 0))])
    used = []
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: used.append(addr))
    with pytest.raises(PageUnavailable):
        page_text("http://rebind.example/")
    assert not any(a[0] == "127.0.0.1" for a in used)


def test_connect_uses_validated_ip(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    used = []
    def fake(addr, *a, **k):
        used.append(addr)
        raise OSError("stop")
    monkeypatch.setattr(socket, "create_connection", fake)
    with pytest.raises(PageUnavailable):
        page_text("http://example.com/")
    assert used == [("93.184.216.34", 80)]


def test_env_proxy_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    _resolve_to(monkeypatch, "93.184.216.34")
    used = []
    def fake(addr, *a, **k):
        used.append(addr)
        raise OSError("stop")
    monkeypatch.setattr(socket, "create_connection", fake)
    with pytest.raises(PageUnavailable):
        page_text("http://example.com/")
    assert used == [("93.184.216.34", 80)]


@pytest.mark.parametrize(
    "ip",
    [
        "100.64.0.1",
        "64:ff9b::7f00:1",
        "2002:c0a8:0101::1",
        "198.18.0.1",
        "fec0::1",
        "::7f00:1",
        "2002:7f00:1::1",
    ],
)
def test_non_global_addresses_rejected(monkeypatch, ip):
    _resolve_to(monkeypatch, ip)
    with pytest.raises(PageUnavailable):
        ex._check_url("http://host.example/")


def test_opener_has_no_file_handler():
    with pytest.raises(PageUnavailable):
        ex._opener().open("file:///etc/passwd")
