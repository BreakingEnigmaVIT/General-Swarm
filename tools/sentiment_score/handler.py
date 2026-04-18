"""Sentiment scorer — TextBlob/VADER local or HuggingFace transformers."""

from __future__ import annotations
from typing import Any
from tools.base import ToolHandler

_POSITIVE_WORDS = {"great", "love", "excellent", "amazing", "fantastic", "helpful", "easy", "fast", "good", "best", "awesome"}
_NEGATIVE_WORDS = {"hate", "terrible", "awful", "broken", "bug", "crash", "slow", "bad", "worst", "useless", "annoying", "horrible", "frustrating"}


def _lexicon_score(text: str) -> dict:
    """Simple lexicon-based scorer — no external dependencies."""
    words = set(text.lower().split())
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return {"label": "neutral", "score": 0.0, "positive": 0, "negative": 0}
    score = (pos - neg) / total
    label = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"
    return {"label": label, "score": round(score, 3), "positive": pos, "negative": neg}


class SentimentScoreHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        texts = inputs.get("texts", [])
        provider = inputs.get("provider", "auto")

        if provider in ("auto", "transformers"):
            try:
                from transformers import pipeline
                pipe = pipeline("sentiment-analysis", model="distilbert-base-uncased-finetuned-sst-2-english")
                results = []
                for text in texts[:50]:
                    out = pipe(text[:512])[0]
                    results.append({
                        "text_preview": text[:100],
                        "label": out["label"].lower(),
                        "score": round(out["score"], 3),
                    })
                dist = {"positive": 0, "negative": 0, "neutral": 0}
                for r in results:
                    dist[r["label"]] = dist.get(r["label"], 0) + 1
                return {"results": results, "distribution": dist, "provider": "transformers"}
            except ImportError:
                pass

        # Local lexicon fallback
        results = []
        for text in texts[:200]:
            s = _lexicon_score(text)
            results.append({"text_preview": text[:100], **s})

        dist = {"positive": 0, "negative": 0, "neutral": 0}
        for r in results:
            dist[r["label"]] = dist.get(r["label"], 0) + 1

        return {"results": results, "distribution": dist, "provider": "local_lexicon"}

    async def self_test(self) -> bool:
        result = await self._run({"texts": ["I love this product!", "This is terrible and broken."]})
        return "results" in result and len(result["results"]) == 2


handler = SentimentScoreHandler()
