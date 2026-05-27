import json
from dataclasses import dataclass
from pathlib import Path

from agent.llm import chat, get_token_usage, reset_token_counter, trim_context
from agent.prompts import SYSTEM_PROMPT, TESTS_NOT_PASSED_NUDGE
from agent.tools import TOOL_SCHEMAS, execute_tool, parse_tool_call_fallback
from agent.failure import classify_failure, tests_passed_in_history
from agent.fsm import AgentState, get_tools_for_state, transition, detect_initial_state

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
    """Run the agent loop with FSM control."""
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
    state = detect_initial_state(workspace)
    last_tool: str | None = None
    last_result: str = ""
    has_written = False 

    for iteration in range(1, max_iterations + 1):
        messages = trim_context(messages)

        # FSM: get tools valid for current state
        tools = get_tools_for_state(state, TOOL_SCHEMAS, has_written)
        

        message = chat(messages, tools=tools)
        messages.append(_assistant_message_dict(message))
        last_output = message.content or ""

        tool_calls = _get_tool_calls(message)
        
        

        # FSM enforcement: reject tool calls not allowed in current state
        allowed_names = [t["function"]["name"] for t in tools]
        invalid = [tc for tc in tool_calls if tc["name"] not in allowed_names]
        tool_calls = [tc for tc in tool_calls if tc["name"] in allowed_names]

        if invalid and not tool_calls:
            state_guidance = {
                AgentState.EXPLORE: "You are in EXPLORE phase. Read and understand the code first. Use: list_files, read_file, view_file_range, search_code.",
                AgentState.IMPLEMENT: "You are in IMPLEMENT phase. Write your solution using write_file (first time) or str_replace (fixes).",
                AgentState.VERIFY: "You are in VERIFY phase. Run run_tests to check your solution.",
                AgentState.FIX: "You are in FIX phase. Use str_replace to fix the specific failing line, then run_tests.",
                AgentState.DONE: "Task is complete.",
            }
            messages.append({
                "role": "user",
                "content": state_guidance.get(state, f"Available tools: {', '.join(allowed_names)}")
            })
            continue

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

        # One tool per turn
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

        last_tool = tc["name"]
        last_result = execute_tool(last_tool, args, workspace) + extra_note
        if last_tool == "write_file":
            has_written = True

        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": last_result,
        })

        # FSM: transition to next state
        state = transition(state, last_tool, last_result, iteration)

        if state == AgentState.DONE:
            return _finalize_result(True, last_output, iteration, messages)

    return _finalize_result(
        False,
        last_output,
        max_iterations,
        messages,
        error="max_iterations",
    )
