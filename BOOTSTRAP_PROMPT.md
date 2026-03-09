# Bootstrap Prompt: Create a MotherDuck MCP Memory Layer

Use this prompt with an AI coding agent to create a new repo that wraps the
[mcp-server-motherduck](https://pypi.org/project/mcp-server-motherduck/) package
with a memory layer (corrections, dbt context) tailored to your data warehouse.

---

## Prompt

Create a new Python project called `mcp-mdw-memory` that wraps the official
`mcp-server-motherduck` MCP server with a memory layer for better text-to-SQL.

Use https://github.com/MotherDuck-Open-Source/mcp-memory-layer as the reference
implementation. That repo provides:
- A corrections store (`corrections.py`) for schema gotchas
- A dbt manifest parser (`dbt_context.py`) for column descriptions, lineage, tests
- A manifest resolver (`manifest_resolver.py`) for loading manifests from files or URLs
- A popularity tracker (`popularity.py`) for query pattern tracking

### What to build

1. **A wrapper server** (`server.py`) that:
   - Uses `mcp_server_motherduck.server.create_mcp_server()` as the base
   - Accepts `--db-path`, `--motherduck-token`, `--read-write`, `--manifest-path`,
     `--corrections-path`, and `--transport` CLI options
   - Registers memory layer tools on top of the base server:
     - `get_corrections(question, tables?)` — retrieve relevant schema corrections
     - `save_correction(correction, tables, columns?, keywords?)` — save new corrections
     - `get_dbt_context(table_name)` — get model description, columns, lineage, tests
     - `get_model_sql(table_name)` — get full SQL source of a dbt model
     - `list_dbt_models()` — list all dbt models with descriptions
   - Patches the base `execute_query` tool description to say:
     "Before writing SQL, call get_corrections and get_dbt_context for schema context."
   - Patches `list_tables` and `list_columns` to recommend `list_dbt_models` / `get_dbt_context`
   - Prepends memory layer instructions to the server's instructions string
   - Registers MCP resources for discoverability:
     - `motherduck://overview` — available tools and recommended workflow
     - `motherduck://dbt-models` — full model list
     - `motherduck://corrections` — all corrections

2. **Server instructions** that enforce this workflow for every data question:
   1. Call `get_corrections` with the user's question
   2. Call `list_dbt_models` to discover tables, then `get_dbt_context` for relevant ones
   3. Write and run SQL with `execute_query`
   4. Call `save_correction` if something non-obvious was discovered
   - Explicitly tell the LLM NOT to use `duckdb_tables()`, `duckdb_columns()`,
     `information_schema`, or `DESCRIBE`/`SUMMARIZE` for schema exploration —
     the dbt catalog is the source of truth.

3. **A manifest resolver** that supports:
   - Local file path: `/path/to/manifest.json`
   - HTTP URL: `https://bucket.s3.amazonaws.com/manifest.json` (with 1-hour cache)
   - MotherDuck table: `md:<database>.<schema>.<table>` (expects a table with a
     `manifest` JSON column, single row). Uses `duckdb.connect("md:")` to fetch.

4. **A refresh script** (`scripts/refresh-manifest.sh`) that:
   - Compiles the dbt project and copies `target/manifest.json` to a config directory
   - Accepts `DATATEAM_DIR` and output dir as env vars

5. **A run script** (`scripts/run.sh`) that:
   - Sources the MotherDuck token from your dbt project's `.envrc`
   - Launches the server with `uv run mcp-mdw-memory --read-write`

### Dependencies

```toml
[project]
dependencies = [
    "mcp>=1.0",
    "duckdb>=1.2",
    "sqlglot>=25.0",
    "mcp-server-motherduck>=1.0",
    "click>=8.1",
]
```

### Claude Desktop config

```json
{
  "mcpServers": {
    "my-warehouse": {
      "command": "uv",
      "args": ["run", "mcp-mdw-memory", "--read-write"],
      "env": {
        "MOTHERDUCK_TOKEN": "your_token",
        "MCP_MEMORY_MANIFEST_PATH": "/path/to/dbt/target/manifest.json",
        "MCP_MEMORY_CORRECTIONS_PATH": "/path/to/corrections.json"
      }
    }
  }
}
```

### Key implementation details

- Install the reference repo as a dependency (`mcp-memory-layer`) and import
  `CorrectionsStore`, `DbtManifest`, and `resolve_manifest` from it. Don't
  duplicate the correction matching, manifest parsing, or popularity logic.
- Use `click` for CLI argument parsing.
- Tool annotations: mark read-only tools with `readOnlyHint: True`.
- Patch tool descriptions by accessing `mcp._tool_manager._tools[name].description`.
- DuckDB quirk: in `ON CONFLICT DO UPDATE SET` clauses, use `now()` not
  `current_timestamp` for TIMESTAMP columns (DuckDB misinterprets it as a column ref).

### Project structure

```
mcp-mdw-memory/
├── pyproject.toml
├── src/mcp_mdw_memory/
│   ├── __init__.py
│   └── server.py          # Wrapper server with CLI
├── scripts/
│   ├── run.sh
│   └── refresh-manifest.sh
├── corrections.json        # Your warehouse-specific corrections
└── README.md
```
