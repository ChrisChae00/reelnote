import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOGIN = re.compile(r"login required|rate-limit reached", re.I)
_GONE = re.compile(r"isn't available|is not available anymore|video unavailable|http error 404|private video|has been removed", re.I)
_FORMAT_PART = re.compile(r"\.f\d+\.")


class Unavailable(Exception):
    pass


class LoginRequired(Exception):
    pass


@dataclass
class Download:
    video: Path
    caption: str
    author: str | None


def _error_line(stderr: str) -> str:
    lines = [l for l in stderr.strip().splitlines() if l.strip()]
    errs = [l for l in lines if l.startswith("ERROR:")]
    return (errs or lines or ["yt-dlp failed"])[-1]


def _attempt(url: str, workdir: Path, cookies_browser: str | None, run) -> Download:
    for f in workdir.iterdir():  # stale files from earlier runs must not be picked up
        if f.is_file():
            f.unlink()
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--write-info-json", "-f", "mp4/best",
           "-o", str(workdir / "%(id)s.%(ext)s")]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    res = run(cmd + ["--", url], capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        err = _error_line(res.stderr or "")
        if _LOGIN.search(err):
            raise LoginRequired(err)
        if _GONE.search(err):
            raise Unavailable(err)
        raise RuntimeError(err)
    info_path = next(workdir.glob("*.info.json"), None)
    if info_path is None:
        raise RuntimeError("yt-dlp wrote no info.json")
    info = json.loads(info_path.read_text())
    video = next(p for p in workdir.glob(f"{info['id']}.*")
                 if p.suffix not in (".json", ".part") and not _FORMAT_PART.search(p.name))
    return Download(video=video, caption=info.get("description") or "",
                    author=info.get("uploader") or info.get("channel"))


def download(url: str, workdir: Path, cookies_browser: str | None = None, run=subprocess.run) -> Download:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        return _attempt(url, workdir, None, run)
    except LoginRequired:
        if not cookies_browser:
            raise
        return _attempt(url, workdir, cookies_browser, run)
