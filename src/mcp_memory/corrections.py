"""Corrections store — captures non-obvious corrections that can't be inferred from schema alone."""

import json
from datetime import datetime, timezone
from pathlib import Path


class CorrectionsStore:
    """JSON-backed store for query corrections."""

    def __init__(self, corrections_path: Path):
        self.path = corrections_path
        self.corrections: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.corrections = data.get("corrections", [])
        else:
            self.corrections = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "corrections": self.corrections}
        self.path.write_text(json.dumps(data, indent=2))

    def get_corrections(self, question: str, tables: list[str] | None = None) -> str:
        """Retrieve relevant corrections for a natural language question.

        Args:
            question: The user's natural language question.
            tables: Optional list of table names to filter corrections by.

        Returns:
            Relevant corrections as formatted text, or empty if none match.
        """
        matches: list[tuple[int, dict]] = []
        q_lower = question.lower()

        for corr in self.corrections:
            score = 0
            scope = corr.get("scope", {})

            # Match on table overlap
            if tables and set(tables) & set(scope.get("tables", [])):
                score += 3

            # Match on keyword overlap
            keyword_hits = sum(1 for kw in scope.get("keywords", []) if kw in q_lower)
            score += keyword_hits

            # Match on column mentions
            col_hits = sum(1 for col in scope.get("columns", []) if col in q_lower)
            score += col_hits * 2

            if score > 0:
                matches.append((score, corr))

        matches.sort(key=lambda x: -x[0])
        top = matches[:3]

        if not top:
            return "No relevant corrections found."

        lines = ["CORRECTIONS (apply these before writing SQL):\n"]
        for _score, corr in top:
            lines.append(f"- {corr['correction']}")
            lines.append(f"  Applies to: {', '.join(corr['scope']['tables'])}\n")

        return "\n".join(lines)

    def save_correction(
        self,
        correction: str,
        tables: list[str],
        columns: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> str:
        """Save a new correction for future queries."""
        new_corr = {
            "id": f"corr_{len(self.corrections) + 1:03d}",
            "scope": {
                "tables": tables,
                "columns": columns or [],
                "keywords": keywords or [],
            },
            "correction": correction,
            "category": "learned",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "learned",
        }
        self.corrections.append(new_corr)
        self._save()
        return f"Saved correction {new_corr['id']}: {correction}"
