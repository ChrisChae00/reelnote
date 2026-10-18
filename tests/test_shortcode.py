import pytest
from reelnote.shortcode import shortcode, canonical_url, NotInstagramPost

@pytest.mark.parametrize("url", [
    "https://www.instagram.com/reel/C8xYz2Qp/",
    "https://www.instagram.com/reel/C8xYz2Qp/?igsh=MWx0b2N3",
    "https://instagram.com/reels/C8xYz2Qp",
    "https://www.instagram.com/p/C8xYz2Qp/",
    "https://m.instagram.com/reel/C8xYz2Qp/",
    "https://www.instagram.com/some.creator/reel/C8xYz2Qp/",
    "  https://www.instagram.com/reel/C8xYz2Qp/  ",
])
def test_variants_share_one_shortcode(url):
    assert shortcode(url) == "C8xYz2Qp"

@pytest.mark.parametrize("url", [
    "https://www.instagram.com/some.creator/",
    "https://example.com/reel/C8xYz2Qp/",
    "ftp://www.instagram.com/reel/C8xYz2Qp/",
    "not a url",
])
def test_rejects_non_posts(url):
    with pytest.raises(NotInstagramPost):
        shortcode(url)

def test_canonical_url():
    assert canonical_url("C8xYz2Qp") == "https://www.instagram.com/p/C8xYz2Qp/"
