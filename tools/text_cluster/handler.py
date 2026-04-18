"""Text clustering — TF-IDF + KMeans or simple keyword grouping fallback."""

from __future__ import annotations
import math
import re
from collections import Counter
from typing import Any
from tools.base import ToolHandler

_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "it", "i", "we", "you", "they",
              "to", "of", "in", "and", "or", "for", "on", "at", "with", "this", "that", "be"}


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text) if w.lower() not in _STOPWORDS]


def _simple_cluster(texts: list[str], n: int) -> list[dict]:
    """Fallback: group by most common term."""
    all_terms: Counter = Counter()
    term_texts: dict[str, list[int]] = {}
    for idx, text in enumerate(texts):
        for tok in set(_tokenize(text)):
            all_terms[tok] += 1
            term_texts.setdefault(tok, []).append(idx)

    top_terms = [t for t, _ in all_terms.most_common(n * 3) if all_terms[t] > 1][:n]
    assigned = set()
    clusters = []
    for term in top_terms:
        members = [i for i in term_texts.get(term, []) if i not in assigned][:20]
        if not members:
            continue
        assigned.update(members)
        clusters.append({
            "theme": term,
            "size": len(members),
            "examples": [texts[i][:150] for i in members[:3]],
        })
    return clusters


class TextClusterHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        texts = inputs.get("texts", [])
        n_clusters = int(inputs.get("n_clusters", 0)) or min(8, max(3, len(texts) // 10))
        min_size = int(inputs.get("min_cluster_size", 2))

        if len(texts) < 3:
            return {"clusters": [], "count": 0, "note": "Too few texts to cluster"}

        # Try sklearn
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.cluster import KMeans

            vec = TfidfVectorizer(max_features=500, stop_words="english")
            X = vec.fit_transform(texts[:500])
            k = min(n_clusters, len(texts))
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X)

            feature_names = vec.get_feature_names_out()
            clusters = []
            for cluster_id in range(k):
                members = [i for i, l in enumerate(labels) if l == cluster_id]
                if len(members) < min_size:
                    continue
                # Top terms for this cluster
                center = km.cluster_centers_[cluster_id]
                top_term_ids = center.argsort()[-5:][::-1]
                theme = ", ".join(feature_names[i] for i in top_term_ids)
                clusters.append({
                    "theme": theme,
                    "size": len(members),
                    "examples": [texts[i][:150] for i in members[:3]],
                })
            clusters.sort(key=lambda c: -c["size"])
            return {"clusters": clusters, "count": len(clusters), "provider": "sklearn"}
        except ImportError:
            pass

        # Simple fallback
        clusters = _simple_cluster(texts, n_clusters)
        clusters = [c for c in clusters if c["size"] >= min_size]
        return {"clusters": clusters, "count": len(clusters), "provider": "keyword_fallback"}

    async def self_test(self) -> bool:
        texts = ["the app crashes often", "great product love it", "crashes on startup", "love the features", "bug in login screen"]
        result = await self._run({"texts": texts})
        return "clusters" in result


handler = TextClusterHandler()
