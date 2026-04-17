"""Self-reflection tool — LLM-backed critique of the agent's own output."""

from __future__ import annotations

import json
from typing import Any, Optional

from providers.base import LLMProvider
from tools.base import ToolHandler

_provider: Optional[LLMProvider] = None
_model: str = "llama-3.3-70b-versatile"


def set_provider(provider: LLMProvider, model: str) -> None:
    global _provider, _model
    _provider = provider
    _model = model


class SelfReflectHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _provider is None:
            return {"error": "Provider not configured for self_reflect"}

        content = inputs["content"]
        criteria = inputs.get("criteria", "accuracy, clarity, completeness, and helpfulness")

        system = (
            "You are a self-critical AI assistant. Evaluate the given content objectively "
            "and return a JSON object with: score (1-10), strengths (list), "
            "weaknesses (list), improved (improved version of the content)."
        )
        user = f"Evaluate the following content based on {criteria}:\n\n{content}"

        result = await _provider.complete(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=_model,
            temperature=0.3,
        )
        try:
            text = (result.content or "").strip()
            # Extract JSON from possible markdown code block
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception:
            return {"score": 0, "strengths": [], "weaknesses": [], "improved": result.content or ""}

    async def self_test(self) -> bool:
        return True


handler = SelfReflectHandler()
