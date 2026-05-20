import subprocess
import sys
from pathlib import Path

from execution.output_format import format_pytest_output, truncate_lines


def run_python_file(
    workspace: Path, filepath: str, timeout: int = 30
) -> str:
    """Run a Python file in the workspace and return combined output."""
    target = workspace / filepath
    if not target.exists():
        return f"Error: {filepath} not found in workspace"

    try:
        result = subprocess.run(
            [sys.executable, filepath],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: execution timed out after {timeout}s"

    output = result.stdout + result.stderr
    if result.returncode != 0:
        output += f"\n* EXIT CODE: {result.returncode}"
    return truncate_lines(output or "(no output)", max_lines=30)


def run_pytest(workspace: Path, test_path: str = ".", timeout: int = 60) -> str:
    """Run pytest in the workspace and return formatted output with pass/fail summary."""
    args = [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"]
    try:
        result = subprocess.run(
            args,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: pytest timed out after {timeout}s"

    raw = result.stdout + result.stderr
    output = format_pytest_output(raw)
    if result.returncode == 0:
        output += "\n* ALL TESTS PASSED"
    else:
        output += "\n* SOME TESTS FAILED"
    return output
