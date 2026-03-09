# MCP Memory Layer for Text-to-SQL

An [MCP](https://modelcontextprotocol.io/) server that wraps a DuckDB/dbt data warehouse with a **memory layer** — corrections, dbt model context, and query popularity tracking — so LLMs write better SQL on the first try.

Comes with a ready-to-run **jaffle_shop** example and an **evaluation framework** for A/B testing memory features.

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

## Quick start

The repo includes a complete jaffle_shop example — a DuckDB database with dbt models, pre-seeded corrections, and popularity data.

```bash
uv sync
uv run mcp-memory
```

### Claude Desktop configuration

```json
{
  "mcpServers": {
    "memory-layer": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-memory-layer", "mcp-memory"]
    }
  }
}
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MCP_MEMORY_DATA_DIR` | `data/` | Base directory for data files |
| `MCP_MEMORY_DUCKDB_PATH` | `data/jaffle_shop/jaffle_shop.duckdb` | DuckDB database path |
| `MCP_MEMORY_MANIFEST_PATH` | `dbt_project/target/manifest.json` | dbt manifest.json path |
| `MCP_MEMORY_CORRECTIONS_PATH` | `data/corrections.json` | Corrections JSON path |
| `MCP_MEMORY_POPULARITY_DB` | `data/popularity.duckdb` | Popularity tracking database |
| `MCP_MEMORY_CORRECTIONS` | `true` | Enable/disable corrections |
| `MCP_MEMORY_DBT` | `true` | Enable/disable dbt context |
| `MCP_MEMORY_POPULARITY` | `true` | Enable/disable popularity tracking |

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

## Bring your own project

The memory layer works with any DuckDB/dbt project — not just the bundled jaffle_shop demo.

### Quick setup

```bash
# Initialize config directory (~/.mcp-memory/)
mcp-memory-cli init \
  --duckdb-path /path/to/your/database.duckdb \
  --manifest-path /path/to/your/dbt/target/manifest.json
```

This creates:
- `~/.mcp-memory/config.toml` — paths and feature flags
- `~/.mcp-memory/corrections.json` — empty corrections store (grows as you use it)

Then run the server:

```bash
mcp-memory    # reads config from ~/.mcp-memory/config.toml
```

### Connecting your dbt manifest

The `manifest` path supports multiple sources. The server resolves the manifest on startup:

```toml
[paths]
# Local file — point to your dbt project's target directory
manifest = "/path/to/your/dbt/target/manifest.json"

# URL — S3 presigned URL, GCS signed URL, or any HTTP endpoint
manifest = "https://my-bucket.s3.amazonaws.com/dbt/manifest.json"
```

**Local file** is the simplest: run `dbt compile` (or `dbt build`) and point to `target/manifest.json`.

**URL** is useful for teams: upload the manifest to a shared bucket as part of your dbt CI/CD pipeline (e.g. `dbt build && aws s3 cp target/manifest.json s3://...`). The server caches fetched manifests locally and re-fetches when the cache is older than 1 hour.

### Config file

Instead of env vars, you can configure everything in `~/.mcp-memory/config.toml`:

```toml
[paths]
duckdb = "/path/to/your/database.duckdb"
manifest = "/path/to/your/dbt/target/manifest.json"
corrections = "~/.mcp-memory/corrections.json"
# popularity_db = "~/.mcp-memory/popularity.duckdb"

[features]
query = true
corrections = true
dbt = true
popularity = true
```

Precedence: **env vars > config.toml > defaults**. You can mix both — use the config file for stable paths and env vars for overrides.

### What you need

| Component | Required? | Notes |
|-----------|-----------|-------|
| DuckDB database | Yes | Local `.duckdb` file |
| dbt manifest | Recommended | Local file or URL. Without it, `list_dbt_models` and `get_dbt_context` are disabled. |
| corrections.json | No | Starts empty, grows via `save_correction` tool calls |
| popularity seed | No | Popularity tracking auto-populates from real queries |

### Without dbt

If you don't use dbt, set `dbt = false` in config (or `MCP_MEMORY_DBT=false`). The server runs with just the query tool + corrections + popularity tracking. You can still save corrections about your schema and benefit from popularity-based join suggestions.

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
