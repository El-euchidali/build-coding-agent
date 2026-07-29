"""Workspace session management for the IDE UI."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from agent.agent import init_conversation, warm_rag_index
from agent.filesystem import FileSystem
from agent.history import create_conversation, migrate_legacy


@dataclass
class WorkspaceSession:
    session_id: str
    workspace: Path
    conversation_id: str
    messages: list[dict]
    transcript: list[dict] = field(default_factory=list)
    rag_info: str | None = None
    stopped: bool = False


_browser_sessions: dict[str, WorkspaceSession] = {}
_chat_sessions: dict[str, dict] = {}
_persist_lock = threading.Lock()

# Captured at import so a later os.chdir (or a reload worker) cannot move it.
LAUNCH_WORKSPACE = Path.cwd().resolve()


def _sessions_store_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    store_dir = root / ".agent_history"
    store_dir.mkdir(parents=True, exist_ok=True)
    return store_dir / "ui_browser_sessions.json"


def _persist_browser_sessions() -> None:
    payload = {
        sid: {
            "workspace": str(session.workspace),
            "conversation_id": session.conversation_id,
        }
        for sid, session in _browser_sessions.items()
    }
    path = _sessions_store_path()
    with _persist_lock:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_browser_sessions() -> None:
    path = _sessions_store_path()
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(payload, dict):
        return
    for session_id, data in payload.items():
        if session_id in _browser_sessions or not isinstance(data, dict):
            continue
        workspace_raw = data.get("workspace")
        if not workspace_raw:
            continue
        workspace = Path(workspace_raw)
        if not workspace.exists():
            continue
        _browser_sessions[session_id] = WorkspaceSession(
            session_id=session_id,
            workspace=workspace.resolve(),
            conversation_id=str(data.get("conversation_id", "")),
            messages=[],
        )


def _allowed_roots() -> list[Path]:
    raw = os.environ.get("ALLOWED_WORKSPACE_ROOTS", "").strip()
    if not raw:
        return []
    return [Path(r).expanduser().resolve() for r in raw.split(",") if r.strip()]


def _is_under_allowed_roots(path: Path) -> bool:
    roots = _allowed_roots()
    if not roots:
        return True
    resolved = path.resolve()
    return any(
        str(resolved).startswith(str(root)) or str(root).startswith(str(resolved))
        for root in roots
    )


def get_default_workspace() -> Path:
    """The directory the server was launched from — opened automatically on load."""
    roots = _allowed_roots()
    if roots and not _is_under_allowed_roots(LAUNCH_WORKSPACE):
        return roots[0]
    return LAUNCH_WORKSPACE


def get_browse_roots() -> list[dict]:
    """Return starting locations for the folder picker."""
    roots = _allowed_roots()
    if roots:
        return [{"name": r.name or str(r), "path": str(r)} for r in roots if r.is_dir()]

    seen: set[str] = set()
    items: list[dict] = []
    for label, p in [("Home", Path.home()), ("Current", LAUNCH_WORKSPACE)]:
        resolved = p.expanduser().resolve()
        key = str(resolved)
        if key not in seen and resolved.is_dir():
            seen.add(key)
            items.append({"name": label, "path": key})
    return items


def browse_directory(raw: str | None) -> dict:
    """List subdirectories at raw path, or roots when raw is empty."""
    if not raw:
        entries = get_browse_roots()
        return {"path": None, "parent": None, "entries": entries, "roots": True}

    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Not a directory: {path}")
    if not _is_under_allowed_roots(path):
        raise ValueError(f"Path not allowed: {path}")

    parent_path = path.parent
    parent = str(parent_path.resolve()) if parent_path != path else None
    if parent and not _is_under_allowed_roots(parent_path):
        parent = None

    entries: list[dict] = []
    try:
        for entry in sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            if not entry.is_dir():
                continue
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if not resolved.is_dir() or not _is_under_allowed_roots(resolved):
                continue
            entries.append({"name": entry.name, "path": str(resolved)})
    except PermissionError as e:
        raise ValueError(f"Permission denied: {path}") from e

    return {
        "path": str(path),
        "parent": parent,
        "entries": entries,
        "roots": False,
    }


def validate_workspace(raw: str, create: bool = True) -> Path:
    """Resolve and validate a workspace path, optionally creating it."""
    p = Path(raw).expanduser().resolve()

    if not p.exists():
        if create:
            p.mkdir(parents=True, exist_ok=True)
        else:
            raise ValueError(f"Directory does not exist: {p}")
    elif not p.is_dir():
        raise ValueError(f"Not a directory: {p}")

    allowed_roots = os.environ.get("ALLOWED_WORKSPACE_ROOTS", "").strip()
    if allowed_roots:
        roots = [Path(r).expanduser().resolve() for r in allowed_roots.split(",") if r.strip()]
        if not any(str(p).startswith(str(root)) for root in roots):
            raise ValueError(f"Path not allowed: {p}")

    return p


def build_tree(fs: FileSystem, max_depth: int = 4) -> list[dict]:
    """Build a nested file tree from tracked files."""
    root: dict[str, dict] = {}

    for fpath in fs.tracked_files():
        rel = str(fpath.relative_to(fs.workspace)).replace("\\", "/")
        parts = rel.split("/")
        node = root
        for i, part in enumerate(parts):
            is_file = i == len(parts) - 1
            if part not in node:
                node[part] = {
                    "name": part,
                    "path": "/".join(parts[: i + 1]),
                    "is_dir": not is_file,
                    "children": {} if not is_file else None,
                }
            entry = node[part]
            if not is_file:
                node = entry["children"]

    def to_list(d: dict[str, dict], depth: int = 0) -> list[dict]:
        items: list[dict] = []
        for name in sorted(d.keys(), key=lambda n: (not d[n]["is_dir"], n.lower())):
            entry = d[name]
            item: dict = {
                "name": entry["name"],
                "path": entry["path"],
                "is_dir": entry["is_dir"],
            }
            if entry["is_dir"] and entry["children"] is not None:
                if depth < max_depth:
                    item["children"] = to_list(entry["children"], depth + 1)
                else:
                    item["children"] = []
            items.append(item)
        return items

    return to_list(root)


def list_tree_children(fs: FileSystem, rel_path: str = "") -> list[dict]:
    """Return immediate children under rel_path for lazy tree expansion."""
    rel_path = rel_path.strip("/")
    prefix = f"{rel_path}/" if rel_path else ""
    children: dict[str, dict] = {}

    for fpath in fs.tracked_files():
        rel = str(fpath.relative_to(fs.workspace)).replace("\\", "/")
        if rel_path and not rel.startswith(prefix):
            continue
        remainder = rel[len(prefix) :] if prefix else rel
        if not remainder:
            continue
        parts = remainder.split("/")
        name = parts[0]
        is_file = len(parts) == 1
        child_path = f"{rel_path}/{name}".lstrip("/") if rel_path else name
        if name not in children:
            children[name] = {
                "name": name,
                "path": child_path,
                "is_dir": not is_file,
            }

    return sorted(
        children.values(),
        key=lambda e: (not e["is_dir"], e["name"].lower()),
    )


def open_workspace(session_id: str, raw_path: str | None = None) -> WorkspaceSession:
    """Initialize harness session for a workspace path, or the launch directory."""
    if raw_path:
        workspace = validate_workspace(raw_path)
    else:
        workspace = validate_workspace(str(get_default_workspace()), create=False)
    migrate_legacy(workspace)

    conv = create_conversation(workspace)
    messages = init_conversation(workspace, build_rag=False)

    fs = FileSystem(workspace)
    py_count = sum(1 for f in fs.tracked_files("*.py"))
    rag_info = f"Indexed {py_count} Python files" if py_count > 3 else None

    session = WorkspaceSession(
        session_id=session_id,
        workspace=workspace,
        conversation_id=conv["id"],
        messages=messages,
        transcript=[],
        rag_info=rag_info,
    )
    _browser_sessions[session_id] = session

    _chat_sessions[conv["id"]] = {
        "messages": messages,
        "workspace": str(workspace),
        "conversation_id": conv["id"],
        "transcript": [],
        "rag_info": rag_info,
        "stopped": False,
    }

    threading.Thread(
        target=warm_rag_index,
        args=(workspace,),
        daemon=True,
        name="rag-warm",
    ).start()

    _persist_browser_sessions()
    return session


def get_browser_session(session_id: str) -> WorkspaceSession | None:
    if session_id not in _browser_sessions:
        _load_browser_sessions()
    return _browser_sessions.get(session_id)


def get_session_workspace(session_id: str) -> Path | None:
    session = get_browser_session(session_id)
    if session:
        return session.workspace
    return None


def get_chat_session(conversation_id: str) -> dict | None:
    return _chat_sessions.get(conversation_id)


def register_chat_session(conversation_id: str, session: dict) -> None:
    _chat_sessions[conversation_id] = session


def remove_chat_session(conversation_id: str) -> None:
    _chat_sessions.pop(conversation_id, None)
