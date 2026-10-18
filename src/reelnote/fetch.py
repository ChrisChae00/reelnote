import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOGIN = re.compile(r"login required|rate-limit reached|cookies", re.I)
_GONE = re.compile(r"not available|isn't available|404|private|removed", re.I)


class Unavailable(Exception):
    pass


class LoginRequired(Exception):
    pass


@dataclass
class Download:
    video: Path
    caption: str
    author: str | None


def _attempt(url: str, workdir: Path, cookies_browser: str | None, run) -> Download:
    cmd = ["yt-dlp", "--no-playlist", "--no-progress", "--write-info-json", "-f", "mp4/best",
           "-o", str(workdir / "%(id)s.%(ext)s")]
    if cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    res = run(cmd + [url], capture_output=True, text=True, timeout=600)
    if res.returncode != 0:
        err = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "yt-dlp failed"
        if _LOGIN.search(res.stderr):
            raise LoginRequired(err)
        if _GONE.search(res.stderr):
            raise Unavailable(err)
        raise RuntimeError(err)
    info_path = next(workdir.glob("*.info.json"))
    info = json.loads(info_path.read_text())
    video = next(p for p in workdir.glob(f"{info['id']}.*") if not p.name.endswith(".json"))
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
