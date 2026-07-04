"""
Conversational coding agent UI with IDE layout.
Run: python -m ui.app
Open: http://localhost:8000
"""

from contextlib import asynccontextmanager
import asyncio
import json
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.agent import init_conversation, handle_message_streaming
from agent.llm import get_token_usage
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
from ui.chat_runs import ChatRun, ChatRunManager
from ui.session import (
    browse_directory,
    build_tree,
    get_chat_session,
    list_tree_children,
    open_workspace,
    register_chat_session,
    remove_chat_session,
    validate_workspace,
)
from ui.terminal import pty_manager

UI_DIR = Path(__file__).parent
FRONTEND_DIST = UI_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await pty_manager.shutdown()


app = FastAPI(title="Coding Agent IDE", lifespan=lifespan)
chat_runs = ChatRunManager()


def _workspace_path(workspace: str) -> Path:
    return Path(workspace).resolve()


def _get_session(conversation_id: str, workspace: str) -> dict:
    existing = get_chat_session(conversation_id)
    if existing:
        return existing

    workspace_path = _workspace_path(workspace)
    migrate_legacy(workspace_path)

    conv = load_conversation(workspace_path, conversation_id)
    messages = init_conversation(workspace_path, build_rag=False)
    transcript: list[dict] = []
    if conv:
        messages.extend(conv.get("messages", []))
        transcript = conv.get("transcript", [])

    fs = FileSystem(workspace_path)
    py_count = sum(1 for f in fs.tracked_files("*.py"))
    rag_info = f"Indexed {py_count} Python files" if py_count > 3 else None

    session = {
        "messages": messages,
        "workspace": str(workspace_path),
        "conversation_id": conversation_id,
        "transcript": transcript,
        "rag_info": rag_info,
        "stopped": False,
    }
    register_chat_session(conversation_id, session)
    return session


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


def _serve_index() -> FileResponse | HTMLResponse:
    dist_index = FRONTEND_DIST / "index.html"
    if not dist_index.exists():
        return HTMLResponse(
            "<!DOCTYPE html><html><body>"
            "<h1>Frontend not built</h1>"
            "<p>Run <code>cd ui/frontend && npm install && npm run build</code>, "
            "or <code>npm run dev</code> for development with hot reload.</p>"
            "</body></html>",
            status_code=503,
        )
    return FileResponse(dist_index)


def _stream_run(run: ChatRun, offset: int = 0) -> StreamingResponse:
    return StreamingResponse(run.sse(offset), media_type="text/event-stream")


def _stream_run_missing(run_id: str, offset: int = 0) -> StreamingResponse:
    event = {
        "type": "run_finished",
        "run_id": run_id,
        "offset": offset,
        "status": "missing",
        "done": True,
    }
    return StreamingResponse(
        iter([f"data: {json.dumps(event)}\n\n"]),
        media_type="text/event-stream",
    )


@app.get("/")
async def index():
    return _serve_index()


