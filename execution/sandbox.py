import subprocess
import sys
from pathlib import Path


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
    return output or "(no output)"


def run_pytest(workspace: Path, test_path: str = ".", timeout: int = 60) -> str:
    """Run pytest in the workspace and return output with pass/fail summary."""
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

    output = result.stdout + result.stderr
    lower = output.lower()
    if result.returncode == 0:
        output += "\n* ALL TESTS PASSED"
    elif "failed" in lower or result.returncode != 0:
        output += "\n* SOME TESTS FAILED"
    return output
