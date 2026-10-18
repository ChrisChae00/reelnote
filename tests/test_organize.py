import json
from reelnote.embed import cosine, embed
from reelnote.organize import match, clusters

A, A2, B = [1.0, 0.0, 0.0], [0.99, 0.1, 0.0], [0.0, 1.0, 0.0]

def test_cosine():
    assert round(cosine(A, A), 6) == 1.0
    assert round(cosine(A, B), 6) == 0.0

def test_embed_posts_to_ollama():
    seen = {}
    def post(url, body):
        seen["url"], seen["body"] = url, body
        return {"embeddings": [[0.1, 0.2]]}
    assert embed("hello", "http://localhost:11434", post=post) == [0.1, 0.2]
    assert seen["url"].endswith("/api/embed") and seen["body"]["model"] == "bge-m3"

def test_match_duplicate():
    m = match(A2, [("C1", A, "Mac apps")], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.other, m.hub) == ("duplicate", "C1", "Mac apps")

def test_match_existing_hub():
    m = match([0.8, 0.6, 0.0], [("C1", A, "Mac apps")], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.hub) == ("hub", "Mac apps")

def test_match_new_hub_when_neighbor_unassigned():
    m = match([0.8, 0.6, 0.0], [("C1", A, None)], dup_t=0.98, hub_t=0.75)
    assert (m.kind, m.other) == ("new_hub", "C1")

def test_match_none_and_empty():
    assert match(B, [("C1", A, "x")], 0.98, 0.75).kind == "none"
    assert match(B, [], 0.98, 0.75).kind == "none"

def test_clusters_groups_close_vectors():
    items = [("a", [1, 0, 0]), ("b", [0.98, 0.05, 0]), ("c", [0, 1, 0]), ("d", [0.02, 0.99, 0]), ("e", [0, 0, 1])]
    groups = sorted(sorted(g) for g in clusters(items))
    assert groups == [["a", "b"], ["c", "d"]]

def test_clusters_too_few():
    assert clusters([("a", [1, 0])]) == []

def test_clusters_ignores_zero_vectors():
    items = [("a", [1, 0, 0]), ("b", [0.98, 0.05, 0]), ("c", [0, 1, 0]), ("d", [0.02, 0.99, 0]), ("e", [0, 0, 1]), ("z", [0, 0, 0])]
    groups = sorted(sorted(g) for g in clusters(items))
    assert groups == [["a", "b"], ["c", "d"]]


def test_embed_strips_trailing_slash():
    seen = {}
    def post(url, body):
        seen["url"] = url
        return {"embeddings": [[0.1]]}
    embed("x", "http://localhost:11434/", post=post)
    assert seen["url"] == "http://localhost:11434/api/embed"
