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
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the content of a file in the workspace with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path to the file to read",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "Replace an exact string in a file with new text. "
                "Fails if old_str is not found or appears more than once. "
                "Always prefer this over write_file when fixing existing code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "old_str": {
                        "type": "string",
                        "description": "Exact text to find. Must appear exactly once in the file.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Text to replace it with.",
                    },
                },
                "required": ["filepath", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files currently in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file_range",
            "description": (
                "Read only specific lines from a file. "
                "More token-efficient than read_file for large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-indexed)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (inclusive)",
                    },
                },
                "required": ["filepath", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a text pattern across all files in the workspace. Returns file name, line number, and matching line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for",
                    },
                },
                "required": ["query"],
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

def read_file(workspace: Path, filepath: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
    return numbered or "(empty file)"


def str_replace(workspace: Path, filepath: str, old_str: str, new_str: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    content = target.read_text(encoding="utf-8")
    count = content.count(old_str)
    if count == 0:
        return f"Error: old_str not found in {filepath}"
    if count > 1:
        return (
            f"Error: old_str appears {count} times in {filepath} — be more specific"
        )
    new_content = content.replace(old_str, new_str, 1)
    target.write_text(new_content, encoding="utf-8")
    return f"Successfully replaced in {filepath}"


def list_files(workspace: Path) -> str:
    files = sorted(workspace.rglob("*"))
    result = "\n".join(
        str(f.relative_to(workspace))
        for f in files
        if f.is_file()
    )
    return result or "(empty workspace)"


def view_file_range(
    workspace: Path, filepath: str, start_line: int, end_line: int
) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    lines = target.read_text(encoding="utf-8").splitlines()
    selected = lines[start_line - 1 : end_line]
    if not selected:
        return f"Error: no lines in range {start_line}-{end_line}"
    return "\n".join(
        f"{i+start_line:4d} | {line}" for i, line in enumerate(selected)
    )


def search_code(workspace: Path, query: str) -> str:
    results = []
    for filepath in sorted(workspace.rglob("*")):
        if not filepath.is_file():
            continue
        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if query.lower() in line.lower():
                rel = filepath.relative_to(workspace)
                results.append(f"{rel}:{i}: {line.rstrip()}")
    return "\n".join(results) if results else f"No matches found for '{query}'"


_TOOLS_MAP = {
    "write_file":      write_file,
    "read_file":       read_file,
    "str_replace":     str_replace,
    "list_files":      list_files,
    "view_file_range": view_file_range,
    "search_code":     search_code,
    "run_code":        run_code,
    "run_tests":       run_tests,
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
