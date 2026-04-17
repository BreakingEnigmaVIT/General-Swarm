"""Human input tool — pauses execution and asks the operator a question."""

from __future__ import annotations

import asyncio
from typing import Any

from tools.base import ToolHandler


class HumanInputHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from rich.console import Console
        from rich.prompt import Prompt

        console = Console()
        prompt_text = inputs["prompt"]
        options = inputs.get("options")

        console.print(f"\n[bold yellow]⏸  Human input required:[/bold yellow]")
        console.print(f"[cyan]{prompt_text}[/cyan]")

        if options:
            choices_str = " / ".join(f"[green]{o}[/green]" for o in options)
            console.print(f"Options: {choices_str}")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: Prompt.ask("[bold]Your response[/bold]", choices=options)
            if options
            else Prompt.ask("[bold]Your response[/bold]"),
        )
        return {"response": response}

    async def self_test(self) -> bool:
        return True  # Skip interactive prompt in tests


handler = HumanInputHandler()
