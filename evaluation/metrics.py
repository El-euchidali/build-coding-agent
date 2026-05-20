import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from agent.agent import TaskResult


def compute_metrics(results: list[tuple[str, TaskResult]]) -> dict:
    """Compute success rate, iterations, tokens, and failure breakdown."""
    if not results:
        return {
            "total": 0,
            "passed": 0,
            "success_rate": 0.0,
            "avg_iterations": 0.0,
            "failures": [],
            "failure_breakdown": {},
        }

    passed = sum(1 for _, r in results if r.success)
    total = len(results)
    iterations = [r.iterations for _, r in results]
    failures = [
        {
            "id": task_id,
            "error": r.error,
            "category": r.failure_category or "unknown",
            "output": (r.output or "")[:500],
            "iterations": r.iterations,
        }
        for task_id, r in results
        if not r.success
    ]

    categories = Counter(
        r.failure_category or "unknown" for _, r in results if not r.success
    )

    total_tokens = sum(
        r.tokens.get("total", 0) for _, r in results if r.tokens
    )
    avg_tokens = round(total_tokens / total, 1) if total else 0

    return {
        "total": total,
        "passed": passed,
        "success_rate": round(passed / total * 100, 1),
        "avg_iterations": round(sum(iterations) / total, 2),
        "failures": failures,
        "failure_breakdown": dict(categories),
        "avg_tokens": avg_tokens,
        "total_tokens": total_tokens,
    }


def save_results(
    results: list[tuple[str, TaskResult]],
    metrics: dict,
    path: Path | None = None,
) -> Path:
    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    if path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = results_dir / f"run_{ts}.json"

    payload = {
        "metrics": metrics,
        "tasks": [
            {"id": task_id, **{k: v for k, v in asdict(result).items()}}
            for task_id, result in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
