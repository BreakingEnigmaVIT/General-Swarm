"""SLO evaluator — compare metric values against declared thresholds."""

from __future__ import annotations
from typing import Any
from tools.base import ToolHandler


class SloEvaluatorHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        slos = inputs.get("slos", [])
        results = []
        breaches = []

        for slo in slos:
            name = slo["name"]
            current = float(slo["current_value"])
            threshold = float(slo["threshold"])
            comparison = slo["comparison"]

            passes = {
                "lt": current < threshold,
                "lte": current <= threshold,
                "gt": current > threshold,
                "gte": current >= threshold,
            }.get(comparison, False)

            entry = {
                "name": name,
                "metric": slo["metric_name"],
                "current_value": current,
                "threshold": threshold,
                "comparison": comparison,
                "unit": slo.get("unit", ""),
                "status": "pass" if passes else "breach",
            }
            results.append(entry)
            if not passes:
                breaches.append(name)

        return {
            "total_slos": len(slos),
            "passing": len(slos) - len(breaches),
            "breaching": len(breaches),
            "has_breach": bool(breaches),
            "breach_names": breaches,
            "results": results,
        }

    async def self_test(self) -> bool:
        result = await self._run({"slos": [
            {"name": "latency_p95", "metric_name": "http_latency_p95", "current_value": 150, "threshold": 200, "comparison": "lt", "unit": "ms"}
        ]})
        return result.get("passing") == 1


handler = SloEvaluatorHandler()
