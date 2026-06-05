import ast
import json
import subprocess
import sys
from pathlib import Path

from execution.sandbox import run_python_file, run_pytest
from agent.rag import search_codebase
from agent.filesystem import FileSystem


# ── Safety blocklist for run_command ─────────────────────────────────────────
_ALLOWED_COMMAND_PREFIXES = [
    "python", "pip", "pytest", "git",
    "ls", "dir", "cat", "head", "tail", "grep", "find", "wc",
    "echo", "pwd", "cd", "mkdir", "touch", "cp", "mv",
    "npm", "node", "make", "cargo", "go ",
    "which", "where", "env", "printenv", "type",
]

TOOL_SCHEMAS = [
    # ── File Navigation ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List all files in the workspace. Best for small repos. For large repos use explore_repo instead.",
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
            "description": "Read the full content of a file with line numbers. For large files use view_file_range instead.",
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
            "name": "read_files",
            "description": "Read multiple files at once with line numbers. Saves iterations when you need to see several files (max 5 per call).",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepaths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of relative file paths to read (max 5)",
                    },
                },
                "required": ["filepaths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file_range",
            "description": "Read specific lines from a file. Much more token-efficient than read_file for large files.",
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
            "description": "Search for exact text across all files. Returns file, line number, and matching line. Use when you know the exact function or variable name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Exact text to search for",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Semantic search over the codebase using AI embeddings. Finds code related to your query even if exact words don't match. Use when you know what the code does but not the exact name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description e.g. 'function that computes separability matrix'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_outline",
            "description": "Show all function and class names in a file with their line numbers, without showing the code bodies. Use to understand a file's structure before reading specific parts.",
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
            "name": "get_function",
            "description": "Extract a single function or class definition by name from a file. Uses AST parsing to find the exact boundaries. More precise than view_file_range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "Relative path to the Python file",
                    },
                    "name": {
                        "type": "string",
                        "description": "Function or class name to extract",
                    },
                },
                "required": ["filepath", "name"],
            },
        },
    },
    # ── Batch Tools ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_and_read",
            "description": "Search for a pattern and show surrounding lines for each match. Combines search_code + view_file_range in one call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines to show above and below each match (default: 10)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_and_verify",
            "description": "Apply a str_replace edit then immediately show the git diff. Combines str_replace + git_diff in one call.",
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
            "name": "edit_files",
            "description": "Apply multiple str_replace edits across different files in one call. Use for refactoring, renaming, or fixing the same pattern in multiple places. Shows combined git diff after all edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "filepath": {"type": "string"},
                                "old_str": {"type": "string"},
                                "new_str": {"type": "string"},
                            },
                            "required": ["filepath", "old_str", "new_str"],
                        },
                        "description": "List of edits to apply (max 5)",
                    },
                },
                "required": ["edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_and_replace_all",
            "description": "Find all occurrences of a string across all files and replace them. Use for renaming a function, variable, or import across the entire codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_str": {
                        "type": "string",
                        "description": "Exact text to find in all files",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Text to replace it with",
                    },
                },
                "required": ["old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_repo",
            "description": "Get a high-level overview of the repository — directory structure, file counts, and key files. Use as the first tool on large codebases.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ── File Editing ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or fully overwrite a file. Use only for initial implementation. For fixes use str_replace.",
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
                "Replace exact text in a file. Fails if old_str not found or appears more than once. "
                "Use this for single-file fixes. For multi-file use edit_files or search_and_replace_all."
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
            "description": "Insert lines at a specific line number. Existing lines shift down.",
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
            "description": "Delete a range of lines from a file.",
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
            "description": "Run a shell command in the workspace. Use for pip install, setup.py, etc. Dangerous commands are blocked.",
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
            "description": "Show exact changes since last commit as unified diff. Use to verify your fix before committing.",
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
            "description": "Stage all changes and commit with a message. Use after all tests pass.",
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
            "description": "Show recent commit history.",
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
    if ".git" in resolved.parts:
        raise ValueError(f"Cannot access .git directory: {filepath}")
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
    fs = FileSystem(workspace)
    files = [str(f.relative_to(workspace)) for f in fs.tracked_files()]
    return "\n".join(files) or "(empty workspace)"


def view_directory(workspace: Path, dirpath: str = ".") -> str:
    fs = FileSystem(workspace)
    items = fs.list_directory(dirpath)
    if not items:
        return f"Error: {dirpath} not found or empty"
    lines = []
    for item in items:
        indent = "  " * (item["depth"] + 1)
        suffix = "/" if item["type"] == "dir" else ""
        lines.append(f"{indent}{item['name']}{suffix}")
    return "\n".join(lines)


def find_files(workspace: Path, pattern: str) -> str:
    fs = FileSystem(workspace)
    files = [str(f.relative_to(workspace)) for f in fs.tracked_files(pattern)]
    return "\n".join(files[:50]) or f"No files found matching '{pattern}'"


def read_file(workspace: Path, filepath: str) -> str:
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
    return numbered or "(empty file)"


def read_files(workspace: Path, filepaths: list[str]) -> str:
    """Read multiple files at once (max 5). Saves iterations."""
    results = []
    for fp in filepaths[:5]:
        try:
            target = _resolve_path(workspace, fp)
        except ValueError as e:
            results.append(f"### {fp}\nError: {e}\n")
            continue
        if not target.exists():
            results.append(f"### {fp}\nError: not found\n")
            continue
        content = target.read_text(encoding="utf-8")
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        results.append(f"### {fp}\n{numbered}\n")
    return "\n".join(results)


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
    fs = FileSystem(workspace)
    results = fs.search(query)
    if not results:
        return f"No matches found for '{query}'"
    return "\n".join(f"{path}:{line}: {text}" for path, line, text in results)


def file_outline(workspace: Path, filepath: str) -> str:
    """Show function/class names + line numbers using AST. No code bodies."""
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    try:
        content = target.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError as e:
        return f"Error: cannot parse {filepath}: {e}"

    items = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            items.append((node.lineno, f"  class {node.name} (lines {node.lineno}-{end})"))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    iend = getattr(item, "end_lineno", item.lineno)
                    items.append((item.lineno, f"    def {item.name}() (lines {item.lineno}-{iend})"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Only top-level functions (not methods inside classes)
            if not any(
                isinstance(parent, ast.ClassDef)
                for parent in ast.walk(tree)
                if node in getattr(parent, "body", [])
            ):
                end = getattr(node, "end_lineno", node.lineno)
                items.append((node.lineno, f"  def {node.name}() (lines {node.lineno}-{end})"))

    items.sort(key=lambda x: x[0])
    if not items:
        return f"{filepath}: no functions or classes found"
    return f"{filepath}:\n" + "\n".join(item[1] for item in items)


def get_function(workspace: Path, filepath: str, name: str) -> str:
    """Extract a single function or class by name using AST."""
    target = _resolve_path(workspace, filepath)
    if not target.exists():
        return f"Error: {filepath} not found"
    try:
        content = target.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError as e:
        return f"Error: cannot parse {filepath}: {e}"

    lines = content.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                start = node.lineno - 1
                end = getattr(node, "end_lineno", start + 1)
                extracted = "\n".join(
                    f"{i+1:4d} | {lines[i]}" for i in range(start, end)
                )
                return f"### {filepath}: {name} (lines {start+1}-{end})\n{extracted}"

    return f"Error: '{name}' not found in {filepath}"


# ── Batch Tools ───────────────────────────────────────────────────────────────

def search_and_read(workspace: Path, query: str, context_lines: int = 10) -> str:
    fs = FileSystem(workspace)
    return fs.search_with_context(query, context_lines)


def edit_and_verify(workspace: Path, filepath: str, old_str: str, new_str: str) -> str:
    result = str_replace(workspace, filepath, old_str, new_str)
    if result.startswith("Error"):
        return result
    diff = FileSystem(workspace).git("diff")
    return f"{result}\n\n--- git diff ---\n{diff}"


def edit_files(workspace: Path, edits: list[dict]) -> str:
    results = []
    for edit in edits[:5]:
        fp = edit.get("filepath", "")
        old = edit.get("old_str", "")
        new = edit.get("new_str", "")
        result = str_replace(workspace, fp, old, new)
        results.append(f"{fp}: {result}")
    diff = FileSystem(workspace).git("diff")
    if diff and diff != "(no output)":
        results.append(f"\n--- git diff ---\n{diff}")
    return "\n".join(results)


def search_and_replace_all(workspace: Path, old_str: str, new_str: str) -> str:
    fs = FileSystem(workspace)
    replaced = []
    for filepath in fs.tracked_files():
        if not filepath.is_file():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        count = content.count(old_str)
        if count > 0:
            new_content = content.replace(old_str, new_str)
            filepath.write_text(new_content, encoding="utf-8")
            rel = str(filepath.relative_to(workspace))
            replaced.append(f"{rel}: {count} replacement(s)")
    if not replaced:
        return f"'{old_str}' not found in any file"
    diff = fs.git("diff")
    result = f"Replaced in {len(replaced)} file(s):\n" + "\n".join(replaced)
    if diff and diff != "(no output)":
        result += f"\n\n--- git diff ---\n{diff}"
    return result


def explore_repo(workspace: Path) -> str:
    fs = FileSystem(workspace)
    return fs.summary()

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
    cmd_lower = command.strip().lower()
    allowed = any(cmd_lower.startswith(prefix) for prefix in _ALLOWED_COMMAND_PREFIXES)
    if not allowed:
        return (
            f"Error: command not in allowlist. Allowed prefixes: "
            f"{', '.join(_ALLOWED_COMMAND_PREFIXES[:10])}... "
            f"Use a specific tool instead (run_code, run_tests, git_status, etc)."
        )
    try:
        result = subprocess.run(
            command, shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr
        if result.returncode != 0:
            output += f"\n* EXIT CODE: {result.returncode}"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 60s"


# ── Git Operations ────────────────────────────────────────────────────────────

def git_status(workspace: Path) -> str:
    return FileSystem(workspace).git("status")

def git_diff(workspace: Path) -> str:
    return FileSystem(workspace).git("diff") or "(no changes)"

def git_commit(workspace: Path, message: str) -> str:
    fs = FileSystem(workspace)
    fs.git("add", ".")
    return fs.git("commit", "-m", message)

def git_log(workspace: Path, n: int = 5) -> str:
    return FileSystem(workspace).git("log", "--oneline", f"-{n}")

def git_checkout_file(workspace: Path, filepath: str) -> str:
    _resolve_path(workspace, filepath)
    return FileSystem(workspace).git("checkout", "--", filepath)


# ── Dispatcher ────────────────────────────────────────────────────────────────

_TOOLS_MAP = {
    # File Navigation
    "list_files":             list_files,
    "view_directory":         view_directory,
    "find_files":             find_files,
    "read_file":              read_file,
    "read_files":             read_files,
    "view_file_range":        view_file_range,
    "search_code":            search_code,
    "search_codebase":        search_codebase,
    "file_outline":           file_outline,
    "get_function":           get_function,
    # Batch Tools
    "search_and_read":        search_and_read,
    "edit_and_verify":        edit_and_verify,
    "edit_files":             edit_files,
    "search_and_replace_all": search_and_replace_all,
    "explore_repo":           explore_repo,
    # File Editing
    "write_file":             write_file,
    "str_replace":            str_replace,
    "insert_at_line":         insert_at_line,
    "delete_lines":           delete_lines,
    "create_directory":       create_directory,
    # Execution
    "run_code":               run_code,
    "run_tests":              run_tests,
    "run_command":            run_command,
    # Git
    "git_status":             git_status,
    "git_diff":               git_diff,
    "git_commit":             git_commit,
    "git_log":                git_log,
    "git_checkout_file":      git_checkout_file,
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