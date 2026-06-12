"""
Persistent conversation history shared by CLI and web UI.

Storage layout (per workspace, gitignored):
  .agent_history/
    index.json
    conversations/<id>.json
    .legacy_migrated          # marker after importing last_conversation.json
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

TRANSCRIPT_RESULT_CAP = 800


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def history_dir(workspace: Path) -> Path:
    return workspace.resolve() / ".agent_history"


def conversations_dir(workspace: Path) -> Path:
    return history_dir(workspace) / "conversations"


def _index_path(workspace: Path) -> Path:
    return history_dir(workspace) / "index.json"


def _conv_path(workspace: Path, conv_id: str) -> Path:
    return conversations_dir(workspace) / f"{conv_id}.json"


def _read_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def title_from_message(message: str, max_len: int = 50) -> str:
    text = " ".join(message.strip().split())
    if not text:
        return "New chat"
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def extract_turn_messages(messages: list[dict]) -> list[dict]:
    """Return conversation turns only (no system prompt or scratchpad injection)."""
    return [
        m
        for m in messages
        if m.get("role") != "system"
        and not m.get("content", "").startswith("## Your Scratchpad Notes")
    ]


def sanitize_transcript_event(event: dict) -> dict:
    """Prepare an SSE event for disk storage."""
    out = dict(event)
    if out.get("type") == "tool_call" and "result" in out:
        result = out["result"]
        if isinstance(result, str) and len(result) > TRANSCRIPT_RESULT_CAP:
            out["result"] = result[:TRANSCRIPT_RESULT_CAP]
    return out


def list_conversations(workspace: Path) -> list[dict]:
    """List conversation metadata, most recently updated first."""
    raw = _read_json(_index_path(workspace))
    if not isinstance(raw, list):
        return []
    return sorted(raw, key=lambda e: e.get("updated_at", ""), reverse=True)


def load_conversation(workspace: Path, conv_id: str) -> dict | None:
    data = _read_json(_conv_path(workspace, conv_id))
    return data if isinstance(data, dict) else None


def create_conversation(workspace: Path, title: str = "New chat") -> dict:
    """Create a new empty conversation (not added to index until first save)."""
    conv_id = uuid.uuid4().hex
    now = _now_iso()
    conv = {
        "id": conv_id,
        "title": title,
        "workspace": str(workspace.resolve()),
        "created_at": now,
        "updated_at": now,
        "messages": [],
        "transcript": [],
    }
    _write_json(_conv_path(workspace, conv_id), conv)
    return conv


def _upsert_index(workspace: Path, conv: dict) -> None:
    index = _read_json(_index_path(workspace))
    if not isinstance(index, list):
        index = []
    meta = {
        "id": conv["id"],
        "title": conv.get("title", "New chat"),
        "created_at": conv.get("created_at", _now_iso()),
        "updated_at": conv.get("updated_at", _now_iso()),
    }
    index = [e for e in index if e.get("id") != conv["id"]]
    index.append(meta)
    index.sort(key=lambda e: e.get("updated_at", ""), reverse=True)
    _write_json(_index_path(workspace), index)


def save_conversation(workspace: Path, conv: dict) -> None:
    """Persist conversation; adds to index once it has messages or transcript."""
    has_content = bool(conv.get("messages")) or bool(conv.get("transcript"))
    if not has_content:
        return
    conv["updated_at"] = _now_iso()
    _write_json(_conv_path(workspace, conv["id"]), conv)
    _upsert_index(workspace, conv)


def delete_conversation(workspace: Path, conv_id: str) -> bool:
    path = _conv_path(workspace, conv_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            return False
    index = _read_json(_index_path(workspace))
    if isinstance(index, list):
        index = [e for e in index if e.get("id") != conv_id]
        _write_json(_index_path(workspace), index)
    return True


def get_most_recent_conversation(workspace: Path) -> dict | None:
    convs = list_conversations(workspace)
    if not convs:
        return None
    return load_conversation(workspace, convs[0]["id"])


def migrate_legacy(workspace: Path) -> str | None:
    """
    Import legacy last_conversation.json into the new format once.
    Returns the new conversation id, or None if nothing to migrate.
    """
    root = history_dir(workspace)
    marker = root / ".legacy_migrated"
    if marker.exists():
        return None

    root.mkdir(parents=True, exist_ok=True)
    legacy_path = root / "last_conversation.json"
    if not legacy_path.exists():
        marker.write_text(_now_iso(), encoding="utf-8")
        return None

    raw = _read_json(legacy_path)
    if not isinstance(raw, list) or not raw:
        marker.write_text(_now_iso(), encoding="utf-8")
        return None

    # Legacy format: user/assistant only, truncated content.
    messages = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in raw
        if m.get("role") in ("user", "assistant")
    ]
    if not messages:
        marker.write_text(_now_iso(), encoding="utf-8")
        return None

    first_user = next((m["content"] for m in messages if m["role"] == "user"), "")
    conv = create_conversation(workspace, title=title_from_message(first_user))
    conv["messages"] = messages
    conv["transcript"] = [
        {"type": "user", "content": m["content"]}
        if m["role"] == "user"
        else {"type": "response", "content": m["content"]}
        for m in messages
        if m["role"] in ("user", "assistant")
    ]
    save_conversation(workspace, conv)
    marker.write_text(_now_iso(), encoding="utf-8")
    return conv["id"]
