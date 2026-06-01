import json
import subprocess
import sys
from pathlib import Path

from execution.sandbox import run_python_file, run_pytest

# ── Safety blocklist for run_command ─────────────────────────────────────────
_BLOCKED_COMMANDS = [
    "rm -rf", "rmdir /s", "format", "mkfs", "dd ",
    "shutdown", "reboot", "halt", "poweroff",
    "curl", "wget", "nc ", "ncat", "netcat",
    ":(){:|:&};:", "fork bomb",
]

TOOL_SCHEMAS = [
    # ── File Navigation ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files in the workspace. Use this first to understand the project structure.",
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
            "name": "view_directory",
            "description": "Show directory tree up to 2 levels deep. Use to understand repo structure before diving into files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {
                        "type": "string",
                        "description": "Relative path to directory (default: '.' for workspace root)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files matching a name pattern across the workspace. Use to locate files by name in large repos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Filename pattern e.g. '*.py', 'separable.py', 'test_*.py'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a file with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path to the file",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file_range",
            "description": "Read specific lines from a file. Much more token-efficient than read_file for large files. Prefer this when you know which lines to inspect.",
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
            "description": "Search for a text pattern across all files. Returns file, line number, and matching line. Use to locate functions, classes, or variable names.",
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
    # ── File Editing ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or fully overwrite a file. Use only for the initial implementation. For fixes, always use str_replace instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path e.g. solution.py",
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
            "name": "str_replace",
            "description": (
                "Replace an exact string in a file with new text. "
                "Fails if old_str not found or appears more than once. "
                "Always use this instead of write_file when fixing existing code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "old_str": {
                        "type": "string",
                        "description": "Exact text to find. Must appear exactly once.",
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
            "name": "insert_at_line",
            "description": "Insert one or more lines at a specific line number. Existing lines shift down. Use when you need to add code without replacing anything.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "line_number": {
                        "type": "integer",
                        "description": "Line number to insert at (1-indexed). New lines go BEFORE this line.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to insert",
                    },
                },
                "required": ["filepath", "line_number", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_lines",
            "description": "Delete a range of lines from a file. Use when you need to remove code entirely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "start_line": {
                        "type": "integer",
                        "description": "First line to delete (1-indexed)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to delete (inclusive)",
                    },
                },
                "required": ["filepath", "start_line", "end_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_directory",
            "description": "Create a new directory in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {
                        "type": "string",
                        "description": "Relative path of directory to create",
                    },
                },
                "required": ["dirpath"],
            },
        },
    },
    # ── Code Execution ────────────────────────────────────────────────────────
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
            "description": "Run pytest on a test file or directory. Always run this before finishing.",
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
            "name": "run_command",
            "description": "Run a shell command in the workspace. Use for pip install, python setup.py, or other setup tasks. Dangerous commands are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run",
                    },
                },
                "required": ["command"],
            },
        },
    },
    # ── Git Operations ────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show which files have been changed, added, or deleted since the last commit.",
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
            "name": "git_diff",
            "description": "Show the exact changes made since the last commit as a unified diff. Use to verify your fix looks correct before committing.",
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
            "name": "git_commit",
            "description": "Stage all changes and commit them with a message. Use after all tests pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message describing what was fixed",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent commit history. Use to understand the repo's recent changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of commits to show (default: 5)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_checkout_file",
            "description": "Revert a single file to its state at the last commit. Use to undo a bad edit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path to the file to revert",
                    },
                },
                "required": ["filepath"],
            },
        },
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_path(workspace: Path, filepath: str) -> Path:
    """Resolve filepath within workspace; reject path traversal."""
    resolved = (workspace / filepath).resolve()
    workspace_resolved = workspace.resolve()
    if not str(resolved).startswith(str(workspace_resolved)):
        raise ValueError(f"Path escapes workspace: {filepath}")
    return resolved


