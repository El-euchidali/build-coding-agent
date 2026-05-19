import json
from dataclasses import dataclass
from pathlib import Path

from agent.llm import chat
from agent.prompts import SYSTEM_PROMPT
from agent.tools import TOOL_SCHEMAS, execute_tool, parse_tool_call_fallback


@dataclass
class TaskResult:
    success: bool
    output: str
    iterations: int
    error: str | None = None


def _assistant_message_dict(message) -> dict:
    msg: dict = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
    return msg


def _get_tool_calls(message) -> list[dict]:
    """Extract tool calls from message (native API or JSON fallback)."""
    if message.tool_calls:
        return [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in message.tool_calls
        ]
    fallback = parse_tool_call_fallback(message.content)
    if fallback:
        return [
            {
                "id": f"fallback_{i}",
                "name": item["name"],
                "arguments": json.dumps(item["arguments"])
                if isinstance(item["arguments"], dict)
                else item["arguments"],
            }
            for i, item in enumerate(fallback)
        ]
    return []


def run_task(
    description: str,
    workspace: Path,
    max_iterations: int = 10,
) -> TaskResult:
    """Run the agent loop until done or max iterations."""
    workspace.mkdir(parents=True, exist_ok=True)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Task:\n{description}\n\nWorkspace: {workspace.resolve()}",
        },
    ]

    last_output = ""
    for iteration in range(1, max_iterations + 1):
        message = chat(messages, tools=TOOL_SCHEMAS)
        messages.append(_assistant_message_dict(message))
        last_output = message.content or ""

        tool_calls = _get_tool_calls(message)
        if not tool_calls:
            success = _check_tests_passed(messages)
            return TaskResult(
                success=success,
                output=last_output,
                iterations=iteration,
            )

        for tc in tool_calls:
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(tc["name"], args, workspace)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )

    return TaskResult(
        success=False,
        output=last_output,
        iterations=max_iterations,
        error="max_iterations",
    )


def _check_tests_passed(messages: list[dict]) -> bool:
    """Check if the most recent test run reported all tests passed."""
    for msg in reversed(messages):
        if msg.get("role") == "tool" and "* ALL TESTS PASSED" in msg.get("content", ""):
            return True
    return False
