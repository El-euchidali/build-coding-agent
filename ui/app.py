"""
Conversational coding agent UI.
Run: python -m ui.app
Open: http://localhost:8000
"""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from agent.agent import init_conversation, handle_message_streaming
from agent.filesystem import FileSystem
from agent.history import (
    _now_iso,
    create_conversation,
    delete_conversation,
    extract_turn_messages,
    list_conversations,
    load_conversation,
    migrate_legacy,
    sanitize_transcript_event,
    save_conversation,
    title_from_message,
)

app = FastAPI(title="Coding Agent")
UI_DIR = Path(__file__).parent

_sessions: dict[str, dict] = {}


def _workspace_path(workspace: str) -> Path:
    return Path(workspace).resolve()


def _get_session(conversation_id: str, workspace: str) -> dict:
    if conversation_id not in _sessions:
        workspace_path = _workspace_path(workspace)
        migrate_legacy(workspace_path)

        conv = load_conversation(workspace_path, conversation_id)
        messages = init_conversation(workspace_path)
        transcript: list[dict] = []
        if conv:
            messages.extend(conv.get("messages", []))
            transcript = conv.get("transcript", [])

        fs = FileSystem(workspace_path)
        py_count = sum(1 for f in fs.tracked_files("*.py"))
        rag_info = f"Indexed {py_count} Python files" if py_count > 3 else None

        _sessions[conversation_id] = {
            "messages": messages,
            "workspace": str(workspace_path),
            "conversation_id": conversation_id,
            "transcript": transcript,
            "rag_info": rag_info,
            "stopped": False,
        }
    return _sessions[conversation_id]


def _persist_session(session: dict, user_message: str | None = None) -> None:
    """Write in-memory session state to disk."""
    workspace_path = Path(session["workspace"])
    conv_id = session["conversation_id"]
    conv = load_conversation(workspace_path, conv_id)
    if conv is None:
        now = _now_iso()
        conv = {
            "id": conv_id,
            "title": "New chat",
            "workspace": str(workspace_path),
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "transcript": [],
        }

    conv["messages"] = extract_turn_messages(session["messages"])
    conv["transcript"] = session.get("transcript", [])

    if conv.get("title", "New chat") == "New chat" and user_message:
        conv["title"] = title_from_message(user_message)

    save_conversation(workspace_path, conv)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/workspace")
async def get_workspace():
    cwd = Path.cwd()
    fs = FileSystem(cwd)
    files = [str(f.relative_to(cwd)) for f in fs.tracked_files()]
    dirs = set()
    for f in files:
        parts = f.replace("\\", "/").split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
    return {
        "workspace": str(cwd),
        "files": files[:200],
        "directories": sorted(dirs),
        "total_files": len(files),
    }


@app.get("/api/conversations")
async def get_conversations(workspace: str | None = None):
    root = _workspace_path(workspace) if workspace else Path.cwd().resolve()
    migrate_legacy(root)
    return {"conversations": list_conversations(root)}


@app.post("/api/conversations")
async def post_conversation(request: Request):
    body = await request.json()
    workspace = body.get("workspace", str(Path.cwd()))
    title = body.get("title", "New chat")
    root = _workspace_path(workspace)
    migrate_legacy(root)
    conv = create_conversation(root, title=title)
    return {"id": conv["id"], "title": conv["title"]}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str, workspace: str | None = None):
    root = _workspace_path(workspace) if workspace else Path.cwd().resolve()
    conv = load_conversation(root, conv_id)
    if conv is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {
        "id": conv["id"],
        "title": conv.get("title", "New chat"),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "transcript": conv.get("transcript", []),
        "message_count": len(conv.get("messages", [])),
    }


@app.delete("/api/conversations/{conv_id}")
async def remove_conversation(conv_id: str, workspace: str | None = None):
    root = _workspace_path(workspace) if workspace else Path.cwd().resolve()
    if conv_id in _sessions:
        del _sessions[conv_id]
    ok = delete_conversation(root, conv_id)
    if not ok:
        return JSONResponse({"error": "Delete failed"}, status_code=500)
    return {"status": "deleted", "id": conv_id}


@app.get("/api/file")
async def get_file(path: str, workspace: str | None = None):
    """Return the contents of a file, restricted to inside the workspace."""
    root = _workspace_path(workspace) if workspace else Path.cwd().resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return JSONResponse({"error": "Path outside workspace"}, status_code=403)
    if not target.is_file():
        return JSONResponse({"error": "Not a file"}, status_code=404)
    try:
        if target.stat().st_size > 1_000_000:
            return JSONResponse({"error": "File too large"}, status_code=413)
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {
        "path": path,
        "content": content,
        "lines": content.count("\n") + 1,
    }


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    user_message = body.get("message", "")
    workspace = body.get("workspace", str(Path.cwd()))
    conversation_id = body.get("conversation_id") or body.get("session_id", "default")

    session = _get_session(conversation_id, workspace)
    session["stopped"] = False
    workspace_path = Path(session["workspace"])
    rag_info = session.pop("rag_info", None)

    def stream():
        turn_events: list[dict] = [{"type": "user", "content": user_message}]
        completed = False

        if rag_info:
            rag_ev = {"type": "rag", "info": rag_info}
            turn_events.append(rag_ev)
            yield f"data: {json.dumps(rag_ev)}\n\n"

        try:
            for event in handle_message_streaming(
                user_message=user_message,
                messages=session["messages"],
                workspace=workspace_path,
                max_tool_rounds=30,
            ):
                stored = sanitize_transcript_event(event)
                turn_events.append(stored)
                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") == "response":
                    completed = True

                if session.get("stopped"):
                    stopped_ev = {"type": "stopped"}
                    turn_events.append(stopped_ev)
                    yield f"data: {json.dumps(stopped_ev)}\n\n"
                    break
        except Exception as e:
            err_ev = {"type": "error", "message": str(e)}
            turn_events.append(err_ev)
            yield f"data: {json.dumps(err_ev)}\n\n"
        finally:
            session["transcript"].extend(turn_events)
            _persist_session(session, user_message=user_message)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/stop")
async def stop_endpoint(request: Request):
    """Cancel a running agent turn."""
    body = await request.json()
    conversation_id = body.get("conversation_id") or body.get("session_id", "default")
    if conversation_id in _sessions:
        _sessions[conversation_id]["stopped"] = True
    return {"status": "stopped", "conversation_id": conversation_id}


@app.post("/api/reset")
async def reset_session(request: Request):
    """Start a new conversation (legacy name kept for compatibility)."""
    body = await request.json()
    workspace = body.get("workspace", str(Path.cwd()))
    root = _workspace_path(workspace)
    migrate_legacy(root)
    conv = create_conversation(root)
    return {"status": "reset", "conversation_id": conv["id"], "id": conv["id"], "title": conv["title"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host="0.0.0.0", port=8000, reload=True)
