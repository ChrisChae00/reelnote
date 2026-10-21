import json
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from reelnote.shortcode import NotInstagramPost, shortcode
from reelnote.vault import merge_tags

AUTO = "auto"


class InvalidInboxItem(ValueError):
    pass


@dataclass(frozen=True)
class SaveItem:
    id: str
    video_url: str
    dm_url: str | None
    memo: str | None
    category: str
    created_at: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenameOp:
    id: str
    old: str
    new: str


def _opt_str(d: dict, key: str) -> str | None:
    v = d.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise InvalidInboxItem(f"{key} must be a string")
    return v.strip() or None


def _req_str(d: dict, key: str) -> str:
    v = _opt_str(d, key)
    if v is None:
        raise InvalidInboxItem(f"{key} is required")
    return v


def parse_item(path: Path) -> SaveItem | RenameOp:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidInboxItem(f"not valid JSON: {e}") from e
    if not isinstance(d, dict):
        raise InvalidInboxItem("top level must be an object")
    op = d.get("op")
    if op == "rename_category":
        old, new = _req_str(d, "from"), _req_str(d, "to")
        if old == new:
            raise InvalidInboxItem("rename to the same name")
        return RenameOp(id=path.stem, old=old, new=new)
    if op != "save":
        raise InvalidInboxItem(f"unknown op {op!r}")
    video_url = _req_str(d, "video_url")
    try:
        shortcode(video_url)
    except NotInstagramPost as e:
        raise InvalidInboxItem(f"not an Instagram post URL: {video_url}") from e
    dm_url = _opt_str(d, "dm_url")
    if dm_url and urlparse(dm_url).scheme not in ("http", "https"):
        raise InvalidInboxItem(f"dm_url must be http(s): {dm_url}")
    created_at = _req_str(d, "created_at")
    try:
        datetime.fromisoformat(created_at)  # it becomes part of the note filename
    except ValueError as e:
        raise InvalidInboxItem(f"created_at must be an ISO 8601 date: {created_at}") from e
    tags = d.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise InvalidInboxItem("tags must be a list of strings")
    return SaveItem(id=path.stem, video_url=video_url, dm_url=dm_url, memo=_opt_str(d, "memo"),
                    category=_req_str(d, "category"), created_at=created_at, tags=tuple(merge_tags(tags)))
