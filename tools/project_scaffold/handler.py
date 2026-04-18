"""Project scaffold — writes starter file trees for common stack templates."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from tools.base import ToolHandler
from core.exceptions import SafetyError

_CWD = Path.cwd()

# NOTE: All templates use single braces { } for actual language syntax.
# The only substitution is content.replace("{name}", name) — NOT .format().
# Never use {{ }} here; they will be written to disk literally.

_TEMPLATES: dict[str, dict[str, str]] = {
    "fastapi": {
        "pyproject.toml": '[project]\nname = "{name}"\nversion = "0.1.0"\n\n[project.dependencies]\nfastapi = ">=0.110"\nuvicorn = {\'extras\': [\'standard\'], \'version\': \'>=0.27\'}\nsqlalchemy = ">=2.0"\nalembic = ">=1.13"\nstructlog = ">=24.0"\n',
        "app/__init__.py": "",
        "app/main.py": 'from fastapi import FastAPI\n\napp = FastAPI(title="{name}")\n\n@app.get("/health")\nasync def health():\n    return {"status": "ok"}\n',
        "app/config.py": 'import os\n\nDATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")\nSECRET_KEY = os.environ.get("SECRET_KEY", "change-me")\n',
        "alembic.ini": '[alembic]\nscript_location = migrations\nsqlalchemy.url = sqlite:///./dev.db\n',
        "migrations/env.py": '"""Alembic env."""\nfrom alembic import context\n\ndef run_migrations_offline():\n    context.configure(url=context.config.get_main_option("sqlalchemy.url"), literal_binds=True)\n    with context.begin_transaction():\n        context.run_migrations()\n\ndef run_migrations_online():\n    pass\n\nif context.is_offline_mode():\n    run_migrations_offline()\nelse:\n    run_migrations_online()\n',
        ".env.example": "DATABASE_URL=postgresql+asyncpg://user:pass@localhost/dbname\nSECRET_KEY=change-me\n",
        "Dockerfile": 'FROM python:3.12-slim\nWORKDIR /app\nCOPY pyproject.toml .\nRUN pip install -e .\nCOPY . .\nCMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]\n',
    },
    "react_tailwind": {
        "package.json": '{\n  "name": "{name}",\n  "version": "0.1.0",\n  "scripts": {\n    "dev": "vite",\n    "build": "tsc && vite build",\n    "test": "vitest"\n  },\n  "dependencies": {\n    "react": "^18",\n    "react-dom": "^18",\n    "@tanstack/react-query": "^5",\n    "react-hook-form": "^7",\n    "axios": "^1"\n  },\n  "devDependencies": {\n    "@vitejs/plugin-react": "^4",\n    "tailwindcss": "^3",\n    "typescript": "^5",\n    "vite": "^5",\n    "vitest": "^1"\n  }\n}\n',
        "index.html": '<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n    <title>{name}</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.tsx"></script>\n  </body>\n</html>\n',
        "vite.config.ts": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n});\n',
        "tsconfig.json": '{\n  "compilerOptions": {\n    "target": "ES2020",\n    "useDefineForClassFields": true,\n    "lib": ["ES2020", "DOM", "DOM.Iterable"],\n    "module": "ESNext",\n    "skipLibCheck": true,\n    "moduleResolution": "bundler",\n    "allowImportingTsExtensions": true,\n    "resolveJsonModule": true,\n    "isolatedModules": true,\n    "noEmit": true,\n    "jsx": "react-jsx",\n    "strict": true\n  },\n  "include": ["src"]\n}\n',
        "src/main.tsx": 'import React from "react";\nimport ReactDOM from "react-dom/client";\nimport App from "./App";\nimport "./index.css";\n\nReactDOM.createRoot(document.getElementById("root")!).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>\n);\n',
        "src/App.tsx": 'import React from "react";\n\nexport default function App() {\n  return (\n    <div className="min-h-screen bg-gray-50">\n      <h1 className="text-2xl font-bold p-8">{name}</h1>\n    </div>\n  );\n}\n',
        "src/index.css": "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
        "src/api.ts": 'import axios from "axios";\n\nexport const api = axios.create({\n  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000",\n});\n',
        ".env.example": "VITE_API_URL=http://localhost:8000\n",
        "tailwind.config.js": 'export default {\n  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],\n  theme: { extend: {} },\n  plugins: [],\n};\n',
    },
    "cli_python": {
        "pyproject.toml": '[project]\nname = "{name}"\nversion = "0.1.0"\n[project.scripts]\n{name} = "{name}.cli:main"\n',
        "{name}/__init__.py": "",
        "{name}/cli.py": 'import click\n\n@click.group()\ndef main(): pass\n\n@main.command()\n@click.argument("input")\ndef run(input): click.echo(f"Running: {input}")\n',
    },
    "monorepo": {
        "README.md": "# {name}\n\nMonorepo containing frontend and backend services.\n",
        "frontend/.gitkeep": "",
        "backend/.gitkeep": "",
        "docker-compose.yml": 'version: "3.9"\nservices:\n  backend:\n    build: ./backend\n    ports: ["8000:8000"]\n  frontend:\n    build: ./frontend\n    ports: ["3000:3000"]\n',
    },
    "go_fiber": {
        "go.mod": 'module {name}\n\ngo 1.22\n\nrequire github.com/gofiber/fiber/v2 v2.52.0\n',
        "main.go": 'package main\n\nimport "github.com/gofiber/fiber/v2"\n\nfunc main() {\n\tapp := fiber.New()\n\tapp.Get("/health", func(c *fiber.Ctx) error {\n\t\treturn c.JSON(fiber.Map{"status": "ok"})\n\t})\n\tapp.Listen(":8000")\n}\n',
        "Dockerfile": "FROM golang:1.22-alpine AS build\nWORKDIR /app\nCOPY . .\nRUN go build -o server .\nFROM alpine:3.19\nCOPY --from=build /app/server /server\nCMD [\"/server\"]\n",
    },
    "express": {
        "package.json": '{\n  "name": "{name}",\n  "scripts": {"start": "node index.js", "dev": "nodemon index.js", "test": "jest"},\n  "dependencies": {"express": "^4", "dotenv": "^16"},\n  "devDependencies": {"jest": "^29", "nodemon": "^3"}\n}\n',
        "index.js": 'const express = require("express");\nrequire("dotenv").config();\nconst app = express();\napp.use(express.json());\napp.get("/health", (_, res) => res.json({ status: "ok" }));\napp.listen(process.env.PORT || 8000);\n',
        ".env.example": "PORT=8000\nDATABASE_URL=\n",
    },
}


class ProjectScaffoldHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        template_name = inputs["template"]
        # Always place generated projects under built/ so all agent output
        # lands in one place regardless of what output_dir the agent requests.
        requested = inputs["output_dir"].lstrip("/").lstrip("\\")
        # Strip a leading "built/" if the agent already added it, to avoid built/built/
        if requested.startswith("built/") or requested.startswith("built\\"):
            requested = requested[6:]
        out = _CWD / "built" / requested
        # Safety check
        if not str(out.resolve()).startswith(str(_CWD)):
            raise SafetyError("output_dir escapes working directory")
        name = inputs["project_name"].replace("-", "_").replace(" ", "_")
        overwrite = inputs.get("overwrite", False)

        template = _TEMPLATES.get(template_name)
        if template is None:
            return {"error": f"Unknown template '{template_name}'. Available: {list(_TEMPLATES.keys())}"}

        written = []
        skipped = []
        for rel_path, content in template.items():
            rel_path = rel_path.replace("{name}", name)
            content = content.replace("{name}", name)
            target = out / rel_path
            if target.exists() and not overwrite:
                skipped.append(rel_path)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel_path)

        rel_out = str(out.relative_to(_CWD))
        return {
            "template": template_name,
            "output_dir": rel_out,
            "files_written": written,
            "files_skipped": skipped,
            "IMPORTANT": (
                f"Scaffold written to '{rel_out}/'. "
                f"ALL subsequent filesystem writes MUST use paths prefixed with '{rel_out}/' "
                f"(e.g. '{rel_out}/src/App.tsx', NOT 'src/App.tsx'). "
                "Writing to bare paths like 'src/' will create files in the wrong location."
            ),
        }

    async def self_test(self) -> bool:
        import tempfile
        with tempfile.TemporaryDirectory(dir=_CWD) as tmp:
            result = await self._run({
                "template": "cli_python",
                "output_dir": Path(tmp).name,
                "project_name": "test_proj",
            })
            return "files_written" in result


handler = ProjectScaffoldHandler()
