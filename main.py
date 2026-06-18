#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.llm import smoke_test
from evaluation.humaneval_runner import (
    load_humaneval_tasks,
    run_humaneval_benchmark,
    run_single_humaneval_task,
    save_humaneval_results,
)
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


def cmd_humaneval(
    task_id: str | None,
    limit: int | None,
    offset: int,
    max_iterations: int,
    timeout: float,
) -> int:
    if task_id:
        tasks = load_humaneval_tasks(task_ids=[task_id])
        if not tasks:
            print(f"HumanEval task '{task_id}' not found.")
            return 1
        print(f"Running HumanEval {task_id}: {tasks[0].entry_point}")
        result = run_single_humaneval_task(
            tasks[0], max_iterations=max_iterations, timeout=timeout
        )
        status = "PASS" if result.success else "FAIL"
        print(f"\nResult: {status} ({result.iterations} iterations)")
        if result.tokens:
            print(
                f"Tokens: total={result.tokens.get('total', 'n/a')}"
            )
        if result.error:
            print(f"Error: {result.error}")
        results = [(task_id, result)]
    else:
        print("Running HumanEval benchmark...")
        results = run_humaneval_benchmark(
            limit=limit,
            offset=offset,
            max_iterations=max_iterations,
            timeout=timeout,
        )

    metrics = compute_metrics(results)
    out_path = save_humaneval_results(results, metrics)

    print("\n=== HumanEval Summary ===")
    print(f"Total:        {metrics['total']}")
    print(f"Passed:       {metrics['passed']}")
    print(f"Pass@1:       {metrics['success_rate']}%")
    print(f"Avg iter:     {metrics['avg_iterations']}")
    if metrics.get("total_tokens"):
        print(f"Total tokens: {metrics['total_tokens']}")
    print(f"Results:      {out_path}")

    if metrics.get("failure_breakdown"):
        print(f"Failure breakdown: {metrics['failure_breakdown']}")
    if metrics["failures"]:
        print("\nFailures:")
        for f in metrics["failures"][:10]:
            cat = f.get("category", "?")
            print(f"  - {f['id']} [{cat}]: {f.get('error', 'unknown')[:80]}")
        if len(metrics["failures"]) > 10:
            print(f"  ... and {len(metrics['failures']) - 10} more")

    return 0 if metrics["passed"] == metrics["total"] else 1


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

    if metrics.get("failure_breakdown"):
        print(f"Failure breakdown: {metrics['failure_breakdown']}")
    if metrics["failures"]:
        print("\nFailures:")
        for f in metrics["failures"]:
            cat = f.get("category", "?")
            print(f"  - {f['id']} [{cat}]: {f.get('error', 'unknown')[:80]}")

    return 0 if metrics["passed"] == metrics["total"] else 1

def cmd_swebench(limit: int | None, max_iterations: int, run_eval: bool, instance_ids: list | None = None) -> int:
    from evaluation.swebench_runner import run_swebench_benchmark
    results, report = run_swebench_benchmark(
        instance_ids=instance_ids,
        limit=limit,
        max_iterations=max_iterations,
        run_eval=run_eval,
    )
    return 0


def main() -> int:
    print("Starting the application...")
    parser = argparse.ArgumentParser(description="Coding Agent MVP")
    parser.add_argument("--smoke", action="store_true", help="Test LLM connection")
    parser.add_argument("--task", type=str, metavar="ID", help="Run single task (e.g. 001)")
    parser.add_argument("--benchmark", action="store_true", help="Run custom JSON benchmark")
    parser.add_argument("--swebench", action="store_true", help="Run SWE-bench Verified benchmark")
    parser.add_argument("--instance-ids", nargs="+", default=None, help="Specific SWE-bench instance IDs to run")
    parser.add_argument("--no-eval", action="store_true", help="Skip official evaluation (agent only)")
    parser.add_argument(
        "--humaneval",
        action="store_true",
        help="Run HumanEval benchmark (official dataset)",
    )
    parser.add_argument(
        "--humaneval-task",
        type=str,
        metavar="ID",
        help="Run single HumanEval task (e.g. HumanEval/0)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tasks")
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N tasks (HumanEval or custom benchmark)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Per-problem execution timeout for HumanEval verification (seconds)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Max agent iterations per task (default: 20)",
    )
    args = parser.parse_args()

    if args.smoke:
        return cmd_smoke()
    if args.task:
        return cmd_task(args.task, args.max_iterations)
    if args.benchmark:
        return cmd_benchmark(args.limit, args.max_iterations)
    if args.humaneval or args.humaneval_task:
        return cmd_humaneval(
            args.humaneval_task,
            args.limit,
            args.offset,
            args.max_iterations,
            args.timeout,
        )
    if args.swebench:
        return cmd_swebench(args.limit, args.max_iterations, not args.no_eval, args.instance_ids)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
