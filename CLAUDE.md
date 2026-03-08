# MCP Memory Layer

An MCP server providing a memory layer (corrections, dbt context, popularity tracking) for better text-to-SQL over a DuckDB/dbt project.

## Architecture

- **`src/mcp_memory/server.py`** — FastMCP server with tools: `query`, `get_corrections`, `save_correction`, `get_dbt_context`, `list_dbt_models`, `get_popular_context`
- **`src/mcp_memory/corrections.py`** — Stores corrections in `data/corrections.json` (human-readable, version-controlled)
- **`src/mcp_memory/dbt_context.py`** — Parses dbt `manifest.json` into in-memory `DbtManifest`; tools return small, relevant slices to minimize token usage
- **`src/mcp_memory/popularity.py`** — Tracks query patterns (tables, joins, columns) in a DuckDB database; seeded from `data/popularity_seed.sql`
- **`data/`** — Contains corrections.json, popularity_seed.sql, and jaffle_shop project data
- **`eval/`** — Evaluation framework for A/B testing memory features
- **`tests/`** — Unit and integration tests

## Key Technical Details

- Python >= 3.11, managed with `uv`
- SQL parsing: `sqlglot` with `dialect="duckdb"` throughout
- DuckDB quirk: In `ON CONFLICT DO UPDATE SET` clauses, use `now()` not `current_timestamp` for TIMESTAMP columns (gets misinterpreted as column reference)
- Feature flags via env vars: `MCP_MEMORY_CORRECTIONS`, `MCP_MEMORY_DBT`, `MCP_MEMORY_POPULARITY` (all default `true`)
- All config paths are env-var configurable (`MCP_MEMORY_DATA_DIR`, `MCP_MEMORY_DUCKDB_PATH`, etc.)

## Test Dataset: Jaffle Shop v3

The test data uses `dbt-labs/jaffle-shop` v3 which has a significantly different schema from older versions:
- `raw_orders` amounts are in **cents**; `stg_orders` converts to dollars but retains `_cents` columns
- Column renames: `raw_orders.customer` → `customer_id`, `raw_orders.id` → `order_id`, `raw_orders.store_id` → `location_id`
- Entities: customers, orders, products, locations, supplies, order_items
- Mart models have pre-computed metrics (e.g., `customers.lifetime_spend`, `orders.count_food_items`)
- Build requires: `dbt seed --vars '{"load_source_data": true}'` and `dbt build --vars '{"load_source_data": true}'`

## Development

```bash
uv sync                    # Install dependencies
uv run pytest              # Run tests
uv run mcp-memory          # Run the MCP server
```

## Conventions

- Tools return formatted strings for LLM consumption
- Ruff for linting (E, F, I, W rules, line-length 100)
- Tests before new features
- PRs: title < 80 chars, description < 5 sentences, no test plans unless requested

@FP_CLAUDE.md
