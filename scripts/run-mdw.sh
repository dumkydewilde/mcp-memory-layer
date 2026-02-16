#!/usr/bin/env bash
# Run the integrated MotherDuck + memory layer MCP server.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Source motherduck token from datateam envrc if not already set
if [ -z "${motherduck_token:-}" ] && [ -f "$HOME/code/datateam/dbt/.envrc" ]; then
    source "$HOME/code/datateam/dbt/.envrc"
fi

cd "$REPO_DIR"
exec uv run mcp-memory-mdw --read-write "$@"
