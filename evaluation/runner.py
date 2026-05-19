import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from agent.agent import TaskResult, run_task
from execution.sandbox import run_pytest

BENCHMARK_PATH = Path(__file__).parent / "tasks" / "benchmark.json"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent / "workspace"


@dataclass
class Task:
    id: str
    description: str
    entry_point: str
    solution_stub: str
    test_content: str
    test_file: str = "test_solution.py"


def load_tasks(path: Path | None = None) -> list[Task]:
    """Load benchmark tasks from JSON."""
    path = path or BENCHMARK_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Task(
            id=item["id"],
            description=item["description"],
            entry_point=item["entry_point"],
            solution_stub=item["solution_stub"],
            test_content=item["test_content"],
            test_file=item.get("test_file", "test_solution.py"),
        )
        for item in raw
    ]


def _seed_workspace(task: Task, workspace: Path) -> None:
    """Create fresh workspace with stub solution and tests."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "solution.py").write_text(task.solution_stub, encoding="utf-8")
    (workspace / task.test_file).write_text(task.test_content, encoding="utf-8")


def verify_solution(workspace: Path, test_file: str = "test_solution.py") -> bool:
    """Run pytest directly to verify the final solution."""
    output = run_pytest(workspace, test_file)
    return "* ALL TESTS PASSED" in output


def run_single_task(task: Task, max_iterations: int = 10) -> TaskResult:
    """Run the agent on one benchmark task."""
    workspace = WORKSPACE_ROOT / task.id
    _seed_workspace(task, workspace)

    result = run_task(
        description=task.description,
        workspace=workspace,
        max_iterations=max_iterations,
    )

    if verify_solution(workspace, task.test_file):
        result.success = True
        result.error = None
    elif not result.success and result.error is None:
        result.error = "tests_failed"

    return result


def run_benchmark(
    tasks: list[Task] | None = None,
    limit: int | None = None,
    max_iterations: int = 10,
) -> list[tuple[str, TaskResult]]:
    """Run the agent on all (or limited) benchmark tasks."""
    tasks = tasks or load_tasks()
    if limit is not None:
        tasks = tasks[:limit]

    results: list[tuple[str, TaskResult]] = []
    for task in tasks:
        print(f"\n--- Task {task.id}: {task.entry_point} ---")
        result = run_single_task(task, max_iterations=max_iterations)
        status = "PASS" if result.success else "FAIL"
        print(f"  {status} ({result.iterations} iterations)")
        if result.error:
            print(f"  Error: {result.error}")
        results.append((task.id, result))
    return results
