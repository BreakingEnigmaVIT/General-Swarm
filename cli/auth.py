"""Authentication flows and credential storage for `swarm auth`."""

from __future__ import annotations

import os
import shlex
import subprocess
from getpass import getpass
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from cli.auth_config import AUTH_SERVICES

CREDENTIALS_FILE = Path.home() / ".swarm" / "credentials"


def load_credentials() -> dict[str, str]:
    if not CREDENTIALS_FILE.is_file():
        return {}
    out: dict[str, str] = {}
    for line in CREDENTIALS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def save_credential(key: str, value: str) -> None:
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = load_credentials()
    data[key] = value
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    tmp = CREDENTIALS_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(CREDENTIALS_FILE)
    try:
        os.chmod(CREDENTIALS_FILE, 0o600)
    except OSError:
        pass


def credential_exists(key: str) -> bool:
    if os.environ.get(key):
        return True
    return key in load_credentials()


def read_secret(prompt: str) -> str:
    raw = getpass(prompt)
    value = raw.strip()
    if not value:
        raise ValueError("No value entered — skipping this credential.")
    return value


def verify_credential(service: dict[str, Any]) -> tuple[bool, str]:
    if service.get("test_cmd") is None:
        return True, "stored — not verified"
    cmd = service["test_cmd"]
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)[:200]
    if proc.returncode == 0:
        return True, "verified"
    err = (proc.stderr or proc.stdout or "")[:200]
    return False, err


def run_adc_flow(service: dict[str, Any], console: Console) -> bool:
    console.print("[cyan]Opening browser for gcloud authentication...[/cyan]")
    try:
        r1 = subprocess.run(
            ["gcloud", "auth", "login", "--quiet"],
            timeout=120,
            check=False,
        )
        r2 = subprocess.run(
            ["gcloud", "auth", "application-default", "login", "--quiet"],
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        console.print(
            "[red]gcloud not found. Install: https://cloud.google.com/sdk/docs/install[/red]"
        )
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]Authentication timed out[/red]")
        return False
    if r1.returncode == 0 and r2.returncode == 0:
        console.print("[green]GCP authentication complete.[/green]")
        return True
    console.print("[red]gcloud authentication did not complete successfully.[/red]")
    return False


def run_apikey_flow(service: dict[str, Any], console: Console) -> bool:
    console.print(f"[dim]{service['description']}[/dim]")
    try:
        value = read_secret(f"Paste your {service['name']} key: ")
    except ValueError:
        console.print("[yellow]Skipped.[/yellow]")
        return False
    save_credential(service["env_key"], value)
    return True


def run_oauth_flow(service: dict[str, Any], console: Console) -> bool:
    # TODO: replace manual token paste with localhost redirect OAuth callback.
    console.print(
        "[yellow]This requires a manual step for "
        f"{service['name']}:[/yellow] create an integration/access token."
    )
    name = service["name"]
    if name == "Notion":
        url = "https://www.notion.so/my-integrations"
    elif name == "Intercom":
        url = "https://app.intercom.com/a/apps/_/developer-hub"
    else:
        url = "Visit the service's developer settings"
    console.print(f"[link={url}]{url}[/link]" if url.startswith("http") else url)
    return run_apikey_flow(service, console)


def run_auth_flow(
    services: list[dict[str, Any]],
    skip_existing: bool,
    only: tuple[str, ...] | None,
    console: Console,
) -> dict[str, str]:
    summary: dict[str, str] = {}
    filtered = list(services)
    if only:
        want = {x.strip() for x in only}
        filtered = [s for s in filtered if s["name"] in want]

    for svc in filtered:
        name = svc["name"]
        if skip_existing and credential_exists(svc["env_key"]):
            console.print(f"[dim]{name} — already configured, skipping[/dim]")
            summary[name] = "skipped"
            continue
        console.print(f"\n[bold]--- {name} ---[/bold]")
        console.print(svc["description"])
        ok = False
        method = svc["method"]
        if method == "adc":
            ok = run_adc_flow(svc, console)
        elif method == "oauth":
            ok = run_oauth_flow(svc, console)
        elif method == "apikey":
            ok = run_apikey_flow(svc, console)
        else:
            summary[name] = "failed"
            continue

        if not ok:
            summary[name] = "failed"
            continue

        vok, vmsg = verify_credential(svc)
        console.print(f"Verification: {'[green]' + vmsg + '[/green]' if vok else '[red]' + vmsg + '[/red]'}")
        summary[name] = "ok" if vok else "failed"

    return summary


