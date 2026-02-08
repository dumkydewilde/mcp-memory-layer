"""Evaluation harness for MCP memory layer A/B testing."""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from anthropic import Anthropic

from eval.scorer import score_response

# Server configurations to test
CONFIGS = {
    "baseline": {"corrections": False, "dbt": False, "popularity": False},
    "corrections": {"corrections": True, "dbt": False, "popularity": False},
    "dbt": {"corrections": False, "dbt": True, "popularity": False},
    "popularity": {"corrections": False, "dbt": False, "popularity": True},
    "all_features": {"corrections": True, "dbt": True, "popularity": True},
}

# Import memory layer modules
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.mcp_memory.corrections import CorrectionsStore
from src.mcp_memory.dbt_context import DbtManifest
from src.mcp_memory.popularity import PopularityTracker


def build_system_prompt(config: dict) -> str:
    """Build system prompt describing available tools."""
    prompt = (
        "You are a SQL assistant for a DuckDB database containing the jaffle_shop dataset. "
        "When asked a question, write a DuckDB SQL query to answer it. "
        "Always return your final SQL query in a ```sql code block.\n\n"
    )

    tools_desc = ["Available tools:\n- query: Execute SQL queries against the DuckDB database"]
    if config.get("corrections"):
        tools_desc.append(
            "- get_corrections: Get relevant corrections before writing SQL. "
            "Call this FIRST to avoid common mistakes."
        )
    if config.get("dbt"):
        tools_desc.append(
            "- get_dbt_context: Get model description, columns, lineage for a table.\n"
            "- list_dbt_models: List all available models."
        )
    if config.get("popularity"):
        tools_desc.append(
            "- get_popular_context: Get table popularity stats and common join patterns."
        )

    prompt += "\n".join(tools_desc)
    return prompt


def build_tools(config: dict) -> list[dict]:
    """Build Anthropic tool definitions based on enabled features."""
    tools = [
        {
            "name": "query",
            "description": "Execute a SQL query against the DuckDB database.",
            "input_schema": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "SQL query to execute"}},
                "required": ["sql"],
            },
        }
    ]

    if config.get("corrections"):
        tools.append({
            "name": "get_corrections",
            "description": (
                "Retrieve relevant corrections for a question. "
                "Call this BEFORE writing SQL to avoid common mistakes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The natural language question"},
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional table names to filter by",
                    },
                },
                "required": ["question"],
            },
        })

    if config.get("dbt"):
        tools.extend([
            {
                "name": "get_dbt_context",
                "description": "Get dbt model context: description, columns, lineage, tests.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "Model/table name"}
                    },
                    "required": ["table_name"],
                },
            },
            {
                "name": "list_dbt_models",
                "description": "List all available dbt models with descriptions.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ])

    if config.get("popularity"):
        tools.append({
            "name": "get_popular_context",
            "description": "Get query popularity stats and common join patterns.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tables": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional table names for specific patterns",
                    }
                },
            },
        })

    return tools


def simulate_tool_call(
    tool_name: str,
    tool_input: dict,
    corrections_store: CorrectionsStore | None,
    dbt_manifest: DbtManifest | None,
    popularity_tracker: PopularityTracker | None,
) -> str:
    """Execute a tool locally and return results."""
    if tool_name == "get_corrections" and corrections_store:
        return corrections_store.get_corrections(
            tool_input["question"], tool_input.get("tables")
        )
    elif tool_name == "get_dbt_context" and dbt_manifest:
        return dbt_manifest.get_context(tool_input["table_name"])
    elif tool_name == "list_dbt_models" and dbt_manifest:
        return dbt_manifest.list_models()
    elif tool_name == "get_popular_context" and popularity_tracker:
        return popularity_tracker.get_popular_context(tool_input.get("tables"))
    elif tool_name == "query":
        return "(Query execution skipped in eval mode — please provide SQL in your response.)"
    else:
        return f"Tool '{tool_name}' not available in this configuration."


def extract_sql_from_messages(messages: list) -> str | None:
    """Extract SQL from the assistant's messages."""
    for msg in reversed(messages):
        if msg["role"] != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    sql = _extract_sql_block(block["text"])
                    if sql:
                        return sql
        elif isinstance(content, str):
            sql = _extract_sql_block(content)
            if sql:
                return sql
    return None


