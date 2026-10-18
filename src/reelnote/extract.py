import io
import ipaddress
import re
import socket
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import trafilatura


class PageUnavailable(Exception):
    pass


def _run(run, tool: str, cmd: list[str], timeout: int):
    res = run(cmd, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        lines = [l for l in (res.stderr or "").strip().splitlines() if l.strip()]
        raise RuntimeError(f"{tool} failed: {lines[-1] if lines else 'exit ' + str(res.returncode)}")
    return res


def transcript(video: Path, model: Path, workdir: Path, run=subprocess.run) -> str:
    wav = workdir / "audio.wav"
    try:
        _run(run, "ffmpeg", ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-ar", "16000", "-ac", "1", str(wav)], 600)
    except RuntimeError as e:
        if "does not contain any stream" in str(e):
            return ""  # video has no audio track
        raise
    _run(run, "whisper-cli", ["whisper-cli", "-m", str(model), "-f", str(wav), "-l", "auto", "-otxt",
                              "-of", str(workdir / "audio"), "-np"], 1800)
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
    for old in frames.iterdir():
        if old.is_file():
            old.unlink()
    _run(run, "ffmpeg", ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "fps=1",
                         "-frames:v", "300", str(frames / "%04d.png")], 600)
    return dedupe_lines([ocr(p) for p in sorted(frames.glob("*.png"))])


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PageUnavailable(f"{url}: only http/https URLs are allowed")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None)
    except OSError as e:
        raise PageUnavailable(f"{url}: cannot resolve host: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        ip = getattr(ip, "ipv4_mapped", None) or ip
        if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise PageUnavailable(f"{url}: refusing non-public address {ip}")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_bytes(url: str) -> tuple[bytes, str]:
    # shortcut: DNS is resolved again at connect time (rebinding window), upgrade by pinning the checked IP
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) reelnote"})
    with urllib.request.build_opener(_CheckedRedirect).open(req, timeout=20) as r:
        data = r.read(10_000_001)
        if len(data) > 10_000_000:
            raise PageUnavailable(f"{url}: too large")
        return data, r.headers.get("Content-Type", "")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def page_text(url: str, fetch_bytes=None) -> str:
    try:
        data, ctype = (fetch_bytes or _fetch_bytes)(url)
    except Exception as e:
        raise PageUnavailable(f"{url}: {e}") from e
    try:
        if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
            text = _pdf_text(data)
        else:
            text = trafilatura.extract(data.decode("utf-8", errors="replace")) or ""
    except Exception as e:
        raise PageUnavailable(f"{url}: unreadable content: {e}") from e
    if not text.strip():
        raise PageUnavailable(f"{url}: no readable text (login wall or empty page)")
    return text.strip()
