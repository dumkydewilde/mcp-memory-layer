"""Tests for the CLI init command."""

import json
from pathlib import Path

from click.testing import CliRunner

from mcp_memory.cli import cli


def test_init_creates_config_dir(tmp_path):
    """init creates config directory, config.toml, and empty corrections.json."""
    config_dir = tmp_path / ".mcp-memory"
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dir", str(config_dir)])

    assert result.exit_code == 0
    assert (config_dir / "config.toml").exists()
    assert (config_dir / "corrections.json").exists()

    corrections = json.loads((config_dir / "corrections.json").read_text())
    assert corrections == {"version": 1, "corrections": []}


def test_init_with_paths(tmp_path):
    """init writes provided paths into config.toml."""
    config_dir = tmp_path / ".mcp-memory"
    runner = CliRunner()
    result = runner.invoke(cli, [
        "init",
        "--dir", str(config_dir),
        "--duckdb-path", "/my/db.duckdb",
        "--manifest-path", "/my/manifest.json",
    ])

    assert result.exit_code == 0
    content = (config_dir / "config.toml").read_text()
    assert 'duckdb = "/my/db.duckdb"' in content
    assert 'manifest = "/my/manifest.json"' in content


def test_init_does_not_overwrite(tmp_path):
    """init does not overwrite existing files."""
    config_dir = tmp_path / ".mcp-memory"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("existing")
    (config_dir / "corrections.json").write_text("existing")

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dir", str(config_dir)])

    assert result.exit_code == 0
    assert "already exists" in result.output
    assert (config_dir / "config.toml").read_text() == "existing"
