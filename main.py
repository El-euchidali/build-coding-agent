#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.llm import smoke_test
from evaluation.metrics import compute_metrics, save_results
from evaluation.runner import load_tasks, run_benchmark, run_single_task


def cmd_smoke() -> int:
    print("Testing InnKube LLM connection...")
    try:
        reply = smoke_test()
        print(f"Response: {reply}")
        return 0
    except Exception as e:
        print(f"Smoke test failed: {e}", file=sys.stderr)
        return 1


def cmd_task(task_id: str, max_iterations: int) -> int:
    tasks = load_tasks()
    task = next((t for t in tasks if t.id == task_id), None)
    if task is None:
        print(f"Task '{task_id}' not found. Available: {[t.id for t in tasks]}")
        return 1

    print(f"Running task {task.id}: {task.entry_point}")
    result = run_single_task(task, max_iterations=max_iterations)
    status = "PASS" if result.success else "FAIL"
    print(f"\nResult: {status} ({result.iterations} iterations)")
    if result.tokens:
        print(f"Tokens: prompt={result.tokens['prompt']} completion={result.tokens['completion']} total={result.tokens['total']}")
    if result.output:
        print(f"Output:\n{result.output}")
    if result.error:
        print(f"Error: {result.error}")
    return 0 if result.success else 1


def cmd_benchmark(limit: int | None, max_iterations: int) -> int:
    print("Running benchmark...")
    results = run_benchmark(limit=limit, max_iterations=max_iterations)
    metrics = compute_metrics(results)
    out_path = save_results(results, metrics)

    print("\n=== Benchmark Summary ===")
    print(f"Total:   {metrics['total']}")
    print(f"Passed:  {metrics['passed']}")
    print(f"Rate:    {metrics['success_rate']}%")
    print(f"Avg iter:{metrics['avg_iterations']}")
    print(f"Results: {out_path}")

    if metrics["failures"]:
        print("\nFailures:")
        for f in metrics["failures"]:
            print(f"  - {f['id']}: {f.get('error', 'unknown')}")

    return 0 if metrics["passed"] == metrics["total"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Coding Agent MVP")
    parser.add_argument("--smoke", action="store_true", help="Test LLM connection")
    parser.add_argument("--task", type=str, metavar="ID", help="Run single task (e.g. 001)")
    parser.add_argument("--benchmark", action="store_true", help="Run full benchmark")
    parser.add_argument("--limit", type=int, default=None, help="Limit benchmark tasks")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="Max agent iterations per task",
    )
    args = parser.parse_args()

    if args.smoke:
        return cmd_smoke()
    if args.task:
        return cmd_task(args.task, args.max_iterations)
    if args.benchmark:
        return cmd_benchmark(args.limit, args.max_iterations)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
