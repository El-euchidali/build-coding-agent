import json
from pathlib import Path

from execution.sandbox import run_python_file, run_pytest

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the task workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path, e.g. solution.py",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write",
                    },
                },
                "required": ["filepath", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": "Run a Python file and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path to the Python file",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run pytest on tests in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "test_path": {
                        "type": "string",
                        "description": "Path to test file or directory (default: test_solution.py)",
                    },
                },
                "required": [],
            },
        },
    },
]


def _resolve_path(workspace: Path, filepath: str) -> Path:
    """Resolve filepath within workspace; reject path traversal."""
    resolved = (workspace / filepath).resolve()
    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ValueError(f"Path escapes workspace: {filepath}")
    return resolved


def write_file(workspace: Path, filepath: str, content: str) -> str:
    target = _resolve_path(workspace, filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {filepath}"


def run_code(workspace: Path, filepath: str) -> str:
    _resolve_path(workspace, filepath)
    return run_python_file(workspace, filepath)


def run_tests(workspace: Path, test_path: str = "test_solution.py") -> str:
    return run_pytest(workspace, test_path)


_TOOLS_MAP = {
    "write_file": write_file,
    "run_code": run_code,
    "run_tests": run_tests,
}


def execute_tool(name: str, args: dict, workspace: Path) -> str:
    """Route tool name to implementation and run it."""
    if name not in _TOOLS_MAP:
        return f"Error: unknown tool '{name}'"
    try:
        return _TOOLS_MAP[name](workspace, **args)
    except TypeError as e:
        return f"Error: invalid arguments for {name}: {e}"
    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error executing {name}: {e}"


def parse_tool_call_fallback(content: str | None) -> list[dict] | None:
    """Parse JSON tool calls from plain text if model does not use native tool_calls."""
    if not content:
        return None
    text = content.strip()
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip() if end > start else text
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip() if end > start else text
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and "name" in data:
        return [{"name": data["name"], "arguments": data.get("args", data.get("arguments", {}))}]
    if isinstance(data, list):
        return [
            {"name": item["name"], "arguments": item.get("args", item.get("arguments", {}))}
            for item in data
            if isinstance(item, dict) and "name" in item
        ]
    return None
