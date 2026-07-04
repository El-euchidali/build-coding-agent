"""Background chat run buffering for resumable UI streams."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class ChatRun:
    id: str
    conversation_id: str
    workspace: str
    user_message: str
    session: dict
    created_at: float = field(default_factory=time.time)
    status: str = "running"
    done: bool = False
    events: list[dict] = field(default_factory=list)
    _condition: threading.Condition = field(default_factory=threading.Condition)

    def append(self, event: dict) -> dict:
        """Store an event and notify attached SSE clients."""
        with self._condition:
            out = dict(event)
            out["run_id"] = self.id
            out["offset"] = len(self.events)
            if self.done:
                return out
            self.events.append(out)
            self._condition.notify_all()
            return out

    def finish(self, status: str = "complete", tokens: dict | None = None) -> None:
        with self._condition:
            if self.done:
                return
            self.status = status
            event = {
                "type": "run_finished",
                "run_id": self.id,
                "offset": len(self.events),
                "status": status,
                "done": True,
            }
            if tokens:
                event["tokens"] = tokens
            self.events.append(event)
            self.done = True
            self._condition.notify_all()

    def next_event(self, offset: int, timeout: float = 15.0) -> dict | None:
        with self._condition:
            if offset < len(self.events):
                return self.events[offset]
            if self.done:
                return None
            self._condition.wait(timeout=timeout)
            if offset < len(self.events):
                return self.events[offset]
            return None

    def is_exhausted(self, offset: int) -> bool:
        with self._condition:
            return self.done and offset >= len(self.events)

    def sse(self, offset: int = 0) -> Iterator[str]:
        index = max(0, offset)
        while True:
            event = self.next_event(index)
            if event is None:
                if self.is_exhausted(index):
                    break
                yield ": keepalive\n\n"
                continue
            index = int(event.get("offset", index)) + 1
            yield f"data: {json.dumps(event)}\n\n"

    def info(self) -> dict:
        with self._condition:
            return {
                "id": self.id,
                "conversation_id": self.conversation_id,
                "workspace": self.workspace,
                "user_message": self.user_message,
                "status": self.status,
                "done": self.done,
                "event_count": len(self.events),
            }


class ChatRunManager:
    def __init__(self) -> None:
        self._runs: dict[str, ChatRun] = {}
        self._active_by_conversation: dict[str, str] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        conversation_id: str,
        workspace: str,
        user_message: str,
        session: dict,
    ) -> ChatRun:
        run = ChatRun(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            workspace=workspace,
            user_message=user_message,
            session=session,
        )
        run.append({
            "type": "run_started",
            "conversation_id": conversation_id,
            "workspace": workspace,
            "user_message": user_message,
        })
        with self._lock:
            self._runs[run.id] = run
            self._active_by_conversation[conversation_id] = run.id
        return run

    def start(self, run: ChatRun, target: Callable[[ChatRun], None]) -> None:
        thread = threading.Thread(
            target=self._run_wrapper,
            args=(run, target),
            name=f"chat-run-{run.id[:8]}",
            daemon=True,
        )
        thread.start()

    def _run_wrapper(self, run: ChatRun, target: Callable[[ChatRun], None]) -> None:
        try:
            target(run)
        finally:
            if not run.done:
                run.finish("complete")
            with self._lock:
                if self._active_by_conversation.get(run.conversation_id) == run.id:
                    self._active_by_conversation.pop(run.conversation_id, None)

    def get(self, run_id: str) -> ChatRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def get_active(self, conversation_id: str) -> ChatRun | None:
        with self._lock:
            run_id = self._active_by_conversation.get(conversation_id)
            if not run_id:
                return None
            run = self._runs.get(run_id)
            if not run or run.done:
                self._active_by_conversation.pop(conversation_id, None)
                return None
            return run
