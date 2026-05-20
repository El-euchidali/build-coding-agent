import json
from dataclasses import dataclass
from pathlib import Path

from agent.llm import chat, get_token_usage, reset_token_counter, trim_context
from agent.prompts import SYSTEM_PROMPT, TESTS_NOT_PASSED_NUDGE
from agent.tools import TOOL_SCHEMAS, execute_tool, parse_tool_call_fallback
from agent.failure import classify_failure, tests_passed_in_history

MAX_NUDGES_WITHOUT_TESTS = 3


@dataclass
class TaskResult:
    success: bool
    output: str
    iterations: int
    error: str | None = None
    tokens: dict | None = None
    failure_category: str | None = None


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


def _finalize_result(
    success: bool,
    output: str,
    iterations: int,
    messages: list[dict],
    error: str | None = None,
) -> TaskResult:
    result = TaskResult(
        success=success,
        output=output,
        iterations=iterations,
        error=error,
        tokens=get_token_usage(),
    )
    result.failure_category = classify_failure(result, messages)
    return result


def run_task(
    description: str,
    workspace: Path,
    max_iterations: int = 12,
) -> TaskResult:
    """Run the agent loop until tests pass, nudge limit hit, or max iterations."""
    workspace.mkdir(parents=True, exist_ok=True)
    reset_token_counter()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Task:\n{description}\n\nWorkspace: {workspace.resolve()}",
        },
    ]

    last_output = ""
    nudge_count = 0

    for iteration in range(1, max_iterations + 1):
        messages = trim_context(messages)
        tools = (
            TOOL_SCHEMAS
            if iteration == 1
            else [t for t in TOOL_SCHEMAS if t["function"]["name"] != "write_file"]
        )

        message = chat(messages, tools=tools)
        messages.append(_assistant_message_dict(message))
        last_output = message.content or ""

        tool_calls = _get_tool_calls(message)

        if not tool_calls:
            if tests_passed_in_history(messages):
                return _finalize_result(True, last_output, iteration, messages)
            nudge_count += 1
            if nudge_count > MAX_NUDGES_WITHOUT_TESTS:
                return _finalize_result(
                    False,
                    last_output,
                    iteration,
                    messages,
                    error="stopped_early",
                )
            messages.append({"role": "user", "content": TESTS_NOT_PASSED_NUDGE})
            continue

        # One tool per turn — execute first only, inform model if more were requested
        if len(tool_calls) > 1:
            skipped = [tc["name"] for tc in tool_calls[1:]]
            tool_calls = tool_calls[:1]
            extra_note = (
                f"\nNote: Only one tool per turn. Skipped: {', '.join(skipped)}. "
                "Call again for the next action."
            )
        else:
            extra_note = ""

        tc = tool_calls[0]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}
        result = execute_tool(tc["name"], args, workspace) + extra_note
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            }
        )

    return _finalize_result(
        False,
        last_output,
        max_iterations,
        messages,
        error="max_iterations",
    )
