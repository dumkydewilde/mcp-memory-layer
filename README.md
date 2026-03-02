# MCP Memory Layer for Text-to-SQL

An [MCP](https://modelcontextprotocol.io/) server that wraps a DuckDB/dbt data warehouse with a **memory layer** — corrections, dbt model context, and query popularity tracking — so LLMs write better SQL on the first try.

## The problem

LLMs generating SQL against a data warehouse hit the same mistakes over and over:

- **Naming traps** — `raw_orders.customer` vs `stg_orders.customer_id`, amounts in cents vs dollars
- **Stale tables** — denormalized snapshots that look useful but have incomplete data
- **Missing business context** — column descriptions, upstream lineage, and pre-computed metrics that the raw schema doesn't reveal
- **Reinventing joins** — ignoring common access patterns that dbt already optimized

Each mistake costs a round-trip: the LLM writes bad SQL, gets an error, tries again. With complex schemas (100+ models), these round-trips add up fast.

## How it works

The memory layer sits between the LLM and the database, providing three types of context that raw schema metadata can't:

### 1. Corrections

A version-controlled JSON file of schema "gotchas" — things an LLM can't infer from `DESCRIBE` alone:

```
"raw_orders amounts (subtotal, tax_paid, order_total) are stored in CENTS.
 Use stg_orders which converts to dollars."
```

```
"AVOID the daily_revenue table — it is a stale snapshot covering only
 3 of 6 locations. Use the orders table instead."
```

Corrections are matched to incoming questions by table name, column name, and keyword overlap. The LLM calls `get_corrections` before writing SQL and gets the top 3 relevant tips.

New corrections can be saved during a session (`save_correction`) when the LLM discovers something non-obvious — building institutional knowledge over time.

### 2. dbt model context

Parses the dbt `manifest.json` to serve column descriptions, upstream lineage, tests, and (truncated) SQL — the same curated metadata your analytics engineers wrote, delivered as small token-efficient slices rather than dumping the entire catalog.

Tools: `list_dbt_models`, `get_dbt_context`, `get_model_sql`

### 3. Query popularity tracking

Records which tables, columns, and join patterns are actually used in queries. Over time this builds a picture of common access patterns:

```
orders: queried 47 times
  → commonly joined to customers ON customer_id (INNER, 32x)
  Popular columns: order_total (select), ordered_at (where)
```

This steers the LLM toward proven patterns instead of inventing joins from scratch.

## Two server modes

### Standalone (`mcp-memory`)

A self-contained MCP server with its own `query` tool for executing SQL against a local DuckDB file. Good for development and evaluation.

### MotherDuck wrapper (`mcp-memory-mdw`)

Wraps the official [mcp-server-motherduck](https://pypi.org/project/mcp-server-motherduck/) — inheriting its `execute_query`, `list_tables`, etc. — and registers memory layer tools on top. Also patches the base tool descriptions to steer the LLM toward `get_dbt_context` over raw `list_columns`.

This is the production mode: point it at your MotherDuck database and dbt manifest, and any MCP client (Claude Desktop, Cursor, etc.) gets the memory layer automatically.

## Evaluation framework

The `eval/` directory contains an A/B testing harness that measures how each memory feature affects SQL quality:

```bash
# Compare baseline (no memory) vs all features
uv run python -m eval.harness --config baseline --api openai
uv run python -m eval.harness --config all_features --api openai

# Generate comparison report
uv run python -m eval.report eval/results/baseline.json eval/results/all_features.json
```

Configurations: `baseline`, `corrections`, `dbt`, `popularity`, `all_features`

Questions include "dead-end traps" — stale tables that look correct but produce wrong results. These specifically test whether corrections can prevent the LLM from falling into schema traps.

## Quick start

```bash
uv sync
uv run mcp-memory          # standalone mode
uv run mcp-memory-mdw      # MotherDuck wrapper mode
```

### Claude Desktop configuration

```json
{
  "mcpServers": {
    "my-warehouse": {
      "command": "uv",
      "args": ["run", "mcp-memory-mdw", "--read-write"],
      "env": {
        "MOTHERDUCK_TOKEN": "your_token",
        "MCP_MEMORY_MANIFEST_PATH": "/path/to/dbt/target/manifest.json",
        "MCP_MEMORY_CORRECTIONS_PATH": "/path/to/corrections.json"
      }
    }
  }
}
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MCP_MEMORY_DATA_DIR` | `data/` | Base directory for data files |
| `MCP_MEMORY_DUCKDB_PATH` | `data/jaffle_shop/jaffle_shop.duckdb` | DuckDB database path (standalone mode) |
| `MCP_MEMORY_MANIFEST_PATH` | `dbt_project/target/manifest.json` | dbt manifest.json path |
| `MCP_MEMORY_CORRECTIONS_PATH` | `data/corrections.json` | Corrections JSON path |
| `MCP_MEMORY_POPULARITY_DB` | `data/popularity.duckdb` | Popularity tracking database |
| `MCP_MEMORY_CORRECTIONS` | `true` | Enable/disable corrections |
| `MCP_MEMORY_DBT` | `true` | Enable/disable dbt context |
| `MCP_MEMORY_POPULARITY` | `true` | Enable/disable popularity tracking |

## Why a semantic/memory layer for MCP?

MCP gives LLMs access to tools. But tools alone aren't enough — an LLM with `execute_query` and `list_tables` will still write bad SQL against an unfamiliar schema because:

1. **Schema metadata is necessary but not sufficient.** Column names and types tell you *what exists*, not *how to use it correctly*. That `amounts are in cents` insight? It's not in the schema. It's in someone's head, a Slack thread, or a dbt description that the LLM never sees.

2. **LLMs don't learn from their mistakes within a session.** If an LLM hits a naming trap in turn 1, it has no mechanism to avoid it in turn 10 (or in the next conversation). Corrections make that learning persistent and shareable.

3. **dbt already solved the documentation problem.** Analytics teams invest heavily in documenting models — column descriptions, tests, lineage. A semantic layer surfaces that work directly to the LLM, rather than having it guess from raw `information_schema`.

4. **Usage patterns encode tribal knowledge.** Which tables do people actually query? What joins work? Popularity tracking captures the implicit knowledge that experienced analysts have but schemas don't express.

The memory layer turns one-shot SQL generation into an iterative, self-improving system. Each session can contribute corrections. Each query refines popularity stats. The more you use it, the better it gets.

## Development

```bash
uv sync                    # install dependencies
uv run pytest              # run tests
uv run ruff check .        # lint
```

Requires Python >= 3.11. Uses `sqlglot` for SQL parsing (DuckDB dialect) and `FastMCP` for the MCP server.
