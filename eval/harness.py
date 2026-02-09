"""Evaluation harness for MCP memory layer A/B testing."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml

from eval.scorer import score_response, score_text_response

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


# ---------------------------------------------------------------------------
# API abstraction: Anthropic vs OpenRouter (OpenAI-compatible)
# ---------------------------------------------------------------------------

def create_client(api: str):
    """Create an API client based on the selected backend."""
    if api == "anthropic":
        from anthropic import Anthropic
        return Anthropic()
    elif api == "openrouter":
        from openai import OpenAI
        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )
    else:
        raise ValueError(f"Unknown API backend: {api}")


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-style tool defs to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    """Convert internal message format to OpenAI chat messages."""
    oai: list[dict] = [{"role": "system", "content": system}]

    for msg in messages:
        if msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                # Tool results
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        oai.append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": item["content"],
                        })
            else:
                oai.append({"role": "user", "content": content})
        elif msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block["text"])
                        elif block.get("type") == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(block["input"]),
                                },
                            })
                oai_msg: dict = {"role": "assistant"}
                if text_parts:
                    oai_msg["content"] = "\n".join(text_parts)
                else:
                    oai_msg["content"] = None
                if tool_calls:
                    oai_msg["tool_calls"] = tool_calls
                oai.append(oai_msg)
            else:
                oai.append({"role": "assistant", "content": content})

    return oai


def call_api(client, api: str, model: str, system: str, tools: list[dict], messages: list[dict]):
    """Make an API call and return a normalized response dict.

    Returns:
        {
            "assistant_content": list[dict],  # internal format blocks
            "tool_blocks": list[dict],         # {id, name, input}
            "has_tool_use": bool,
            "is_done": bool,
            "input_tokens": int,
            "output_tokens": int,
        }
    """
    if api == "anthropic":
        return _call_anthropic(client, model, system, tools, messages)
    else:
        return _call_openrouter(client, model, system, tools, messages)


def _call_anthropic(client, model, system, tools, messages):
    response = client.messages.create(
        model=model,
        system=system,
        tools=tools,
        messages=messages,
        max_tokens=2000,
        temperature=0,
    )
    assistant_content = []
    tool_blocks = []
    for block in response.content:
        if block.type == "text":
            assistant_content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            assistant_content.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
            tool_blocks.append({"id": block.id, "name": block.name, "input": block.input})

    return {
        "assistant_content": assistant_content,
        "tool_blocks": tool_blocks,
        "has_tool_use": bool(tool_blocks),
        "is_done": response.stop_reason == "end_turn",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def _call_openrouter(client, model, system, tools, messages):
    oai_messages = _messages_to_openai(system, messages)
    oai_tools = _tools_to_openai(tools)

    kwargs: dict = {
        "model": model,
        "messages": oai_messages,
        "max_tokens": 2000,
        "temperature": 0,
    }
    if oai_tools:
        kwargs["tools"] = oai_tools

    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    msg = choice.message

    assistant_content = []
    tool_blocks = []

    if msg.content:
        assistant_content.append({"type": "text", "text": msg.content})

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                tool_input = {}
            assistant_content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": tool_input,
            })
            tool_blocks.append({"id": tc.id, "name": tc.function.name, "input": tool_input})

    usage = response.usage
    return {
        "assistant_content": assistant_content,
        "tool_blocks": tool_blocks,
        "has_tool_use": bool(tool_blocks),
        "is_done": choice.finish_reason == "stop",
        "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
    }


# ---------------------------------------------------------------------------
# Tool definitions and system prompt
# ---------------------------------------------------------------------------

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
    """Build tool definitions (Anthropic format, converted at call time for OpenRouter)."""
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
                "name": "get_model_sql",
                "description": "Get the full raw SQL for a dbt model. Use when you need to understand exact transformations, column renames, or business logic.",
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


# ---------------------------------------------------------------------------
# Tool simulation and SQL extraction
# ---------------------------------------------------------------------------

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
    elif tool_name == "get_model_sql" and dbt_manifest:
        return dbt_manifest.get_model_sql(tool_input["table_name"])
    elif tool_name == "list_dbt_models" and dbt_manifest:
        return dbt_manifest.list_models()
    elif tool_name == "get_popular_context" and popularity_tracker:
        return popularity_tracker.get_popular_context(tool_input.get("tables"))
    elif tool_name == "query":
        return "(Query execution skipped in eval mode — please provide SQL in your response.)"
    else:
        return f"Tool '{tool_name}' not available in this configuration."


def extract_sql_from_messages(messages: list) -> str | None:
    """Extract SQL from the assistant's messages.

    Checks (in priority order):
    1. ```sql code blocks in text responses
    2. SQL from the last `query` tool call input
    """
    # First pass: look for ```sql blocks in text
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

    # Fallback: extract from the last query tool call
    for msg in reversed(messages):
        if msg["role"] != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in reversed(content):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "query"
                ):
                    return block["input"].get("sql")

    return None


