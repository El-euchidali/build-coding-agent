"""HumanEval benchmark runner using the official human-eval package."""

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path

from human_eval.data import read_problems
from human_eval.execution import check_correctness

from agent.agent import TaskResult, run_task
from agent.prompts import HUMANEVAL_TASK_ADDENDUM
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "workspace" / "humaneval"


@dataclass
class HumanEvalTask:
    task_id: str
    entry_point: str
    description: str
    prompt: str
    test: str


def _humaneval_sort_key(task_id: str) -> int:
    """Numeric sort: HumanEval/2 before HumanEval/10."""
    return int(task_id.split("/")[1])


def _sanitize_task_id(task_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", task_id)


def load_humaneval_tasks(
    limit: int | None = None,
    offset: int = 0,
    task_ids: list[str] | None = None,
) -> list[HumanEvalTask]:
    """Load HumanEval problems from the official dataset."""
    problems = read_problems()
    if task_ids:
        ordered = [problems[tid] for tid in task_ids if tid in problems]
    else:
        keys = sorted(problems.keys(), key=_humaneval_sort_key)
        ordered = [problems[k] for k in keys]

    if offset:
        ordered = ordered[offset:]
    if limit is not None:
        ordered = ordered[:limit]

    tasks: list[HumanEvalTask] = []
    for problem in ordered:
        entry_point = problem["entry_point"]
        doc = _extract_docstring(problem["prompt"])
        description = (
            f"Complete the function `{entry_point}` in solution.py.\n\n"
            f"{doc}\n"
            f"{HUMANEVAL_TASK_ADDENDUM.format(entry_point=entry_point)}"
        )
        tasks.append(
            HumanEvalTask(
                task_id=problem["task_id"],
                entry_point=entry_point,
                description=description,
                prompt=problem["prompt"],
                test=problem["test"],
            )
        )
    return tasks


def _extract_docstring(prompt: str) -> str:
    match = re.search(r'"""(.*?)"""', prompt, re.DOTALL)
    if match:
        return match.group(1).strip()
    return "Implement the function according to the stub in solution.py."


def _make_test_file(entry_point: str, test: str) -> str:
    return f'''"""Official HumanEval tests (auto-generated)."""
from solution import {entry_point}

{test}


def test_humaneval_official():
    check({entry_point})
'''


def _seed_workspace(task: HumanEvalTask, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "solution.py").write_text(task.prompt, encoding="utf-8")
    (workspace / "test_solution.py").write_text(
        _make_test_file(task.entry_point, task.test), encoding="utf-8"
    )


def verify_humaneval(
    problem: dict, solution_code: str, timeout: float = 5.0
) -> tuple[bool, str, str]:
    """
    Verify with official HumanEval check.
    Returns (passed, result_message, verification_mode).
    """
    prompt = problem["prompt"]
    if solution_code.startswith(prompt):
        completion = solution_code[len(prompt):]
        mode = "prefix"
        check_problem = problem
    else:
        completion = solution_code
        mode = "full_rewrite"
        check_problem = {**problem, "prompt": ""}

    result = check_correctness(check_problem, completion, timeout=timeout)

    # Windows does not support signal.setitimer — fall back to pytest
    if not result["passed"] and "signal" in result.get("result", ""):
        raise OSError(result["result"])

    return result["passed"], result["result"], mode


def _fallback_pytest_verify(workspace: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_solution.py", "-q", "--tb=line"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode == 0


def run_single_humaneval_task(
    task: HumanEvalTask,
    max_iterations: int = 12,
    timeout: float = 5.0,
) -> TaskResult:
    workspace = WORKSPACE_ROOT / _sanitize_task_id(task.task_id)
    _seed_workspace(task, workspace)

    result = run_task(
        description=task.description,
        workspace=workspace,
        max_iterations=max_iterations,
    )

    solution_code = (workspace / "solution.py").read_text(encoding="utf-8")
    problem = read_problems()[task.task_id]

    try:
        passed, check_msg, verify_mode = verify_humaneval(
            problem, solution_code, timeout=timeout
        )
    except Exception as e:
        passed = _fallback_pytest_verify(workspace)
        check_msg = str(e)
        verify_mode = "pytest_fallback"

    if passed:
        result.success = True
        result.error = None
        result.failure_category = "success"
    else:
        result.success = False
        agent_error = result.error or "tests_failed"
        result.error = f"humaneval_check_failed ({verify_mode}): {check_msg} | agent: {agent_error}"
        result.failure_category = "humaneval_check_failed"

    if verify_mode == "full_rewrite":
        print(f"  Note: solution used full_rewrite verification (stub prefix not preserved)")

    return result


def run_humaneval_benchmark(
    limit: int | None = None,
    offset: int = 0,
    task_ids: list[str] | None = None,
    max_iterations: int = 12,
    timeout: float = 5.0,
) -> list[tuple[str, TaskResult]]:
    tasks = load_humaneval_tasks(limit=limit, offset=offset, task_ids=task_ids)
    results: list[tuple[str, TaskResult]] = []

    print(f"HumanEval: running {len(tasks)} problem(s)...")

    for i, task in enumerate(tasks, 1):
        print(f"\n--- [{i}/{len(tasks)}] {task.task_id}: {task.entry_point} ---")
        result = run_single_humaneval_task(
            task, max_iterations=max_iterations, timeout=timeout
        )
        status = "PASS" if result.success else "FAIL"
        cat = result.failure_category or "unknown"
        print(f"  {status} ({result.iterations} iter, {cat})")
        if result.error:
            print(f"  Error: {result.error[:120]}")
        results.append((task.task_id, result))

    return results


def save_humaneval_results(
    results: list[tuple[str, TaskResult]], metrics: dict
) -> Path:
    results_dir = Path(__file__).resolve().parent.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = results_dir / f"humaneval_{ts}.json"
    payload = {
        "benchmark": "humaneval",
        "metrics": metrics,
        "tasks": [
            {"task_id": tid, **{k: v for k, v in asdict(r).items()}}
            for tid, r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
