"""dbt manifest parser — extracts model context for MCP tool responses."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import sqlglot.expressions as exp


def extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names from SQL using sqlglot, with regex fallback."""
    try:
        parsed = sqlglot.parse_one(sql, dialect="duckdb")
        return [table.name for table in parsed.find_all(exp.Table)]
    except Exception:
        return re.findall(
            r"(?:FROM|JOIN)\s+(?:[\w.]+\.)?(\w+)", sql, re.IGNORECASE
        )


@dataclass
class ModelContext:
    unique_id: str
    name: str
    description: str
    materialized: str
    columns: dict[str, str]  # {col_name: description}
    depends_on: list[str]  # upstream model names
    raw_sql: str
    tests: list[str] = field(default_factory=list)


class DbtManifest:
    """Parsed dbt manifest providing model context."""

    def __init__(self, manifest_path: Path):
        self.models: dict[str, ModelContext] = {}
        self._full_sql: dict[str, str] = {}  # untruncated SQL per model
        if manifest_path.exists():
            self._parse(manifest_path)

    def _parse(self, manifest_path: Path) -> None:
        manifest = json.loads(manifest_path.read_text())

        for node_id, node in manifest.get("nodes", {}).items():
            if node.get("resource_type") not in ("model", "seed"):
                continue

            columns = {}
            for col_name, col_info in node.get("columns", {}).items():
                columns[col_name] = col_info.get("description", "")

            depends_on = [
                dep.split(".")[-1]
                for dep in node.get("depends_on", {}).get("nodes", [])
                if dep.startswith("model.") or dep.startswith("seed.")
            ]

            full_sql = node.get("raw_sql", node.get("raw_code", ""))
            raw_sql = full_sql
            if len(raw_sql) > 500:
                raw_sql = raw_sql[:500] + "\n-- [truncated]"

            self._full_sql[node["name"]] = full_sql
            self.models[node["name"]] = ModelContext(
                unique_id=node_id,
                name=node["name"],
                description=node.get("description", ""),
                materialized=node.get("config", {}).get("materialized", "unknown"),
                columns=columns,
                depends_on=depends_on,
                raw_sql=raw_sql,
            )

        # Attach tests to their parent models
        for node_id, node in manifest.get("nodes", {}).items():
            if node.get("resource_type") != "test":
                continue
            for dep in node.get("depends_on", {}).get("nodes", []):
                model_name = dep.split(".")[-1]
                if model_name in self.models:
                    test_desc = node.get("test_metadata", {}).get("name", node.get("name", ""))
                    self.models[model_name].tests.append(test_desc)

    def get_context(self, table_name: str) -> str:
        """Get dbt model context for a table."""
        if table_name not in self.models:
            close = [m for m in self.models if table_name.lower() in m.lower()]
            if close:
                return f"Model '{table_name}' not found. Did you mean: {', '.join(close)}?"
            return f"Model '{table_name}' not found in dbt manifest."

        model = self.models[table_name]
        parts = [f"## {model.name} ({model.materialized})"]

        if model.description:
            parts.append(f"\n{model.description}")

        if model.columns:
            parts.append("\n### Columns")
            for col, desc in model.columns.items():
                parts.append(f"- **{col}**: {desc}" if desc else f"- {col}")

        if model.depends_on:
            parts.append(f"\n### Upstream: {' → '.join(model.depends_on)} → {model.name}")

        if model.tests:
            parts.append(f"\n### Tests: {', '.join(model.tests)}")

        if model.raw_sql:
            parts.append(f"\n### SQL\n```sql\n{model.raw_sql}\n```")

        return "\n".join(parts)

    def get_model_sql(self, table_name: str) -> str:
        """Get the full raw SQL for a dbt model."""
        if table_name not in self.models:
            close = [m for m in self.models if table_name.lower() in m.lower()]
            if close:
                return f"Model '{table_name}' not found. Did you mean: {', '.join(close)}?"
            return f"Model '{table_name}' not found in dbt manifest."

        sql = self._full_sql.get(table_name, "")
        if not sql:
            return f"No SQL available for '{table_name}' (may be a seed or external source)."

        return f"```sql\n{sql}\n```"

    def enrich_error(self, error_msg: str, sql: str) -> str:
        """Enrich query errors with dbt column/table context when relevant."""
        error_lower = error_msg.lower()
        is_column_error = "column" in error_lower and "not found" in error_lower
        is_table_error = "table" in error_lower and (
            "not exist" in error_lower or "not found" in error_lower
        )

        if not (is_column_error or is_table_error):
            return error_msg

        tables = extract_tables_from_sql(sql)
        hints: list[str] = []

        for table in tables:
            model = self.models.get(table)
            if model and model.columns and is_column_error:
                col_list = ", ".join(model.columns.keys())
                hints.append(f"Available columns for `{table}`: {col_list}")
            elif not model and is_table_error:
                close = [m for m in self.models if table.lower() in m.lower()]
                if close:
                    hints.append(
                        f"Model `{table}` not found. Similar models: {', '.join(close)}"
                    )

        if hints:
            return error_msg + "\n\n**dbt context:**\n" + "\n".join(hints)
        return error_msg

    def list_models(self) -> str:
        """List all available dbt models with brief descriptions."""
        if not self.models:
            return "No dbt models loaded."

        lines = ["| Model | Type | Description |", "|-------|------|-------------|"]
        for name, model in sorted(self.models.items()):
            desc = model.description
            if len(desc) > 60:
                desc = desc[:60] + "..."
            lines.append(f"| {name} | {model.materialized} | {desc} |")
        return "\n".join(lines)
