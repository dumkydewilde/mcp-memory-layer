#!/usr/bin/env bash
# Refreshes the dbt manifest from the datateam repo for the personal MCP memory server.
set -euo pipefail

DATATEAM_DIR="${DATATEAM_DIR:-$HOME/code/datateam}"
DBT_DIR="$DATATEAM_DIR/dbt"
OUTPUT_DIR="${MCP_MEMORY_MDW_DIR:-$HOME/.mcp-memory-mdw}"
MANIFEST_OUT="$OUTPUT_DIR/manifest.json"

mkdir -p "$OUTPUT_DIR"

if [ -f "$DBT_DIR/target/manifest.json" ]; then
    echo "Copying existing manifest from $DBT_DIR/target/manifest.json..."
    cp "$DBT_DIR/target/manifest.json" "$MANIFEST_OUT"
else
    echo "No existing manifest found. Compiling with --target test..."
    cd "$DBT_DIR"
    uv run --with dbt-duckdb==1.10.0 dbt compile --profiles-dir . --target test

    # test target resolves database Jinja to mdw_dev; fix to match prod
    sed 's/mdw_dev/mdw/g; s/mdw_landing_dev/mdw_landing/g' \
        "$DBT_DIR/target/manifest.json" > "$MANIFEST_OUT"
fi

MODEL_COUNT=$(python3 -c "
import json, sys
m = json.load(open('$MANIFEST_OUT'))
print(sum(1 for v in m['nodes'].values() if v.get('resource_type') == 'model'))
" 2>/dev/null || echo "?")

echo "Done. $MANIFEST_OUT has $MODEL_COUNT models."
