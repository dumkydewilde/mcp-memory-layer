"""CLI for mcp-memory: init command and server launcher."""

import json
from pathlib import Path

import click

from .config import DEFAULT_CONFIG_DIR

CONFIG_TEMPLATE = """\
# MCP Memory Layer configuration
# Paths can be absolute or relative to where the server is launched.

[paths]
# duckdb = "/path/to/your/database.duckdb"
# manifest = "/path/to/your/dbt/target/manifest.json"
# corrections = "{config_dir}/corrections.json"
# popularity_db = "{config_dir}/popularity.duckdb"

[features]
query = true
corrections = true
dbt = true
popularity = true
# motherduck_query_rw = false

[motherduck]
# Optional: wrap the hosted MotherDuck MCP instead of querying a local DuckDB file.
# mcp_url = "https://api.motherduck.com/mcp"
# token = "md_your_access_or_read_scaling_token"
"""

EMPTY_CORRECTIONS = {"version": 1, "corrections": []}


@click.group()
def cli():
    """MCP Memory Layer — corrections, dbt context, and popularity for text-to-SQL."""
    pass


@cli.command()
@click.option(
    "--dir",
    "config_dir",
    type=click.Path(),
    default=str(DEFAULT_CONFIG_DIR),
    help=f"Config directory (default: {DEFAULT_CONFIG_DIR})",
)
@click.option(
    "--duckdb-path",
    type=click.Path(),
    default=None,
    help="Path to your DuckDB database file.",
)
@click.option(
    "--manifest-path",
    type=click.Path(),
    default=None,
    help="Path to your dbt target/manifest.json.",
)
def init(config_dir: str, duckdb_path: str | None, manifest_path: str | None):
    """Initialize mcp-memory configuration directory.

    Creates ~/.mcp-memory/ with a config.toml and empty corrections.json.
    """
    config_dir_path = Path(config_dir)
    config_file = config_dir_path / "config.toml"
    corrections_file = config_dir_path / "corrections.json"

    config_dir_path.mkdir(parents=True, exist_ok=True)

    # Write config.toml
    if config_file.exists():
        click.echo(f"Config already exists: {config_file}")
    else:
        content = CONFIG_TEMPLATE.format(config_dir=config_dir_path)

        # Uncomment paths if provided
        if duckdb_path:
            content = content.replace(
                '# duckdb = "/path/to/your/database.duckdb"',
                f'duckdb = "{duckdb_path}"',
            )
        if manifest_path:
            content = content.replace(
                '# manifest = "/path/to/your/dbt/target/manifest.json"',
                f'manifest = "{manifest_path}"',
            )

        config_file.write_text(content)
        click.echo(f"Created config: {config_file}")

    # Write empty corrections.json
    if corrections_file.exists():
        click.echo(f"Corrections file already exists: {corrections_file}")
    else:
        corrections_file.write_text(json.dumps(EMPTY_CORRECTIONS, indent=2))
        click.echo(f"Created empty corrections: {corrections_file}")

    # Summary
    click.echo("")
    click.echo("Next steps:")
    click.echo(f"  1. Edit {config_file} to set your database and manifest paths")
    click.echo("  2. Run: mcp-memory")
    click.echo("")
    click.echo("Or configure in Claude Desktop:")
    click.echo('  "command": "mcp-memory"')
    click.echo(f'  "env": {{"MCP_MEMORY_CONFIG": "{config_file}"}}')


@cli.command()
def run():
    """Run the MCP memory server (same as mcp-memory)."""
    from .server import main

    main()
