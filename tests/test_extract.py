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