def _extract_sql_block(text: str) -> str | None:
    """Extract SQL from a ```sql code block."""
    matches = re.findall(r"```sql\s*(.*?)```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    # Fallback: look for SELECT/WITH statements
    matches = re.findall(r"((?:SELECT|WITH)\s+.+?;)", text, re.DOTALL | re.IGNORECASE)
    if matches:
        return matches[-1].strip()
    return None


def run_question(
    client: Anthropic,
    question: dict,
    config: dict,
    model: str,
    corrections_store: CorrectionsStore | None,
    dbt_manifest: DbtManifest | None,
    popularity_tracker: PopularityTracker | None,
) -> dict:
    """Run a single question through an agentic loop."""
    system = build_system_prompt(config)
    tools = build_tools(config)
    messages = [{"role": "user", "content": question["question"]}]

    total_input_tokens = 0
    total_output_tokens = 0
    tool_calls = []

    # Agentic loop: max 5 turns
    for _ in range(5):
        response = client.messages.create(
            model=model,
            system=system,
            tools=tools,
            messages=messages,
            max_tokens=2000,
            temperature=0,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # Collect assistant response
        assistant_content = []
        has_tool_use = False

        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                has_tool_use = True
                assistant_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                tool_calls.append({"name": block.name, "input": block.input})

        messages.append({"role": "assistant", "content": assistant_content})

        if not has_tool_use or response.stop_reason == "end_turn":
            break

        # Execute tool calls and add results
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = simulate_tool_call(
                    block.name,
                    block.input,
                    corrections_store,
                    dbt_manifest,
                    popularity_tracker,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})

    # Extract SQL from conversation
    generated_sql = extract_sql_from_messages(messages)

    # Score
    scores = score_response(
        generated_sql, {**question["expected"], "trap": question.get("trap")}, question["scoring"]
    )

    return {
        "id": question["id"],
        "question": question["question"],
        "generated_sql": generated_sql,
        "scores": scores,
        "total": sum(scores.values()),
        "max_possible": sum(question["scoring"].values()),
        "target_features": question["target_features"],
        "tool_calls": tool_calls,
        "tokens": {"input": total_input_tokens, "output": total_output_tokens},
    }


def run_eval(
    questions_path: Path,
    config_name: str,
    model: str,
    output_path: Path,
    data_dir: Path,
    runs: int = 3,
) -> list[dict]:
    """Run all questions N times and save results."""
    with open(questions_path) as f:
        questions = yaml.safe_load(f)

    config = CONFIGS[config_name]
    client = Anthropic()

    # Initialize stores based on config
    corrections_store = None
    dbt_manifest = None
    popularity_tracker = None

    if config.get("corrections"):
        corrections_path = data_dir / "corrections.json"
        if corrections_path.exists():
            corrections_store = CorrectionsStore(corrections_path)

    if config.get("dbt"):
        manifest_path = data_dir / "jaffle_shop" / "target" / "manifest.json"
        if manifest_path.exists():
            dbt_manifest = DbtManifest(manifest_path)

    if config.get("popularity"):
        pop_db = data_dir / "popularity.duckdb"
        popularity_tracker = PopularityTracker(pop_db)
        seed_path = data_dir / "popularity_seed.sql"
        count = popularity_tracker.db.execute(
            "SELECT count(*) FROM table_popularity"
        ).fetchone()
        if count and count[0] == 0:
            popularity_tracker.seed(seed_path)

    all_results = []

    for run_idx in range(runs):
        print(f"\n--- Run {run_idx + 1}/{runs} for config '{config_name}' ---")
        for i, question in enumerate(questions):
            print(f"  [{i+1}/{len(questions)}] {question['id']}: {question['question'][:60]}...")
            try:
                result = run_question(
                    client,
                    question,
                    config,
                    model,
                    corrections_store,
                    dbt_manifest,
                    popularity_tracker,
                )
                result["run"] = run_idx
                all_results.append(result)
                print(
                    f"    Score: {result['total']}/{result['max_possible']} "
                    f"| Tokens: {result['tokens']['input']}+{result['tokens']['output']}"
                )
            except Exception as e:
                print(f"    ERROR: {e}")
                all_results.append({
                    "id": question["id"],
                    "question": question["question"],
                    "error": str(e),
                    "run": run_idx,
                    "scores": {k: 0 for k in question["scoring"]},
                    "total": 0,
                    "max_possible": sum(question["scoring"].values()),
                    "target_features": question["target_features"],
                    "tokens": {"input": 0, "output": 0},
                })

    # Save results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)

    if popularity_tracker:
        popularity_tracker.close()

    print(f"\nResults saved to {output_path}")
    return all_results


def main():
    parser = argparse.ArgumentParser(description="Run MCP memory layer evaluation")
    parser.add_argument("--questions", type=Path, default=Path("eval/questions.yaml"))
    parser.add_argument("--config", choices=list(CONFIGS.keys()), required=True)
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--runs", type=int, default=3)

    args = parser.parse_args()
    output_path = args.output_dir / f"{args.config}.json"

    run_eval(args.questions, args.config, args.model, output_path, args.data_dir, args.runs)


if __name__ == "__main__":
    main()
