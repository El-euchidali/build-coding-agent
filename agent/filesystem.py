"""
FileSystem — the foundation layer for all file operations.

Every tool that touches files goes through this class.
Fix it once, fixed everywhere.

Respects .gitignore automatically via git ls-files.
Falls back to manual traversal with skip list for non-git repos.
"""

import subprocess
from pathlib import Path


# Directories to skip when not using git
_SKIP_DIRS = frozenset({
    ".git", "venv", "venv_wsl", ".venv", "__pycache__", "node_modules",
    ".tox", ".eggs", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".ruff_cache", ".coverage", "htmlcov", "egg-info",
})


class FileSystem:
    """Safe file system access for a workspace directory."""

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self._is_git = (workspace / ".git").exists()

    # ── Path safety ───────────────────────────────────────────────────────

    def resolve(self, filepath: str) -> Path:
        """Resolve a relative path within workspace. Rejects path traversal."""
        resolved = (self.workspace / filepath).resolve()
        if not str(resolved).startswith(str(self.workspace)):
            raise ValueError(f"Path escapes workspace: {filepath}")
        return resolved

    # ── File listing ──────────────────────────────────────────────────────

    def tracked_files(self, pattern: str = "*") -> list[Path]:
        """
        List all files the agent should see.
        Uses git ls-files if available — automatically respects .gitignore.
        Falls back to manual walk with skip list.
        """
        if self._is_git:
            files = self._git_ls_files()
            if files is not None:
                if pattern != "*":
                    files = [f for f in files if f.match(pattern)]
                return sorted(files)
        return self._manual_walk(pattern)

    def _git_ls_files(self) -> list[Path] | None:
        """Use git to list tracked + untracked-but-not-ignored files."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return [self.workspace / line for line in result.stdout.strip().splitlines()]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None

    def _manual_walk(self, pattern: str = "*") -> list[Path]:
        """Fallback file listing — skips known junk directories."""
        results = []
        try:
            for f in self.workspace.rglob(pattern):
                if _SKIP_DIRS.intersection(f.parts):
                    continue
                if f.is_file():
                    results.append(f)
        except OSError:
            pass
        return sorted(results)

    # ── Directory listing ─────────────────────────────────────────────────

    def list_directory(self, dirpath: str = ".", depth: int = 2) -> list[dict]:
        """
        List directory contents safely.
        Returns list of {name, type, path} dicts.
        """
        target = self.resolve(dirpath) if dirpath != "." else self.workspace
        if not target.exists():
            return []

        items = []
        self._walk_dir(target, items, depth, 0)
        return items

    def _walk_dir(self, path: Path, items: list, max_depth: int, current_depth: int):
        """Recursive directory walker with depth limit."""
        if current_depth >= max_depth:
            return
        try:
            for item in sorted(path.iterdir()):
                if item.name.startswith(".") or item.name in _SKIP_DIRS:
                    continue
                rel = str(item.relative_to(self.workspace))
                if item.is_dir():
                    items.append({"name": item.name, "type": "dir", "path": rel, "depth": current_depth})
                    self._walk_dir(item, items, max_depth, current_depth + 1)
                else:
                    items.append({"name": item.name, "type": "file", "path": rel, "depth": current_depth})
                if len(items) >= 200:
                    return
        except (PermissionError, OSError):
            pass

    # ── File reading ──────────────────────────────────────────────────────

    def read(self, filepath: str) -> str:
        """Read a file with line numbers."""
        target = self.resolve(filepath)
        if not target.exists():
            return f"Error: {filepath} not found"
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: {filepath} is not a text file"
        except OSError as e:
            return f"Error reading {filepath}: {e}"
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return numbered or "(empty file)"

    def read_range(self, filepath: str, start: int, end: int) -> str:
        """Read specific lines from a file."""
        target = self.resolve(filepath)
        if not target.exists():
            return f"Error: {filepath} not found"
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError) as e:
            return f"Error reading {filepath}: {e}"
        selected = lines[start - 1: end]
        if not selected:
            return f"Error: no lines in range {start}-{end}"
        return "\n".join(f"{i+start:4d} | {line}" for i, line in enumerate(selected))

    def read_raw(self, filepath: str) -> str:
        """Read raw file content without line numbers."""
        target = self.resolve(filepath)
        if not target.exists():
            return f"Error: {filepath} not found"
        try:
            return target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            return f"Error reading {filepath}: {e}"

    # ── File writing ──────────────────────────────────────────────────────

    def write(self, filepath: str, content: str) -> str:
        """Write content to a file. Creates parent directories."""
        target = self.resolve(filepath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {filepath}"

    def replace(self, filepath: str, old_str: str, new_str: str) -> str:
        """Replace exact text in a file. Fails if ambiguous."""
        target = self.resolve(filepath)
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

    def insert_at(self, filepath: str, line_number: int, content: str) -> str:
        """Insert text at a specific line number."""
        target = self.resolve(filepath)
        if not target.exists():
            return f"Error: {filepath} not found"
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        idx = max(0, min(line_number - 1, len(lines)))
        new_lines = content if content.endswith("\n") else content + "\n"
        lines.insert(idx, new_lines)
        target.write_text("".join(lines), encoding="utf-8")
        return f"Inserted at line {line_number} in {filepath}"

    def delete_range(self, filepath: str, start: int, end: int) -> str:
        """Delete a range of lines."""
        target = self.resolve(filepath)
        if not target.exists():
            return f"Error: {filepath} not found"
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        if start < 1 or end > len(lines):
            return f"Error: range {start}-{end} out of bounds (file has {len(lines)} lines)"
        deleted = end - start + 1
        lines = lines[:start - 1] + lines[end:]
        target.write_text("".join(lines), encoding="utf-8")
        return f"Deleted {deleted} lines ({start}-{end}) from {filepath}"

    def mkdir(self, dirpath: str) -> str:
        """Create a directory."""
        target = self.resolve(dirpath)
        target.mkdir(parents=True, exist_ok=True)
        return f"Created directory {dirpath}"

    # ── Search ────────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 100) -> list[tuple[str, int, str]]:
        """
        Search for text across all tracked files.
        Returns list of (relative_path, line_number, line_text).
        """
        results = []
        for filepath in self.tracked_files():
            if not filepath.is_file():
                continue
            try:
                lines = filepath.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(lines, 1):
                if query.lower() in line.lower():
                    rel = str(filepath.relative_to(self.workspace))
                    results.append((rel, i, line.rstrip()))
                    if len(results) >= max_results:
                        return results
        return results

    def search_with_context(self, query: str, context_lines: int = 10, max_results: int = 5) -> str:
        """Search and show surrounding lines for each match."""
        results = []
        for filepath in self.tracked_files():
            if not filepath.is_file():
                continue
            try:
                lines = filepath.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(lines):
                if query.lower() in line.lower():
                    rel = str(filepath.relative_to(self.workspace))
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    context = "\n".join(f"{j+1:4d} | {lines[j]}" for j in range(start, end))
                    results.append(f"### {rel}:{i+1}\n{context}\n")
                    if len(results) >= max_results:
                        return "\n".join(results)
            if len(results) >= max_results:
                break
        return "\n".join(results) if results else f"No matches found for '{query}'"

    # ── Git operations ────────────────────────────────────────────────────

    def git(self, *args: str) -> str:
        """Run a git command in the workspace."""
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            return output or "(no output)"
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"Error: {e}"

    # ── Info ──────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """High-level repo summary — file counts and directory structure."""
        files = self.tracked_files()
        file_count = len(files)
        py_count = sum(1 for f in files if f.suffix == ".py")

        dirs = []
        root_files = []
        try:
            for item in sorted(self.workspace.iterdir()):
                if item.name.startswith(".") or item.name in _SKIP_DIRS:
                    continue
                if item.is_dir():
                    sub_py = sum(1 for f in self.tracked_files("*.py")
                                 if str(f).startswith(str(item)))
                    dirs.append(f"  {item.name}/ ({sub_py} .py files)")
                else:
                    root_files.append(f"  {item.name}")
        except OSError:
            pass

        output = f"Repository: {file_count} files total, {py_count} Python files\n\n"
        if dirs:
            output += "Directories:\n" + "\n".join(dirs[:20]) + "\n"
        if root_files:
            output += "\nRoot files:\n" + "\n".join(root_files[:10]) + "\n"
        return output