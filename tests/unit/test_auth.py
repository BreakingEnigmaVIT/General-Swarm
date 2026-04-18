"""Tests for swarm auth CLI helpers."""

from __future__ import annotations

import os
import stat
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from cli.auth import credential_exists, load_credentials, run_auth_flow
from cli.auth_config import AUTH_SERVICES


def test_save_and_load_credential(monkeypatch, tmp_path) -> None:
    import cli.auth as auth_mod

    cred = tmp_path / "credentials"
    monkeypatch.setattr(auth_mod, "CREDENTIALS_FILE", cred)
    auth_mod.save_credential("TEST_KEY", "test_value")
    assert auth_mod.load_credentials()["TEST_KEY"] == "test_value"


def test_credential_file_permissions(monkeypatch, tmp_path) -> None:
    import cli.auth as auth_mod

    cred = tmp_path / "credentials"
    monkeypatch.setattr(auth_mod, "CREDENTIALS_FILE", cred)
    auth_mod.save_credential("K", "v")
    if os.name != "posix":
        pytest.skip("POSIX file mode not enforced on this platform")
    mode = stat.S_IMODE(cred.stat().st_mode)
    assert oct(mode) == "0o600"


def test_credential_exists_from_env(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "fake")
    assert credential_exists("GROQ_API_KEY") is True


def test_credential_exists_false_when_missing(monkeypatch, tmp_path) -> None:
    import cli.auth as auth_mod

    cred = tmp_path / "credentials"
    cred.write_text("", encoding="utf-8")
    monkeypatch.setattr(auth_mod, "CREDENTIALS_FILE", cred)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert credential_exists("GROQ_API_KEY") is False


def test_run_auth_flow_skips_existing(monkeypatch) -> None:
    from rich.console import Console

    monkeypatch.setattr("cli.auth.credential_exists", lambda _k: True)
    console = Console(record=True)
    summary = run_auth_flow(AUTH_SERVICES, skip_existing=True, only=None, console=console)
    assert summary
    assert all(v == "skipped" for v in summary.values())


def test_auth_setup_check_flag(monkeypatch) -> None:
    from cli.main import cli

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(__file__))
    with patch("cli.auth.verify_credential", return_value=(True, "ok")):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "setup", "--check"])
    assert result.exit_code == 0, result.output
    for svc in AUTH_SERVICES:
        assert svc["name"] in result.output


def test_read_secret_raises_on_empty(monkeypatch) -> None:
    import cli.auth as auth_mod

    monkeypatch.setattr(auth_mod, "getpass", lambda _p: "  ")
    with pytest.raises(ValueError, match="No value"):
        auth_mod.read_secret("Test: ")