def _run_git(workspace: Path, args: list[str]) -> str:
    """Run a git command in the workspace."""
    result = subprocess.run(
        ["git"] + args,
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return output.strip() or "(no output)"


# ── File Navigation ───────────────────────────────────────────────────────────

def list_files(workspace: Path) -> str:
    files = sorted(workspace.rglob("*"))
    result = "\n".join(
        str(f.relative_to(workspace))
        for f in files
        if f.is_file() and ".git" not in f.parts
    )
    return result or "(empty workspace)"


def view_directory(workspace: Path, dirpath: str = ".") -> str:
    target = (workspace / dirpath).resolve()
    if not target.exists():
        return f"Error: {dirpath} not found"
    results = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            results.append(f"📁 {item.name}/")
            try:
                for subitem in sorted(item.iterdir()):
                    if not subitem.name.startswith("."):
                        icon = "📁" if subitem.is_dir() else "📄"
                        results.append(f"   {icon} {subitem.name}")
            except PermissionError:
                pass
        else:
            results.append(f"📄 {item.name}")
    return "\n".join(results[:100]) or "(empty directory)"


def find_files(workspace: Path, pattern: str) -> str:
    results = []
    for f in sorted(workspace.rglob(pattern)):
        if f.is_file() and ".git" not in f.parts:
            results.append(str(f.relative_to(workspace)))
    return "\n".join(results[:50]) or f"No files found matching '{pattern}'"


def read_file(workspace: Path, filepath: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
    return numbered or "(empty file)"


def view_file_range(
    workspace: Path, filepath: str, start_line: int, end_line: int
) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    lines = target.read_text(encoding="utf-8").splitlines()
    selected = lines[start_line - 1: end_line]
    if not selected:
        return f"Error: no lines in range {start_line}-{end_line}"
    return "\n".join(
        f"{i+start_line:4d} | {line}" for i, line in enumerate(selected)
    )


def search_code(workspace: Path, query: str) -> str:
    results = []
    for filepath in sorted(workspace.rglob("*")):
        if not filepath.is_file() or ".git" in filepath.parts:
            continue
        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if query.lower() in line.lower():
                rel = filepath.relative_to(workspace)
                results.append(f"{rel}:{i}: {line.rstrip()}")
    return "\n".join(results[:100]) if results else f"No matches found for '{query}'"


# ── File Editing ──────────────────────────────────────────────────────────────

def write_file(workspace: Path, filepath: str, content: str) -> str:
    target = _resolve_path(workspace, filepath)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {filepath}"


def str_replace(workspace: Path, filepath: str, old_str: str, new_str: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    content = target.read_text(encoding="utf-8")
    count = content.count(old_str)
    if count == 0:
        return f"Error: old_str not found in {filepath}"
    if count > 1:
        return f"Error: old_str appears {count} times in {filepath} — be more specific"
    new_content = content.replace(old_str, new_str, 1)
    target.write_text(new_content, encoding="utf-8")
    return f"Successfully replaced in {filepath}"


def insert_at_line(workspace: Path, filepath: str, line_number: int, content: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    insert_idx = max(0, min(line_number - 1, len(lines)))
    new_lines = content if content.endswith("\n") else content + "\n"
    lines.insert(insert_idx, new_lines)
    target.write_text("".join(lines), encoding="utf-8")
    return f"Inserted at line {line_number} in {filepath}"


def delete_lines(workspace: Path, filepath: str, start_line: int, end_line: int) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    if start_line < 1 or end_line > len(lines):
        return f"Error: line range {start_line}-{end_line} out of bounds (file has {len(lines)} lines)"
    deleted = end_line - start_line + 1
    lines = lines[:start_line - 1] + lines[end_line:]
    target.write_text("".join(lines), encoding="utf-8")
    return f"Deleted {deleted} lines ({start_line}-{end_line}) from {filepath}"


def create_directory(workspace: Path, dirpath: str) -> str:
    target = _resolve_path(workspace, dirpath)
    target.mkdir(parents=True, exist_ok=True)
    return f"Created directory {dirpath}"


# ── Code Execution ────────────────────────────────────────────────────────────

def run_code(workspace: Path, filepath: str) -> str:
    _resolve_path(workspace, filepath)
    return run_python_file(workspace, filepath)


def run_tests(workspace: Path, test_path: str = "test_solution.py") -> str:
    return run_pytest(workspace, test_path)


def run_command(workspace: Path, command: str) -> str:
    # Safety check
    for blocked in _BLOCKED_COMMANDS:
        if blocked.lower() in command.lower():
            return f"Error: command blocked for safety: '{blocked}'"
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            output += f"\n* EXIT CODE: {result.returncode}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60s"


# ── Git Operations ────────────────────────────────────────────────────────────

def git_status(workspace: Path) -> str:
    return _run_git(workspace, ["status"])


def git_diff(workspace: Path) -> str:
    result = _run_git(workspace, ["diff"])
    return result or "(no changes since last commit)"


def git_commit(workspace: Path, message: str) -> str:
    _run_git(workspace, ["add", "."])
    return _run_git(workspace, ["commit", "-m", message])


def git_log(workspace: Path, n: int = 5) -> str:
    return _run_git(workspace, ["log", f"--oneline", f"-{n}"])


def git_checkout_file(workspace: Path, filepath: str) -> str:
    _resolve_path(workspace, filepath)
    return _run_git(workspace, ["checkout", "--", filepath])


# ── Dispatcher ────────────────────────────────────────────────────────────────

_TOOLS_MAP = {
    # File Navigation
    "list_files":       list_files,
    "view_directory":   view_directory,
    "find_files":       find_files,
    "read_file":        read_file,
    "view_file_range":  view_file_range,
    "search_code":      search_code,
    # File Editing
    "write_file":       write_file,
    "str_replace":      str_replace,
    "insert_at_line":   insert_at_line,
    "delete_lines":     delete_lines,
    "create_directory": create_directory,
    # Execution
    "run_code":         run_code,
    "run_tests":        run_tests,
    "run_command":      run_command,
    # Git
    "git_status":       git_status,
    "git_diff":         git_diff,
    "git_commit":       git_commit,
    "git_log":          git_log,
    "git_checkout_file": git_checkout_file,
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