import http.client
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


def _check_ip(ip_str: str, url: str):
    ip = ipaddress.ip_address(ip_str.split("%")[0])
    if ip.version == 6:
        if ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        elif ip.sixtofour:
            ip = ip.sixtofour
        elif ip in ipaddress.ip_network("64:ff9b::/96"):
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if not ip.is_global or ip.is_multicast:
        raise PageUnavailable(f"{url}: refusing non-public address {ip}")
    return ip


def _resolve(host: str, port, url: str) -> list[str]:
    try:
        return [i[4][0] for i in socket.getaddrinfo(host, port)]
    except OSError as e:
        raise PageUnavailable(f"{url}: cannot resolve host: {e}") from e


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PageUnavailable(f"{url}: only http/https URLs are allowed")
    for addr in _resolve(parts.hostname, parts.port or None, url):
        _check_ip(addr, url)


def _safe_socket(host: str, port: int, timeout):
    # connect to the exact IP we validated, so DNS cannot change between check and connect
    addr = _resolve(host, port, host)[0]
    _check_ip(addr, host)
    return socket.create_connection((addr, port), timeout)


class _SafeHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _safe_socket(self.host, self.port, self.timeout)


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _safe_socket(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req, context=self._context)


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoFTP(urllib.request.FTPHandler):
    def ftp_open(self, req):
        raise PageUnavailable("scheme not allowed")


class _NoFile(urllib.request.FileHandler):
    def file_open(self, req):
        raise PageUnavailable("scheme not allowed")


class _NoData(urllib.request.DataHandler):
    def data_open(self, req):
        raise PageUnavailable("scheme not allowed")


def _opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _SafeHTTPHandler, _SafeHTTPSHandler, _CheckedRedirect,
        _NoFTP, _NoFile, _NoData)


def _fetch_bytes(url: str) -> tuple[bytes, str]:
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) reelnote"})
    with _opener().open(req, timeout=20) as r:
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
