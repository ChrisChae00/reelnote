from dataclasses import dataclass

import numpy as np
from sklearn.cluster import HDBSCAN

from reelnote.embed import cosine


@dataclass
class Match:
    kind: str
    other: str | None
    hub: str | None
    score: float


def match(vec, others, dup_t: float, hub_t: float) -> Match:
    if not others:
        return Match("none", None, None, 0.0)
    score, code, hub = max((cosine(vec, v), c, h) for c, v, h in others)
    if score >= dup_t:
        return Match("duplicate", code, hub, score)
    if score >= hub_t:
        return Match("hub", code, hub, score) if hub else Match("new_hub", code, None, score)
    return Match("none", None, None, score)


def clusters(items: list[tuple[str, list[float]]], min_size: int = 2) -> list[list[str]]:
    items = [(c, v) for c, v in items if any(v)]  # zero vectors have no direction; never cluster them
    if len(items) < min_size + 1:
        return []
    x = np.asarray([v for _, v in items], dtype=np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)  # euclidean on unit vectors ranks like cosine
    labels = HDBSCAN(min_cluster_size=min_size, copy=True).fit_predict(x)
    groups: dict[int, list[str]] = {}
    for (code, _), label in zip(items, labels):
        if label >= 0:
            groups.setdefault(int(label), []).append(code)
    return list(groups.values())
