"""
SWE-bench Verified runner for the coding agent.

Usage:
    # Run agent on 10 tasks and evaluate
    python main.py --swebench --limit 10

    # Run agent only (generate patches, no evaluation)
    python main.py --swebench --limit 10 --no-eval

    # Evaluate existing predictions file
    python main.py --swebench-eval --predictions results/swebench_predictions.jsonl
"""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from datasets import load_dataset

from agent.agent import TaskResult, run_task

# ── Constants ─────────────────────────────────────────────────────────────────

DATASET_NAME   = "princeton-nlp/SWE-bench_Verified"
SPLIT          = "test"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "workspace" / "swebench"
RESULTS_DIR    = Path(__file__).resolve().parent.parent / "results"
MODEL_NAME     = "coding-agent-fsm"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SWETask:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    difficulty: str


@dataclass
class SWEResult:
    instance_id: str
    model_patch: str
    success: bool
    iterations: int
    tokens: dict | None
    error: str | None
    agent_output: str


# ── Dataset loading ───────────────────────────────────────────────────────────

def load_swebench_tasks(
    limit: int | None = None,
    offset: int = 0,
    instance_ids: list[str] | None = None,
) -> list[SWETask]:
    """Load tasks from SWE-bench Verified dataset."""
    print("Loading SWE-bench Verified dataset...")
    ds = load_dataset(DATASET_NAME, split=SPLIT)

    if instance_ids:
        tasks = [t for t in ds if t["instance_id"] in instance_ids]
    else:
        tasks = list(ds)

    if offset:
        tasks = tasks[offset:]
    if limit is not None:
        tasks = tasks[:limit]

    return [
        SWETask(
            instance_id=t["instance_id"],
            repo=t["repo"],
            base_commit=t["base_commit"],
            problem_statement=t["problem_statement"],
            hints_text=t.get("hints_text", ""),
            fail_to_pass=json.loads(t["FAIL_TO_PASS"]) if isinstance(t["FAIL_TO_PASS"], str) else t["FAIL_TO_PASS"],
            pass_to_pass=json.loads(t["PASS_TO_PASS"]) if isinstance(t["PASS_TO_PASS"], str) else t["PASS_TO_PASS"],
            difficulty=t.get("difficulty", "unknown"),
        )
        for t in tasks
    ]


# ── Workspace setup ───────────────────────────────────────────────────────────

