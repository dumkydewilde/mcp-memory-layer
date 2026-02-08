"""Report generator for MCP memory layer evaluation results."""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_results(results_dir: Path) -> dict[str, list]:
    """Load all result JSON files from the results directory."""
    results = {}
    for f in results_dir.glob("*.json"):
        config_name = f.stem
        with open(f) as fh:
            results[config_name] = json.load(fh)
    return results


def compute_metrics(results: list) -> dict:
    """Compute aggregate metrics for a single configuration's results."""
    if not results:
        return {}

    # Group by question ID to handle multiple runs
    by_question: dict[str, list] = defaultdict(list)
    for r in results:
        by_question[r["id"]].append(r)

    total_score = 0
    total_max = 0
    trap_avoided = 0
    trap_total = 0
    table_correct = 0
    table_total = 0
    total_input_tokens = 0
    total_output_tokens = 0
    question_count = 0

    by_feature: dict[str, dict] = defaultdict(lambda: {"score": 0, "max": 0, "count": 0})

    for qid, runs in by_question.items():
        # Average across runs
        avg_total = sum(r.get("total", 0) for r in runs) / len(runs)
        avg_max = runs[0].get("max_possible", 0)

        total_score += avg_total
        total_max += avg_max
        question_count += 1

        # Trap avoidance
        for r in runs:
            scores = r.get("scores", {})
            trap_max = r.get("max_possible", 0)
            if "avoids_trap" in scores:
                trap_total += 1
                if scores["avoids_trap"] > 0:
                    trap_avoided += 1

            if "correct_table" in scores:
                table_total += 1
                if scores["correct_table"] > 0:
                    table_correct += 1

        # Tokens
        for r in runs:
            tokens = r.get("tokens", {})
            total_input_tokens += tokens.get("input", 0)
            total_output_tokens += tokens.get("output", 0)

        # By feature category
        for r in runs:
            for feature in r.get("target_features", []):
                by_feature[feature]["score"] += r.get("total", 0)
                by_feature[feature]["max"] += r.get("max_possible", 0)
                by_feature[feature]["count"] += 1

    num_runs = sum(len(runs) for runs in by_question.values())

    return {
        "mean_score": total_score / question_count if question_count else 0,
        "mean_max": total_max / question_count if question_count else 0,
        "score_pct": (total_score / total_max * 100) if total_max else 0,
        "trap_avoidance_rate": (trap_avoided / trap_total * 100) if trap_total else 0,
        "table_accuracy": (table_correct / table_total * 100) if table_total else 0,
        "mean_input_tokens": total_input_tokens / num_runs if num_runs else 0,
        "mean_output_tokens": total_output_tokens / num_runs if num_runs else 0,
        "total_questions": question_count,
        "total_runs": num_runs,
        "by_feature": {
            k: {
                "score_pct": (v["score"] / v["max"] * 100) if v["max"] else 0,
                "count": v["count"],
            }
            for k, v in by_feature.items()
        },
    }


def compute_feature_lift(baseline_metrics: dict, feature_metrics: dict) -> dict:
    """Compute score improvement vs baseline."""
    return {
        "score_lift": feature_metrics.get("score_pct", 0) - baseline_metrics.get("score_pct", 0),
        "trap_lift": (
            feature_metrics.get("trap_avoidance_rate", 0)
            - baseline_metrics.get("trap_avoidance_rate", 0)
        ),
        "table_lift": (
            feature_metrics.get("table_accuracy", 0)
            - baseline_metrics.get("table_accuracy", 0)
        ),
        "token_overhead": (
            feature_metrics.get("mean_input_tokens", 0)
            + feature_metrics.get("mean_output_tokens", 0)
            - baseline_metrics.get("mean_input_tokens", 0)
            - baseline_metrics.get("mean_output_tokens", 0)
        ),
    }


def generate_report(results_dir: Path) -> str:
    """Generate a markdown comparison report."""
    all_results = load_results(results_dir)

    if not all_results:
        return "No results found."

    metrics = {name: compute_metrics(results) for name, results in all_results.items()}

    lines = ["# MCP Memory Layer — Evaluation Report\n"]

    # Summary table
    lines.append("## Overall Results\n")
    lines.append(
        "| Config | Score % | Trap Avoidance | Table Accuracy | Avg Tokens | Questions |"
    )
    lines.append("|--------|---------|---------------|----------------|------------|-----------|")

    for name in ["baseline", "corrections", "dbt", "popularity", "all_features"]:
        if name not in metrics:
            continue
        m = metrics[name]
        avg_tokens = m.get("mean_input_tokens", 0) + m.get("mean_output_tokens", 0)
        lines.append(
            f"| {name} | {m['score_pct']:.1f}% | {m['trap_avoidance_rate']:.1f}% "
            f"| {m['table_accuracy']:.1f}% | {avg_tokens:.0f} | {m['total_questions']} |"
        )

    # Feature lift
    if "baseline" in metrics:
        lines.append("\n## Feature Lift vs Baseline\n")
        lines.append("| Config | Score Lift | Trap Lift | Table Lift | Token Overhead |")
        lines.append("|--------|-----------|-----------|------------|---------------|")

        for name in ["corrections", "dbt", "popularity", "all_features"]:
            if name not in metrics:
                continue
            lift = compute_feature_lift(metrics["baseline"], metrics[name])
            lines.append(
                f"| {name} | {lift['score_lift']:+.1f}pp | {lift['trap_lift']:+.1f}pp "
                f"| {lift['table_lift']:+.1f}pp | {lift['token_overhead']:+.0f} |"
            )

    # Per-feature breakdown
    lines.append("\n## Per-Feature Category Breakdown\n")
    for config_name, m in metrics.items():
        if not m.get("by_feature"):
            continue
        lines.append(f"### {config_name}\n")
        lines.append("| Feature Target | Score % | Questions |")
        lines.append("|---------------|---------|-----------|")
        for feat, fdata in m["by_feature"].items():
            lines.append(f"| {feat} | {fdata['score_pct']:.1f}% | {fdata['count']} |")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate evaluation comparison report")
    parser.add_argument("--results-dir", type=Path, default=Path("eval/results"))
    parser.add_argument("--output", type=Path, default=None)

    args = parser.parse_args()
    report = generate_report(args.results_dir)

    if args.output:
        args.output.write_text(report)
        print(f"Report saved to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
