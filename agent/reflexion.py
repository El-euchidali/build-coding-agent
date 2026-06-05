"""
Reflexion — agent learns from its own failures.
Stores failure reflections and retrieves them for similar tasks.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

REFLECTIONS_DIR = Path(__file__).resolve().parent.parent / "reflections"


def save_reflection(task_description: str, error: str, reflection: str):
    """Save a failure reflection for future retrieval."""
    REFLECTIONS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    entry = {
        "task": task_description[:200],
        "error": error,
        "reflection": reflection,
        "timestamp": timestamp,
    }
    path = REFLECTIONS_DIR / f"reflection_{timestamp}.json"
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")


def get_past_reflections(task_description: str, max_results: int = 3) -> str:
    """Retrieve past reflections that might be relevant to the current task."""
    if not REFLECTIONS_DIR.exists():
        return ""

    reflections = []
    for path in sorted(REFLECTIONS_DIR.glob("reflection_*.json"), reverse=True):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            reflections.append(entry)
        except (json.JSONDecodeError, OSError):
            continue
        if len(reflections) >= max_results:
            break

    if not reflections:
        return ""

    output = "## Lessons from Past Failures\n"
    for r in reflections:
        output += f"- Task: {r['task'][:100]}\n  Error: {r['error']}\n  Lesson: {r['reflection']}\n\n"
    return output