def _setup_workspace(task: SWETask) -> Path:
    """
    Clone the repo at the base commit into the workspace.
    Returns the workspace path.
    """
    workspace = WORKSPACE_ROOT / task.instance_id.replace("/", "__")

    # Clean existing workspace
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    repo_url = f"https://github.com/{task.repo}.git"
    print(f"  Cloning {task.repo} at {task.base_commit[:8]}...")

    # Clone the repo
    result = subprocess.run(
        ["git", "clone", "--quiet", repo_url, str(workspace)],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Clone failed: {result.stderr}")

    # Checkout the base commit
    result = subprocess.run(
        ["git", "checkout", task.base_commit],
        cwd=workspace,
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"Checkout failed: {result.stderr}")

    return workspace


def _get_patch(workspace: Path) -> str:
    """Extract the git diff as a unified patch."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=workspace,
        capture_output=True, text=True
    )
    patch = result.stdout.strip()

    # Also check staged changes
    if not patch:
        result = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=workspace,
            capture_output=True, text=True
        )
        patch = result.stdout.strip()

    return patch


def _build_task_description(task: SWETask) -> str:
    """Build the task description for the agent."""
    desc = f"""You are fixing a bug in the {task.repo} repository.

## Problem Statement

{task.problem_statement}

## Your Task

1. Use search_codebase to find code related to the bug
2. Use read_file or view_file_range to understand the relevant code
3. Apply a minimal fix using str_replace
4. Use git_diff to verify your changes look correct

## Critical Rule About Tests

Tests may not run in this environment due to missing dependencies.
If run_tests fails with ImportError or ModuleNotFoundError:
- Do NOT spend more than one attempt trying to install dependencies
- Instead, focus on reading the code, understanding the bug, and applying the fix
- Use git_diff to verify your patch looks correct
- A correct patch is more valuable than a working test environment

## Important Rules

- Make the smallest possible change that fixes the bug
- Do NOT modify test files
- Do NOT add new files unless absolutely necessary
- Use search_codebase for semantic search (finds related code even without exact name match)
- Use search_code for exact text matching (when you know the function or variable name)
- Prefer str_replace over write_file — surgical edits only
- Use git_diff before finishing to verify your patch

## Failing Tests (for context — helps you understand what is broken)

{json.dumps(task.fail_to_pass, indent=2)}
"""
    if task.hints_text:
        desc += f"""
## Hints

{task.hints_text}
"""
    return desc

# ── Agent runner ──────────────────────────────────────────────────────────────

def run_single_swebench_task(
    task: SWETask,
    max_iterations: int = 20,
) -> SWEResult:
    """Run the agent on one SWE-bench task."""

    print(f"\n{'='*60}")
    print(f"Task: {task.instance_id}")
    print(f"Repo: {task.repo}")
    print(f"Difficulty: {task.difficulty}")
    print(f"{'='*60}")

    # Setup workspace
    try:
        workspace = _setup_workspace(task)
    except Exception as e:
        print(f"  Setup failed: {e}")
        return SWEResult(
            instance_id=task.instance_id,
            model_patch="",
            success=False,
            iterations=0,
            tokens=None,
            error=f"setup_failed: {e}",
            agent_output="",
        )

    # Build task description
    description = _build_task_description(task)

    # Run agent
    print(f"  Running agent (max {max_iterations} iterations)...")
    try:
        result = run_task(
            description=description,
            workspace=workspace,
            max_iterations=max_iterations,
        )
    except Exception as e:
        print(f"  Agent crashed: {e}")
        return SWEResult(
            instance_id=task.instance_id,
            model_patch="",
            success=False,
            iterations=0,
            tokens=None,
            error=f"agent_crashed: {e}",
            agent_output="",
        )

    # Extract patch
    patch = _get_patch(workspace)

    status = "PASS" if result.success else "FAIL"
    print(f"  Agent: {status} ({result.iterations} iterations)")
    print(f"  Patch: {len(patch)} chars")
    if result.tokens:
        print(f"  Tokens: {result.tokens.get('total', 0)}")

    return SWEResult(
        instance_id=task.instance_id,
        model_patch=patch,
        success=result.success,
        iterations=result.iterations,
        tokens=result.tokens,
        error=result.error,
        agent_output=result.output,
    )


# ── Predictions file ──────────────────────────────────────────────────────────

def save_predictions(results: list[SWEResult], run_id: str) -> Path:
    """
    Save predictions in the format expected by the official SWE-bench evaluator.
    Each line is a JSON object with instance_id, model_patch, model_name_or_path.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"swebench_predictions_{run_id}.jsonl"

    with open(path, "w", encoding="utf-8") as f:
        for r in results:
            line = {
                "instance_id": r.instance_id,
                "model_patch": r.model_patch,
                "model_name_or_path": MODEL_NAME,
            }
            f.write(json.dumps(line) + "\n")

    print(f"\nPredictions saved: {path}")
    return path


def save_agent_results(results: list[SWEResult], run_id: str) -> Path:
    """Save full agent results including tokens, iterations, errors."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"swebench_agent_{run_id}.json"

    payload = {
        "run_id": run_id,
        "model": MODEL_NAME,
        "total": len(results),
        "with_patch": sum(1 for r in results if r.model_patch),
        "agent_passed": sum(1 for r in results if r.success),
        "tasks": [
            {
                "instance_id": r.instance_id,
                "success": r.success,
                "iterations": r.iterations,
                "tokens": r.tokens,
                "error": r.error,
                "has_patch": bool(r.model_patch),
                "patch_size": len(r.model_patch),
            }
            for r in results
        ]
    }

    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Agent results saved: {path}")
    return path


# ── Official evaluator ────────────────────────────────────────────────────────

def run_official_evaluation(
    predictions_path: Path,
    run_id: str,
    max_workers: int = 4,
    timeout: int = 120,
) -> dict:
    """
    Run the official SWE-bench evaluator using Docker.
    Must be run from WSL or Linux — Docker required.
    """
    print(f"\nRunning official SWE-bench evaluation...")
    print(f"Predictions: {predictions_path}")
    print(f"This requires Docker and may take a long time...")

    try:
        from swebench.harness.run_evaluation import main as swe_eval

        swe_eval(
            dataset_name=DATASET_NAME,
            split=SPLIT,
            instance_ids=None,
            predictions_path=str(predictions_path),
            max_workers=max_workers,
            force_rebuild=False,
            cache_level="env",
            clean=False,
            open_file_limit=4096,
            run_id=run_id,
            timeout=timeout,
            namespace=None,
            rewrite_reports=False,
            modal=False,
        )

        # Load results
        report_path = Path(f"{run_id}.{MODEL_NAME}.json")
        if report_path.exists():
            report = json.loads(report_path.read_text())
            return report
        else:
            print(f"Warning: report file not found at {report_path}")
            return {}

    except Exception as e:
        print(f"Evaluation failed: {e}")
        print("Make sure you are running from WSL/Linux with Docker available.")
        return {"error": str(e)}


# ── Main benchmark runner ─────────────────────────────────────────────────────

def run_swebench_benchmark(
    limit: int | None = None,
    offset: int = 0,
    instance_ids: list[str] | None = None,
    max_iterations: int = 20,
    run_eval: bool = True,
    max_workers: int = 4,
) -> tuple[list[SWEResult], dict]:
    """
    Full pipeline: load tasks → run agent → save predictions → evaluate.
    Returns (results, evaluation_report).
    """
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Load tasks
    tasks = load_swebench_tasks(
        limit=limit,
        offset=offset,
        instance_ids=instance_ids,
    )
    print(f"\nLoaded {len(tasks)} SWE-bench tasks")

    # Run agent on each task
    results: list[SWEResult] = []
    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}] {task.instance_id}")
        result = run_single_swebench_task(task, max_iterations=max_iterations)
        results.append(result)

        # Save incrementally in case of crash
        save_predictions(results, run_id)
        save_agent_results(results, run_id)

    # Print agent summary
    print(f"\n{'='*60}")
    print(f"AGENT SUMMARY")
    print(f"{'='*60}")
    print(f"Total tasks:    {len(results)}")
    print(f"With patch:     {sum(1 for r in results if r.model_patch)}")
    print(f"Agent passed:   {sum(1 for r in results if r.success)}")
    total_tokens = sum(
        r.tokens.get('total', 0) for r in results if r.tokens
    )
    print(f"Total tokens:   {total_tokens:,}")

    # Run official evaluation
    report = {}
    if run_eval:
        predictions_path = RESULTS_DIR / f"swebench_predictions_{run_id}.jsonl"
        report = run_official_evaluation(
            predictions_path=predictions_path,
            run_id=run_id,
            max_workers=max_workers,
        )

        if report and "resolved" in report:
            resolved = report.get("resolved", [])
            print(f"\n{'='*60}")
            print(f"OFFICIAL EVALUATION RESULTS")
            print(f"{'='*60}")
            print(f"Resolved:  {len(resolved)}/{len(results)}")
            print(f"Resolve rate: {len(resolved)/len(results)*100:.1f}%")

    return results, report