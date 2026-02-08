"""Popularity and join pattern tracking — records and serves query usage stats."""

from pathlib import Path

import duckdb
import sqlglot
import sqlglot.expressions as exp


class PopularityTracker:
    """Tracks table, column, and join pattern popularity in a DuckDB database."""

    def __init__(self, db_path: Path):
        self.db = duckdb.connect(str(db_path))
        self._init_tables()

    def _init_tables(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS table_popularity (
                table_name VARCHAR NOT NULL,
                query_count INTEGER DEFAULT 0,
                last_queried TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (table_name)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS join_patterns (
                left_table VARCHAR NOT NULL,
                right_table VARCHAR NOT NULL,
                join_key VARCHAR NOT NULL,
                join_type VARCHAR DEFAULT 'INNER',
                frequency INTEGER DEFAULT 0,
                last_used TIMESTAMP DEFAULT current_timestamp,
                PRIMARY KEY (left_table, right_table, join_key)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS column_usage (
                table_name VARCHAR NOT NULL,
                column_name VARCHAR NOT NULL,
                usage_context VARCHAR,
                frequency INTEGER DEFAULT 0,
                PRIMARY KEY (table_name, column_name, usage_context)
            )
        """)

    def record_query(self, sql: str) -> None:
        """Parse executed SQL and update popularity tables."""
        try:
            parsed = sqlglot.parse_one(sql, dialect="duckdb")
        except sqlglot.errors.ParseError:
            return

        if not isinstance(parsed, exp.Select):
            return

        tables = [t.name for t in parsed.find_all(exp.Table)]
        for table in tables:
            self.db.execute("""
                INSERT INTO table_popularity (table_name, query_count, last_queried)
                VALUES (?, 1, now())
                ON CONFLICT (table_name) DO UPDATE SET
                    query_count = table_popularity.query_count + 1,
                    last_queried = now()
            """, [table])

        for join in parsed.find_all(exp.Join):
            join_table = join.find(exp.Table)
            join_on = join.find(exp.EQ)
            if join_table and join_on:
                left_col = join_on.left
                join_key = left_col.name if hasattr(left_col, "name") else str(left_col)
                join_kind = join.args.get("kind", "INNER")
                join_type = join_kind.upper() if isinstance(join_kind, str) else "INNER"

                left_table = tables[0] if tables else "unknown"
                self.db.execute("""
                    INSERT INTO join_patterns
                        (left_table, right_table, join_key, join_type, frequency, last_used)
                    VALUES (?, ?, ?, ?, 1, now())
                    ON CONFLICT (left_table, right_table, join_key) DO UPDATE SET
                        frequency = join_patterns.frequency + 1,
                        last_used = now()
                """, [left_table, join_table.name, join_key, join_type])

        for col in parsed.find_all(exp.Column):
            table = col.table or "unknown"
            context = self._get_column_context(col, parsed)
            self.db.execute("""
                INSERT INTO column_usage (table_name, column_name, usage_context, frequency)
                VALUES (?, ?, ?, 1)
                ON CONFLICT (table_name, column_name, usage_context) DO UPDATE SET
                    frequency = column_usage.frequency + 1
            """, [table, col.name, context])

    def _get_column_context(self, col: exp.Column, root: exp.Expression) -> str:
        parent = col.parent
        while parent and parent is not root:
            if isinstance(parent, exp.Where):
                return "where"
            if isinstance(parent, exp.Group):
                return "group_by"
            if isinstance(parent, exp.Order):
                return "order_by"
            parent = parent.parent
        return "select"

    def get_popular_context(self, tables: list[str] | None = None) -> str:
        """Get query popularity stats and common join patterns."""
        parts: list[str] = []

        if tables:
            for table in tables:
                row = self.db.execute(
                    "SELECT query_count FROM table_popularity WHERE table_name = ?", [table]
                ).fetchone()
                if row:
                    parts.append(f"**{table}**: queried {row[0]} times")

                joins = self.db.execute("""
                    SELECT right_table, join_key, join_type, frequency
                    FROM join_patterns
                    WHERE left_table = ?
                    ORDER BY frequency DESC LIMIT 3
                """, [table]).fetchall()
                for j in joins:
                    parts.append(f"  → commonly joined to {j[0]} ON {j[1]} ({j[2]}, {j[3]}x)")

                cols = self.db.execute("""
                    SELECT column_name, usage_context, frequency
                    FROM column_usage
                    WHERE table_name = ?
                    ORDER BY frequency DESC LIMIT 5
                """, [table]).fetchall()
                if cols:
                    col_summary = ", ".join(f"{c[0]} ({c[1]})" for c in cols)
                    parts.append(f"  Popular columns: {col_summary}")
        else:
            top = self.db.execute("""
                SELECT table_name, query_count
                FROM table_popularity
                ORDER BY query_count DESC LIMIT 5
            """).fetchall()
            parts.append("**Most queried tables:**")
            for t in top:
                parts.append(f"  {t[0]}: {t[1]} queries")

            top_joins = self.db.execute("""
                SELECT left_table, right_table, join_key, frequency
                FROM join_patterns
                ORDER BY frequency DESC LIMIT 5
            """).fetchall()
            if top_joins:
                parts.append("\n**Common join patterns:**")
                for j in top_joins:
                    parts.append(f"  {j[0]} JOIN {j[1]} ON {j[2]} ({j[3]}x)")

        return "\n".join(parts) if parts else "No usage data recorded yet."

    def seed(self, seed_sql_path: Path) -> None:
        """Load pre-seeded popularity data from a SQL file."""
        if seed_sql_path.exists():
            sql = seed_sql_path.read_text()
            for statement in sql.split(";"):
                stmt = statement.strip()
                if stmt:
                    self.db.execute(stmt)

    def close(self) -> None:
        self.db.close()
