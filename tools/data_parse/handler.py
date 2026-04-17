"""Structured data parsing — JSON, YAML, CSV, XML."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from tools.base import ToolHandler


class DataParseHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        fmt = inputs["format"]
        content = inputs["content"]
        op = inputs.get("operation", "parse")

        # Parse
        try:
            if fmt == "json":
                data = json.loads(content)
            elif fmt == "yaml":
                import yaml
                data = yaml.safe_load(content)
            elif fmt == "csv":
                reader = csv.DictReader(io.StringIO(content))
                data = list(reader)
            elif fmt == "xml":
                import xml.etree.ElementTree as ET
                root = ET.fromstring(content)
                data = _xml_to_dict(root)
            else:
                return {"error": f"Unknown format: {fmt}"}
        except Exception as exc:
            return {"error": f"Parse error: {exc}"}

        # Post-process
        if op == "keys":
            if isinstance(data, dict):
                return {"keys": list(data.keys())}
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                return {"keys": list(data[0].keys())}
            return {"keys": []}
        elif op == "length":
            return {"length": len(data) if hasattr(data, "__len__") else 1}
        elif op == "filter":
            key = inputs.get("filter_key", "")
            if isinstance(data, dict):
                return {"result": data.get(key)}
            elif isinstance(data, list):
                return {"result": [item.get(key) for item in data if isinstance(item, dict)]}

        return {"data": data}

    async def self_test(self) -> bool:
        r = await self._run({"format": "json", "content": '{"x": 1}'})
        return r.get("data") == {"x": 1}


def _xml_to_dict(element: Any) -> dict:
    d: dict = {}
    for child in element:
        child_data = _xml_to_dict(child) if len(child) else child.text
        if child.tag in d:
            if not isinstance(d[child.tag], list):
                d[child.tag] = [d[child.tag]]
            d[child.tag].append(child_data)
        else:
            d[child.tag] = child_data
    return d or {"text": element.text}


handler = DataParseHandler()
