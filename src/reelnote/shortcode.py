import re
from urllib.parse import urlparse

_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_PATH = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)/?")


class NotInstagramPost(ValueError):
    pass


def shortcode(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or u.netloc.lower() not in _HOSTS:
        raise NotInstagramPost(url)
    m = _PATH.match(u.path)
    if not m:
        raise NotInstagramPost(url)
    return m.group(1)


def canonical_url(code: str) -> str:
    # /p/ opens both reels and photo posts
    return f"https://www.instagram.com/p/{code}/"
