"""
Conversational coding agent UI.
Run: python -m ui.app
Open: http://localhost:8000
"""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from agent.agent import init_conversation, handle_message_streaming
from agent.filesystem import FileSystem

app = FastAPI(title="Coding Agent")
UI_DIR = Path(__file__).parent

_sessions: dict[str, dict] = {}


def _get_session(session_id: str, workspace: str) -> dict:
    if session_id not in _sessions:
        workspace_path = Path(workspace)
        messages = init_conversation(workspace_path)

        fs = FileSystem(workspace_path)
        py_count = sum(1 for f in fs.tracked_files("*.py"))
        rag_info = f"Indexed {py_count} Python files" if py_count > 3 else None

        _sessions[session_id] = {
            "messages": messages,
            "workspace": workspace,
            "rag_info": rag_info,
        }
    return _sessions[session_id]


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


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    body = await request.json()
    user_message = body.get("message", "")
    workspace = body.get("workspace", str(Path.cwd()))
    session_id = body.get("session_id", "default")

    session = _get_session(session_id, workspace)
    workspace_path = Path(session["workspace"])
    rag_info = session.pop("rag_info", None)

    def stream():
        if rag_info:
            yield f"data: {json.dumps({'type': 'rag', 'info': rag_info})}\n\n"
        for event in handle_message_streaming(
            user_message=user_message,
            messages=session["messages"],
            workspace=workspace_path,
            max_tool_rounds=15,
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/reset")
async def reset_session(request: Request):
    body = await request.json()
    session_id = body.get("session_id", "default")
    if session_id in _sessions:
        del _sessions[session_id]
    return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.app:app", host="0.0.0.0", port=8000, reload=True)