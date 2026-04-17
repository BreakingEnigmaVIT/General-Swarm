# How to Add a Custom Tool

Adding a tool requires **two files** in a new folder under `tools/`.
No core code changes.

## Step 1 — Scaffold

```bash
swarm scaffold tool sql-query
```

Creates:
```
tools/sql-query/
├── spec.yaml
├── handler.py
└── __init__.py
```

## Step 2 — Define `spec.yaml`

```yaml
name: sql_query
description: "Execute a read-only SQL query against a SQLite database."
version: "1.0.0"
side_effect_level: read-only   # read-only | mutates-local | mutates-external
permissions: []

input_schema:
  type: object
  properties:
    db_path:
      type: string
      description: "Path to the SQLite file (relative to project root)"
    query:
      type: string
      description: "SQL SELECT query to execute"
    max_rows:
      type: integer
      default: 100
  required: [db_path, query]
  additionalProperties: false

output_schema:
  type: object
  properties:
    columns:
      type: array
      items: {type: string}
    rows:
      type: array
    row_count:
      type: integer

timeout: 15.0
retry:
  max_attempts: 2
  backoff_base: 1.0
  backoff_max: 5.0
```

### Side-effect levels

| Level | Meaning | Default safety gate |
|---|---|---|
| `read-only` | Never modifies anything | No confirmation needed |
| `mutates-local` | Writes to local filesystem/DB | No confirmation in auto mode |
| `mutates-external` | Sends HTTP requests, emails, etc. | Confirmation required in interactive mode |

## Step 3 — Implement `handler.py`

```python
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError
from tools.base import ToolHandler

_CWD = Path.cwd()

class SqlQueryHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        db_path = (_CWD / inputs["db_path"]).resolve()
        if not str(db_path).startswith(str(_CWD)):
            raise SafetyError("db_path escapes project root")

        query = inputs["query"].strip()
        if not query.upper().startswith("SELECT"):
            raise SafetyError("Only SELECT queries are allowed")

        max_rows = int(inputs.get("max_rows", 100))

        conn = sqlite3.connect(str(db_path))
        try:
            cursor = conn.execute(query)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(max_rows)
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
            }
        finally:
            conn.close()

    async def self_test(self) -> bool:
        return True

handler = SqlQueryHandler()
```

### Rules for `_run`

- Always return a `dict` — it is JSON-serialized and sent back to the LLM.
- Raise `ToolInputError` for bad inputs (schema validation catches most cases automatically).
- Raise `SafetyError` for policy violations.
- Any unhandled exception is caught by `ToolHandler.run()` and returned as `{"error": "..."}`.
- `self_test()` is called by `swarm doctor` — make it deterministic and fast.

## Step 4 — Validate

```bash
swarm validate tools/sql-query/spec.yaml
swarm list tools   # sql_query should appear
```

## Step 5 — Grant to an agent

In your agent spec or topology:

```yaml
# agents/my-analyst/spec.yaml
tools:
  - sql_query
  - calculator
```

```yaml
# configs/my-topology.yaml
safety:
  tool_allowlist:
    - sql_query
    - calculator
```

That's it. The tool is available to any agent that lists it.