@app.get("/api/fs/browse")
async def fs_browse(path: str | None = None):
    """Browse server directories for the folder picker."""
    try:
        return browse_directory(path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/workspace/open")
async def open_workspace_endpoint(request: Request):
    body = await request.json()
    session_id = body.get("session_id", "default")
    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "path is required"}, status_code=400)

    try:
        session = open_workspace(session_id, path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Failed to open workspace: {e}"}, status_code=500)

    try:
        await asyncio.to_thread(pty_manager.respawn_sync, session_id, session.workspace)
    except Exception as e:
        return JSONResponse({"error": f"Failed to start terminal: {e}"}, status_code=500)
    fs = FileSystem(session.workspace)

    return {
        "workspace": str(session.workspace),
        "conversation_id": session.conversation_id,
        "tree": build_tree(fs),
        "terminal_ws": f"/ws/terminal?session_id={session_id}",
        "total_files": len(list(fs.tracked_files())),
    }


@app.get("/api/workspace")
async def get_workspace(workspace: str | None = None):
    try:
        root = validate_workspace(workspace) if workspace else Path.cwd().resolve()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    fs = FileSystem(root)
    files = [str(f.relative_to(root)).replace("\\", "/") for f in fs.tracked_files()]
    dirs = set()
    for f in files:
        parts = f.split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
    return {
        "workspace": str(root),
        "files": files[:200],
        "directories": sorted(dirs),
        "total_files": len(files),
        "tree": build_tree(fs),
    }


@app.get("/api/workspace/tree")
async def get_workspace_tree(workspace: str, path: str = ""):
    try:
        root = validate_workspace(workspace)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    fs = FileSystem(root)
    return {"children": list_tree_children(fs, path)}


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
    remove_chat_session(conv_id)
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


@app.get("/api/git/diff")
async def get_git_diff(workspace: str):
    """Return git status and diff for the workspace inspector."""
    try:
        root = validate_workspace(workspace, create=False)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    try:
        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff_result = subprocess.run(
            ["git", "diff"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    files = []
    if status_result.returncode == 0:
        for line in status_result.stdout.splitlines():
            if len(line) < 4:
                continue
            files.append({
                "status": line[:2].strip() or line[:2],
                "path": line[3:].strip(),
            })

    diff = diff_result.stdout
    if diff_result.returncode != 0 and diff_result.stderr:
        diff = diff_result.stderr

    return {"files": files, "diff": diff}


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    user_message = body.get("message", "")
    display_message = body.get("display_message") or user_message
    workspace = body.get("workspace", str(Path.cwd()))
    conversation_id = body.get("conversation_id") or body.get("session_id", "default")

    active_run = chat_runs.get_active(conversation_id)
    if active_run:
        return _stream_run(active_run)

    session = _get_session(conversation_id, workspace)
    session["stopped"] = False
    workspace_path = Path(session["workspace"])
    rag_info = session.pop("rag_info", None)
    run = chat_runs.create(
        conversation_id=conversation_id,
        workspace=str(workspace_path),
        user_message=display_message,
        session=session,
    )

    def execute_run(active: ChatRun):
        turn_events: list[dict] = [{"type": "user", "content": display_message}]
        status = "complete"

        if rag_info:
            rag_ev = {"type": "rag", "info": rag_info}
            turn_events.append(rag_ev)
            active.append(rag_ev)

        try:
            for event in handle_message_streaming(
                user_message=user_message,
                messages=session["messages"],
                workspace=workspace_path,
                max_tool_rounds=45 if body.get("mode", "ask") == "work" else 30,
                composer_mode=body.get("mode", "ask"),
                stream=True,
            ):
                if session.get("stopped") or active.done:
                    status = "stopped"
                    break
                if (
                    event.get("type") == "thinking"
                    and event.get("content") == "Waiting for model response..."
                ):
                    continue
                if event.get("type") in (
                    "heartbeat", "status", "token", "response_start", "stream_discard",
                ):
                    active.append(event)
                    continue
                if event.get("type") == "response_end":
                    stored = {
                        "type": "response",
                        "content": event.get("content", ""),
                    }
                    turn_events.append(stored)
                    active.append(event)
                    continue
                stored = sanitize_transcript_event(event)
                turn_events.append(stored)
                active.append(event)

                if event.get("type") == "response":
                    pass

                if session.get("stopped"):
                    status = "stopped"
                    stopped_ev = {"type": "stopped"}
                    turn_events.append(stopped_ev)
                    active.append(stopped_ev)
                    break
        except Exception as e:
            status = "error"
            err_ev = {"type": "error", "message": str(e)}
            turn_events.append(err_ev)
            active.append(err_ev)
        finally:
            if status == "stopped" and not any(e.get("type") == "stopped" for e in turn_events):
                turn_events.append({"type": "stopped"})
            session["transcript"].extend(turn_events)
            _persist_session(session, user_message=display_message)
            active.finish(status, tokens=get_token_usage())

    chat_runs.start(run, execute_run)
    return _stream_run(run)


@app.get("/api/chat/runs/active")
async def get_active_chat_run(conversation_id: str):
    run = chat_runs.get_active(conversation_id)
    return {"run": run.info() if run else None}


@app.get("/api/chat/runs/{run_id}/events")
async def get_chat_run_events(run_id: str, offset: int = 0):
    run = chat_runs.get(run_id)
    if not run:
        return _stream_run_missing(run_id, offset)
    return _stream_run(run, offset)


@app.post("/api/stop")
async def stop_endpoint(request: Request):
    """Cancel a running agent turn."""
    body = await request.json()
    conversation_id = body.get("conversation_id") or body.get("session_id", "default")
    run_id = body.get("run_id")
    run = chat_runs.get(run_id) if run_id else chat_runs.get_active(conversation_id)
    session = run.session if run else get_chat_session(conversation_id)
    if session:
        session["stopped"] = True
    if run:
        run.finish("stopped")
    return {
        "status": "stopped",
        "conversation_id": conversation_id,
        "run_id": run.id if run else run_id,
    }


@app.post("/api/reset")
async def reset_session(request: Request):
    """Start a new conversation (legacy name kept for compatibility)."""
    body = await request.json()
    workspace = body.get("workspace", str(Path.cwd()))
    root = _workspace_path(workspace)
    migrate_legacy(root)
    conv = create_conversation(root)
    return {"status": "reset", "conversation_id": conv["id"], "id": conv["id"], "title": conv["title"]}


@app.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket, session_id: str = "default"):
    await pty_manager.attach_websocket(websocket, session_id)


if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ui.app:app", host="0.0.0.0", port=8000, reload=True)