def run_auth_check(console: Console, only: tuple[str, ...] | None) -> dict[str, str]:
    summary: dict[str, str] = {}
    filtered = list(AUTH_SERVICES)
    if only:
        want = {x.strip() for x in only}
        filtered = [s for s in filtered if s["name"] in want]
    table = Table(title="Credential check")
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Detail")
    for svc in filtered:
        name = svc["name"]
        if not credential_exists(svc["env_key"]):
            summary[name] = "missing"
            table.add_row(name, "missing", "not configured")
            continue
        vok, vmsg = verify_credential(svc)
        summary[name] = "ok" if vok else "failed"
        table.add_row(name, summary[name], vmsg if vok else vmsg[:80])
    console.print(table)
    return summary


def _print_summary_table(console: Console, summary: dict[str, str]) -> None:
    table = Table(title="Auth summary")
    table.add_column("Service")
    table.add_column("Status")
    for name, st in sorted(summary.items()):
        table.add_row(name, st)
    console.print(table)


def _service_by_display_name(service_name: str) -> dict[str, Any] | None:
    sn = service_name.strip().lower()
    for svc in AUTH_SERVICES:
        if svc["name"].lower() == sn:
            return svc
    return None


@click.group(name="auth")
def auth_cli() -> None:
    """Authenticate external services used by the swarm."""


@auth_cli.command("setup")
@click.option(
    "--skip-existing",
    is_flag=True,
    default=True,
    help="Skip services that already have credentials stored.",
)
@click.option("--only", multiple=True, help="Auth only specific services by name. Repeatable.")
@click.option("--check", "check_only", is_flag=True, help="Only verify existing credentials.")
def auth_setup(
    skip_existing: bool,
    only: tuple[str, ...],
    check_only: bool,
) -> None:
    """Walk through authentication for all required services."""
    console = Console()
    if check_only:
        summary = run_auth_check(console, only if only else None)
    else:
        summary = run_auth_flow(
            AUTH_SERVICES,
            skip_existing=skip_existing,
            only=only if only else None,
            console=console,
        )
        _print_summary_table(console, summary)

    bad = 0
    for svc in AUTH_SERVICES:
        if only and svc["name"] not in only:
            continue
        st = summary.get(svc["name"], "missing")
        if svc["required"] and st in ("failed", "missing"):
            bad += 1

    if bad:
        console.print(
            "[red]Required credentials missing. Run `swarm auth setup` to resolve.[/red]"
        )
        raise click.exceptions.Exit(1)
    console.print("[green]Auth complete. Run `swarm doctor` to verify.[/green]")


@auth_cli.command("show")
def auth_show() -> None:
    """List which credentials are configured (keys only, never values)."""
    console = Console()
    file_keys = load_credentials()
    table = Table(title="Credentials (keys only)")
    table.add_column("Key")
    table.add_column("Source")
    table.add_column("Present")
    seen: set[str] = set()
    for svc in AUTH_SERVICES:
        key = svc["env_key"]
        seen.add(key)
        if os.environ.get(key):
            src = "env"
            present = "yes"
        elif key in file_keys:
            src = "file"
            present = "yes"
        else:
            src = "—"
            present = "no"
        req = " (required)" if svc["required"] else ""
        table.add_row(f"{key}{req}", src, present)
    for key in sorted(file_keys.keys()):
        if key not in seen:
            table.add_row(key, "file", "yes")
    console.print(table)


@auth_cli.command("clear")
@click.argument("service_name")
@click.confirmation_option(prompt="This will remove the stored credential. Continue?")
def auth_clear(service_name: str) -> None:
    """Remove a stored credential by service name."""
    console = Console()
    svc = _service_by_display_name(service_name)
    if not svc:
        console.print(f"[red]Unknown service: {service_name}[/red]")
        raise click.exceptions.Exit(1)
    key = svc["env_key"]
    data = load_credentials()
    if key not in data:
        console.print(f"[yellow]No stored value for {key}[/yellow]")
        return
    del data[key]
    lines = [f"{k}={v}" for k, v in sorted(data.items())]
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CREDENTIALS_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(CREDENTIALS_FILE)
    try:
        os.chmod(CREDENTIALS_FILE, 0o600)
    except OSError:
        pass
    console.print(f"[green]Removed {key} from credentials file.[/green]")