def extract_text_from_messages(messages: list) -> str:
    """Extract all assistant text from messages (for text-answer scoring)."""
    parts = []
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block["text"])
        elif isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


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


# ---------------------------------------------------------------------------
# Core evaluation loop
# ---------------------------------------------------------------------------

def run_question(
    client,
    api: str,
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
        resp = call_api(client, api, model, system, tools, messages)

        total_input_tokens += resp["input_tokens"]
        total_output_tokens += resp["output_tokens"]

        messages.append({"role": "assistant", "content": resp["assistant_content"]})

        for tb in resp["tool_blocks"]:
            tool_calls.append({"name": tb["name"], "input": tb["input"]})

        if not resp["has_tool_use"] or resp["is_done"]:
            break

        # Execute tool calls and add results
        tool_results = []
        for tb in resp["tool_blocks"]:
            result = simulate_tool_call(
                tb["name"],
                tb["input"],
                corrections_store,
                dbt_manifest,
                popularity_tracker,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb["id"],
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    # Score based on answer type
    answer_type = question.get("answer_type", "sql")

    if answer_type == "text":
        generated_text = extract_text_from_messages(messages)
        scores = score_text_response(
            generated_text, question["expected"], question["scoring"]
        )
        generated_sql = None
    else:
        generated_sql = extract_sql_from_messages(messages)
        generated_text = None
        scores = score_response(
            generated_sql, {**question["expected"], "trap": question.get("trap")}, question["scoring"]
        )

    return {
        "id": question["id"],
        "question": question["question"],
        "answer_type": answer_type,
        "generated_sql": generated_sql,
        "generated_text": generated_text[:500] if generated_text else None,
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
    limit: int = 0,
    api: str = "anthropic",
) -> list[dict]:
    """Run all questions N times and save results."""
    with open(questions_path) as f:
        questions = yaml.safe_load(f)

    if limit > 0:
        questions = questions[:limit]

    config = CONFIGS[config_name]
    client = create_client(api)

    # Initialize stores based on config
    corrections_store = None
    dbt_manifest = None
    popularity_tracker = None

    if config.get("corrections"):
        corrections_path = data_dir / "corrections.json"
        if corrections_path.exists():
            corrections_store = CorrectionsStore(corrections_path)

    if config.get("dbt"):
        manifest_path = data_dir.parent / "dbt_project" / "target" / "manifest.json"
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
                    api,
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
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N questions (0=all)")
    parser.add_argument(
        "--api",
        choices=["anthropic", "openrouter"],
        default="openrouter",
        help="API backend (default: openrouter)",
    )

    args = parser.parse_args()

    # Default model depends on API backend
    if args.model is None:
        args.model = {
            "anthropic": "claude-sonnet-4-5-20250929",
            "openrouter": "anthropic/claude-sonnet-4.5",
        }[args.api]

    output_path = args.output_dir / f"{args.config}.json"

    run_eval(
        args.questions, args.config, args.model, output_path, args.data_dir,
        args.runs, args.limit, args.api,
    )


if __name__ == "__main__":
    main()
