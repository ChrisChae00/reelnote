import io
import re
import subprocess
import urllib.request
from pathlib import Path

import trafilatura


class PageUnavailable(Exception):
    pass


def transcript(video: Path, model: Path, workdir: Path, run=subprocess.run) -> str:
    wav = workdir / "audio.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-ar", "16000", "-ac", "1", str(wav)],
        check=True, timeout=600)
    run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", "auto", "-otxt", "-of", str(workdir / "audio"), "-np"],
        check=True, timeout=1800, capture_output=True)
    return (workdir / "audio.txt").read_text(encoding="utf-8").strip()


def _vision_ocr(path: Path) -> list[str]:
    from ocrmac import ocrmac
    return [t for t, _conf, _box in ocrmac.OCR(str(path), language_preference=["ko-KR", "en-US"]).recognize()]


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().casefold()


def dedupe_lines(frames: list[list[str]]) -> list[str]:
    seen, out = set(), []
    for lines in frames:
        for line in lines:
            key = _norm(line)
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            out.append(line)
    return out


def frame_texts(video: Path, workdir: Path, run=subprocess.run, ocr=None) -> list[str]:
    ocr = ocr or _vision_ocr
    frames = workdir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=1", str(frames / "%04d.png")],
        check=True, timeout=600)
    return dedupe_lines([ocr(p) for p in sorted(frames.glob("*.png"))])


def _fetch_bytes(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) reelnote"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(), r.headers.get("Content-Type", "")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def page_text(url: str, fetch_bytes=None) -> str:
    try:
        data, ctype = (fetch_bytes or _fetch_bytes)(url)
    except Exception as e:
        raise PageUnavailable(f"{url}: {e}") from e
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
        text = _pdf_text(data)
    else:
        text = trafilatura.extract(data.decode("utf-8", errors="replace")) or ""
    if not text.strip():
        raise PageUnavailable(f"{url}: no readable text (login wall or empty page)")
    return text.strip()
