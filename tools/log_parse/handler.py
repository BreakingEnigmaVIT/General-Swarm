"""Log parser — JSON, logfmt, nginx, plain text."""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()
_LEVEL_RE = re.compile(r"\b(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE)
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _parse_line(line: str, fmt: str) -> dict:
    line = line.strip()
    if not line:
        return {}

    if fmt == "json" or (fmt == "auto" and line.startswith("{")):
        try:
            obj = json.loads(line)
            return {
                "level": obj.get("level", obj.get("severity", "")),
                "timestamp": obj.get("timestamp", obj.get("time", obj.get("ts", ""))),
                "message": obj.get("message", obj.get("msg", obj.get("event", ""))),
                "service": obj.get("service", obj.get("logger", "")),
                "raw": line[:500],
            }
        except json.JSONDecodeError:
            pass

    # Logfmt: key=value key="value"
    if fmt == "logfmt" or (fmt == "auto" and "=" in line and not line.startswith("{")):
        pairs = {}
        for m in re.finditer(r'(\w+)="([^"]*)"', line):
            pairs[m.group(1)] = m.group(2)
        for m in re.finditer(r'(\w+)=(\S+)', line):
            if m.group(1) not in pairs:
                pairs[m.group(1)] = m.group(2)
        if pairs:
            return {
                "level": pairs.get("level", pairs.get("severity", "")),
                "timestamp": pairs.get("time", pairs.get("ts", pairs.get("t", ""))),
                "message": pairs.get("msg", pairs.get("message", "")),
                "service": pairs.get("logger", pairs.get("service", "")),
                "raw": line[:500],
            }

    # Plain — extract level and timestamp via regex
    level_m = _LEVEL_RE.search(line)
    ts_m = _TS_RE.search(line)
    return {
        "level": level_m.group(0).upper() if level_m else "",
        "timestamp": ts_m.group(0) if ts_m else "",
        "message": line[:300],
        "service": "",
        "raw": line[:500],
    }


class LogParseHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        max_lines = int(inputs.get("max_lines", 500))
        fmt = inputs.get("format", "auto")

        if inputs.get("log_file"):
            fp = (_CWD / inputs["log_file"]).resolve()
            if not fp.exists():
                return {"error": f"File not found: {fp}"}
            text = fp.read_text(encoding="utf-8", errors="replace")
        elif inputs.get("log_text"):
            text = inputs["log_text"]
        else:
            return {"error": "Provide log_text or log_file"}

        lines = text.splitlines()[:max_lines]
        events = [e for line in lines if (e := _parse_line(line, fmt))]

        level_counts: dict[str, int] = {}
        for e in events:
            lvl = e.get("level", "").upper() or "UNKNOWN"
            level_counts[lvl] = level_counts.get(lvl, 0) + 1

        errors = [e for e in events if e.get("level", "").upper() in ("ERROR", "CRITICAL", "FATAL")]

        return {
            "total_lines": len(lines),
            "parsed_events": len(events),
            "level_distribution": level_counts,
            "error_count": len(errors),
            "recent_errors": errors[-20:],
            "events": events[:100],
        }

    async def self_test(self) -> bool:
        result = await self._run({"log_text": '{"level":"ERROR","msg":"test","ts":"2024-01-01T00:00:00Z"}'})
        return result.get("error_count", 0) == 1


handler = LogParseHandler()
