import subprocess
import sys
import re
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
        # Timeout escalation — retry with doubled timeout
        try:
            result = subprocess.run(
                args, cwd=workspace, capture_output=True, text=True, timeout=timeout * 2,
            )
        except subprocess.TimeoutExpired:
            return f"[TEST_RESULT:ERROR] passed=0 failed=0 errors=0\nError: pytest timed out after {timeout * 2}s"

    raw = result.stdout + result.stderr
    output = format_pytest_output(raw)

    # Parse counts from raw output (e.g., "5 passed, 1 failed, 1 errored in 0.12s")
    passed = 0
    failed = 0
    errors = 0
    
    # Pytest summary line usually looks like: "== 5 passed, 1 failed in 0.12s =="
    summary_match = re.search(r"(\d+)\s+passed(?:,)?\s*(\d+)?\s*failed(?:,)?\s*(\d+)?\s*errored", raw)
    if summary_match:
        passed = int(summary_match.group(1) or 0)
        failed = int(summary_match.group(2) or 0)
        errors = int(summary_match.group(3) or 0)
    else:
        # Fallback for different pytest versions or output formats
        p_match = re.search(r"(\d+)\s+passed", raw)
        f_match = re.search(r"(\d+)\s+failed", raw)
        e_match = re.search(r"(\d+)\s+errored", raw)
        passed = int(p_match.group(1)) if p_match else 0
        failed = int(f_match.group(1)) if f_match else 0
        errors = int(e_match.group(1)) if e_match else 0

    status = "PASS" if result.returncode == 0 else "FAIL"
    prefix = f"[TEST_RESULT:{status}] passed={passed} failed={failed} errors={errors}\n"
    
    return prefix + output
