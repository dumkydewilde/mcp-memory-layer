#!/usr/bin/env bash
# Download jaffle_shop seed data and build the dbt project.
# Run from the dbt_project/ directory.
set -euo pipefail

SEED_DIR="seeds/jaffle-data"

if [ ! -d "$SEED_DIR" ]; then
    echo "Downloading jaffle_shop seed data..."
    mkdir -p "$SEED_DIR"
    REPO_URL="https://raw.githubusercontent.com/dbt-labs/jaffle-shop/main/seeds/jaffle-data"
    for f in raw_customers.csv raw_items.csv raw_orders.csv raw_products.csv raw_stores.csv raw_supplies.csv; do
        curl -sL "$REPO_URL/$f" -o "$SEED_DIR/$f"
        echo "  $f ($(wc -l < "$SEED_DIR/$f") lines)"
    done
fi

echo "Installing dbt packages..."
dbt deps --profiles-dir .

echo "Seeding data..."
dbt seed --profiles-dir . --vars '{"load_source_data": true}'

echo "Building models..."
dbt build --profiles-dir . --vars '{"load_source_data": true}'

echo "Done. Database at ../data/jaffle_shop.duckdb"
