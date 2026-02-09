"""Structural SQL scoring using sqlglot."""

import re

import sqlglot
import sqlglot.expressions as exp


def extract_tables(sql: str) -> set[str]:
    """Extract table names from SQL using sqlglot."""
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        return {t.name for t in parsed.find_all(exp.Table)}
    except sqlglot.errors.ParseError:
        # Fallback: regex extraction
        return set(re.findall(r'\bFROM\s+(\w+)|\bJOIN\s+(\w+)', sql, re.IGNORECASE))


def extract_columns(sql: str) -> set[str]:
    """Extract column names from SQL."""
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        return {c.name for c in parsed.find_all(exp.Column)}
    except sqlglot.errors.ParseError:
        return set()


def contains_trap_pattern(sql: str, trap: str | None) -> bool:
    """Check if SQL contains known-bad patterns based on the trap description."""
    if not trap or not sql:
        return False

    sql_lower = sql.lower()

    # Cents trap: using raw_orders without dividing by 100
    if "cents" in trap.lower():
        tables = extract_tables(sql)
        if "raw_orders" in tables:
            if "/ 100" not in sql and "/100" not in sql and "* 0.01" not in sql:
                return True

    # Column name traps: using customer_id instead of customer on raw tables
    trap_lower = trap.lower()
    if "customer_id" in trap_lower and ("instead of" in trap_lower or "not customer_id" in trap_lower):
        if "customer_id" in sql_lower and "raw_orders" in sql_lower:
            return True

    if "customer_id" not in trap_lower and "customer" in trap_lower and "raw_orders" in trap_lower:
        if "customer_id" in sql_lower and "raw_orders" in sql_lower:
            return True

    if "sku" in trap.lower():
        if "product_id" in sql_lower and "raw_items" in sql_lower:
            return True

    if "perishable" in trap.lower() and "is_perishable_supply" in trap.lower():
        if "perishable" in sql_lower and "raw_supplies" in sql_lower:
            return True

    # Wrong value traps
    if "repeat" in trap.lower() or "existing" in trap.lower():
        bad_values = ["'repeat'", "'existing'", "'return'", "'active'", "'inactive'"]
        if any(v in sql_lower for v in bad_values):
            return True

    # Over-engineering trap: joining when pre-computed columns exist
    if "pre-computed" in trap.lower() or "already has" in trap.lower():
        tables = extract_tables(sql)
        if len(tables) > 1 and "join" in sql_lower:
            # Check if any expected table alone would suffice
            return True

    # Dead-end table trap: using non-dbt tables that exist in DB but aren't managed
    if "dead-end" in trap_lower or "not in dbt" in trap_lower:
        dead_end_tables = {"daily_revenue", "customer_segments", "order_facts"}
        if extract_tables(sql) & dead_end_tables:
            return True

    # Legacy SAP column trap: using cryptic raw column names without staging model
    if "amtttl" in trap_lower or "stscode" in trap_lower or "taxamt" in trap_lower or "discpct" in trap_lower:
        raw_legacy_cols = {"amtttl", "taxamt", "stscode", "discpct", "cstcode", "invno", "loccode", "dtcreat"}
        used_cols = {c.lower() for c in extract_columns(sql)}
        if used_cols & raw_legacy_cols:
            tables = extract_tables(sql)
            # Only a trap if querying raw table directly (not through staging)
            if "raw_legacy_invoices" in tables and "stg_legacy_invoices" not in tables:
                return True

    return False


def has_correct_joins(sql: str, expected_join_keys: list[str]) -> bool:
    """Check if SQL uses the expected join keys."""
    if not expected_join_keys:
        return True

    sql_lower = sql.lower()
    return all(key.lower() in sql_lower for key in expected_join_keys)


def score_response(generated_sql: str | None, expected: dict, scoring: dict) -> dict:
    """Score a generated SQL response based on structural correctness.

    Returns a dict of score components.
    """
    if not generated_sql:
        return {k: 0 for k in scoring}

    scores = {}
    used_tables = extract_tables(generated_sql)
    used_columns = extract_columns(generated_sql)

    # 1. Right tables?
    expected_tables = set(expected.get("tables", []))
    if expected_tables and expected_tables.issubset(used_tables):
        scores["correct_table"] = scoring.get("correct_table", 0)
    else:
        scores["correct_table"] = 0

    # 2. Right columns?
    expected_cols = expected.get("columns", [])
    if expected_cols and any(c in used_columns for c in expected_cols):
        scores["correct_column"] = scoring.get("correct_column", 0)
    else:
        scores["correct_column"] = 0

    # 3. Correct filters?
    if expected.get("filters"):
        sql_lower = generated_sql.lower()
        if any(f.split()[0].lower() in sql_lower for f in expected["filters"]):
            scores["correct_filter"] = scoring.get("correct_filter", 0)
        else:
            scores["correct_filter"] = 0
    else:
        scores["correct_filter"] = scoring.get("correct_filter", 0)

    # 4. Correct joins?
    if expected.get("join_keys"):
        if has_correct_joins(generated_sql, expected["join_keys"]):
            scores["correct_join"] = scoring.get("correct_join", 0)
        else:
            scores["correct_join"] = 0
    else:
        scores["correct_join"] = scoring.get("correct_join", 0)

    # 5. Avoids trap?
    trap = expected.get("trap") if "trap" in expected else None
    if not contains_trap_pattern(generated_sql, trap):
        scores["avoids_trap"] = scoring.get("avoids_trap", 0)
    else:
        scores["avoids_trap"] = 0

    return scores
