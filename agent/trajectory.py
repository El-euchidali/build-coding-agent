"""
Trajectory logging — saves every agent run as structured JSON.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


class Trajectory:

    def __init__(self, task_id: str = "interactive"):
        self.task_id = task_id
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.steps: list[dict] = []
        self.result: dict | None = None

    def log_step(self, iteration: int, state: str, tool: str, args: dict,
                 result: str, tokens: dict | None = None, elapsed: float = 0):
        self.steps.append({
            "iteration": iteration, "state": state, "tool": tool,
            "args": args, "result": result[:500],
            "tokens": tokens, "elapsed": elapsed,
        })

    def log_result(self, success: bool, error: str | None = None, tokens: dict | None = None):
        self.result = {
            "success": success, "error": error,
            "tokens": tokens, "total_steps": len(self.steps),
        }

    def save(self) -> Path:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = LOGS_DIR / f"trajectory_{self.task_id}_{timestamp}.json"
        data = {
            "task_id": self.task_id,
            "start_time": self.start_time,
            "steps": self.steps,
            "result": self.result,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path