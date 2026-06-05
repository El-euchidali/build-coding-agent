import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.llm import chat, get_token_usage, reset_token_counter, trim_context
from agent.prompts import SYSTEM_PROMPT, TESTS_NOT_PASSED_NUDGE
from agent.tools import TOOL_SCHEMAS, execute_tool, parse_tool_call_fallback
from agent.failure import classify_failure, tests_passed_in_history
from agent.rag import CodebaseIndex, set_current_index
from agent.filesystem import FileSystem
from agent.fsm import AgentState, STATE_TOOLS, get_tools_for_state, transition, detect_initial_state
from agent.trajectory import Trajectory
from agent.tools import reset_scratchpad, get_scratchpad
from agent.reflexion import save_reflection, get_past_reflections


_DANGEROUS_TOOLS = frozenset({
    "write_file", "str_replace", "delete_lines", "insert_at_line",
    "edit_files", "edit_and_verify", "search_and_replace_all",
    "git_commit", "run_command", "create_directory",
})

MAX_NUDGES_WITHOUT_TESTS = 3

# System prompt for conversational mode — more flexible than task runner
CONVERSATIONAL_PROMPT = """You are an expert Python coding agent. You help developers understand, navigate, edit, and debug their codebase through conversation.

## Workspace
You are working in: {workspace}

## What you can do
- Answer questions about the codebase — read files, search for patterns, explain code
- Navigate large repos — find files, outline functions, explore structure
- Write new code — create files, implement functions
- Fix bugs — read errors, find the cause, apply surgical fixes
- Run tests — execute code, run pytest, check results
- Git operations — status, diff, commit, log

## How to behave
- Be conversational — explain what you are doing and why
- Use tools when you need information — do not guess file contents
- For simple questions, call the relevant tool and answer based on the result
- For coding tasks, work step by step: read → understand → fix → verify
- If you are unsure, ask the user for clarification
- Keep responses concise but informative

## Available tools
You have access to tools for file reading, searching, editing, code execution, and git.
Use them whenever you need concrete information — do not make up file contents or test results.
"""


@dataclass
class TaskResult:
    success: bool
    output: str
    iterations: int
    error: str | None = None
    tokens: dict | None = None
    failure_category: str | None = None


@dataclass
class ToolCall:
    """Record of a single tool call during conversation."""
    name: str
    args: dict
    result: str
    elapsed: float


@dataclass
class MessageResponse:
    """Response from handle_message."""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: dict | None = None


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


def _detect_loop(messages: list[dict], window: int = 3) -> bool:
    """Detect if the last N tool results contain the same error."""
    recent = []
    for msg in reversed(messages):
        if msg.get("role") == "tool":
            recent.append(msg["content"][:100])
        if len(recent) >= window:
            break
    return len(recent) == window and len(set(recent)) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Conversational mode — for the UI
# ══════════════════════════════════════════════════════════════════════════════

def init_conversation(workspace: Path) -> list[dict]:
    """Initialize a new conversation with system prompt and RAG index."""
    reset_scratchpad()
    workspace.mkdir(parents=True, exist_ok=True)

    # Build RAG index if codebase is large enough
    fs = FileSystem(workspace)
    py_files = [f for f in fs.tracked_files("*.py")]
    if len(py_files) > 3:
        index = CodebaseIndex(workspace)
        chunks = index.build()
        set_current_index(index)
    else:
        set_current_index(None)

    prompt = CONVERSATIONAL_PROMPT.format(workspace=workspace.resolve())
    return [{"role": "system", "content": prompt}]


def handle_message(
    user_message: str,
    messages: list[dict],
    workspace: Path,
    max_tool_rounds: int = 30,
) -> tuple[MessageResponse, list[dict]]:
    """
    Handle a single user message in conversational mode.

    The LLM decides whether to use tools or just respond with text.
    If it calls tools, we execute them and let the LLM respond again,
    repeating until the LLM gives a text-only response or we hit max rounds.

    Returns (response, updated_messages).
    """
    reset_token_counter()

    # Add user message
    messages.append({"role": "user", "content": user_message})
    messages = trim_context(messages)

    tool_history: list[ToolCall] = []

    for round_num in range(max_tool_rounds):
        start_time = time.time()

        # Call LLM with all tools available — it decides what to use
        message = chat(messages, tools=TOOL_SCHEMAS)
        elapsed = round(time.time() - start_time, 1)

        # Add assistant message to history
        messages.append(_assistant_message_dict(message))

        # Extract tool calls
        tool_calls = _get_tool_calls(message)

        # No tool calls — LLM is responding with text, we are done
        if not tool_calls:
            return MessageResponse(
                content=message.content or "",
                tool_calls=tool_history,
                tokens=get_token_usage(),
            ), messages

        # Execute first tool call (one at a time for feedback)
        tc = tool_calls[0]
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args = {}

        tool_start = time.time()
        result = execute_tool(tc["name"], args, workspace)
        tool_elapsed = round(time.time() - tool_start, 1)

        # Record the tool call
        tool_history.append(ToolCall(
            name=tc["name"],
            args=args,
            result=result[:800],
            elapsed=tool_elapsed,
        ))

        # Add tool result to conversation
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    # Max rounds reached — return whatever we have
    return MessageResponse(
        content="I reached the maximum number of tool calls. Let me know if you want me to continue.",
        tool_calls=tool_history,
        tokens=get_token_usage(),
    ), messages


def handle_message_streaming(
    user_message: str,
    messages: list[dict],
    workspace: Path,
    max_tool_rounds: int = 30,
):
    """
    Streaming version of handle_message.
    Yields events as the agent thinks and uses tools.
    Returns updated messages list via the final event.
    """
    reset_token_counter()

    # Add user message
    messages.append({"role": "user", "content": user_message})
    messages = trim_context(messages)
    # Inject scratchpad so it survives context trimming
    scratchpad = get_scratchpad()
    if scratchpad:
        pad_content = "## Your Scratchpad Notes\n"
        for key, value in scratchpad.items():
            pad_content += f"### {key}\n{value}\n\n"
        # Remove old scratchpad injection if present
        messages = [m for m in messages if not m.get("content", "").startswith("## Your Scratchpad Notes")]
        # Insert after system prompt and task
        messages.insert(2, {"role": "system", "content": pad_content})

    for round_num in range(max_tool_rounds):
        start_time = time.time()

        message = chat(messages, tools=TOOL_SCHEMAS)
        elapsed = round(time.time() - start_time, 1)

        messages.append(_assistant_message_dict(message))

        tool_calls = _get_tool_calls(message)

        # No tool calls — final text response
        if not tool_calls:
            yield {
                "type": "response",
                "content": message.content or "",
                "tokens": get_token_usage(),
                "elapsed": elapsed,
            }
            return

        # Execute first tool call — permission check for dangerous tools
        tc = tool_calls[0]
        try:
            args_preview = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            args_preview = {}
        if tc["name"] in _DANGEROUS_TOOLS:
            yield {
                "type": "permission",
                "tool": tc["name"],
                "args": args_preview,
                "message": f"I want to use {tc['name']}. Allow?",
            }

        tool_start = time.time()
        result = execute_tool(tc["name"], args, workspace)
        tool_elapsed = round(time.time() - tool_start, 1)

        # Yield tool call event
        yield {
            "type": "tool_call",
            "tool": tc["name"],
            "args": args,
            "result": result[:800],
            "elapsed": elapsed,
            "tool_elapsed": tool_elapsed,
            "round": round_num + 1,
            "tokens": get_token_usage(),
        }

        # If LLM also included text content, yield it
        if message.content:
            yield {
                "type": "thinking",
                "content": message.content,
            }

        # Add tool result to conversation
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })

    # Max rounds
    yield {
        "type": "response",
        "content": "I reached the maximum number of tool calls. Let me know if you want me to continue.",
        "tokens": get_token_usage(),
        "elapsed": 0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Autonomous mode — for benchmarks (unchanged)
# ══════════════════════════════════════════════════════════════════════════════

def run_task(
    description: str,
    workspace: Path,
    max_iterations: int = 12,
    token_budget: int = 200000,
) -> TaskResult:
    """Run the agent loop with FSM control. Used for benchmarks."""
    workspace.mkdir(parents=True, exist_ok=True)

    # Build RAG index if codebase is large enough
    fs = FileSystem(workspace)
    py_files = [f for f in fs.tracked_files("*.py")]
    if len(py_files) > 3:
        index = CodebaseIndex(workspace)
        chunks = index.build()
        print(f"  [RAG] indexed {chunks} chunks from {len(py_files)} files")
        set_current_index(index)
    else:
        set_current_index(None)

    reset_token_counter()
    reset_scratchpad()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Task:\n{description}\n\nWorkspace: {workspace.resolve()}",
        },
    ]
    
    # Inject past reflections if available
    past = get_past_reflections(description)
    if past:
        messages.insert(2, {"role": "system", "content": past})

    last_output = ""
    nudge_count = 0
    state = detect_initial_state(workspace)
    last_tool: str | None = None
    last_result: str = ""
    has_written = False
    blocked_count = 0
    trajectory = Trajectory(task_id=description[:50].replace(" ", "_"))


    for iteration in range(1, max_iterations + 1):
        messages = trim_context(messages)
        # Inject scratchpad so it survives context trimming
        scratchpad = get_scratchpad()
        if scratchpad:
            pad_content = "## Your Scratchpad Notes\n"
            for key, value in scratchpad.items():
                pad_content += f"### {key}\n{value}\n\n"
            # Remove old scratchpad injection if present
            messages = [m for m in messages if not m.get("content", "").startswith("## Your Scratchpad Notes")]
            # Insert after system prompt and task
            messages.insert(2, {"role": "system", "content": pad_content})

        # Loop detection
        if _detect_loop(messages):
            messages.append({
                "role": "user",
                "content": "You have tried the same approach 3 times with the same result. "
                           "Stop retrying and try a completely different approach. "
                           "If tests cannot run, skip them and focus on reading the code and applying the fix."
            })

        # FSM: get tools valid for current state
        tools = get_tools_for_state(state, TOOL_SCHEMAS, has_written)

        message = chat(messages, tools=tools)
        messages.append(_assistant_message_dict(message))
        last_output = message.content or ""

        tool_calls = _get_tool_calls(message)

        # FSM enforcement
        allowed_names = [t["function"]["name"] for t in tools]
        invalid = [tc for tc in tool_calls if tc["name"] not in allowed_names]
        tool_calls = [tc for tc in tool_calls if tc["name"] in allowed_names]

        if invalid and not tool_calls:
            blocked_count += 1
            if blocked_count >= 3:
                if state == AgentState.IMPLEMENT:
                    state = AgentState.VERIFY
                elif state == AgentState.EXPLORE:
                    state = AgentState.IMPLEMENT
                elif state == AgentState.PLAN:
                    state = AgentState.EXPLORE
                blocked_count = 0
                messages.append({
                    "role": "user",
                    "content": f"Moving to {state.value} phase. Available tools: "
                               f"{', '.join(STATE_TOOLS[state])}"
                })
                continue
            state_guidance = {
                AgentState.PLAN: "You are in PLAN phase. Use: explore_repo, search_codebase, search_and_read, find_files, file_outline.",
                AgentState.EXPLORE: "You are in EXPLORE phase. Read and understand the code first. Use: list_files, read_file, search_code, search_codebase, run_tests.",
                AgentState.IMPLEMENT: "You are in IMPLEMENT phase. Write your solution using write_file or fix with str_replace.",
                AgentState.VERIFY: "You are in VERIFY phase. Run run_tests to check your solution.",
                AgentState.FIX: "You are in FIX phase. Use str_replace to fix the specific failing line.",
                AgentState.DONE: "Task is complete.",
            }
            messages.append({
                "role": "user",
                "content": state_guidance.get(state, f"Available tools: {', '.join(allowed_names)}")
            })
            continue

        if not tool_calls:
            if tests_passed_in_history(messages):
                trajectory.log_result(success=True, tokens=get_token_usage())
                trajectory.save()
                return _finalize_result(True, last_output, iteration, messages)
            nudge_count += 1
            if nudge_count > MAX_NUDGES_WITHOUT_TESTS:
                trajectory.log_result(success=False, error="stopped_early", tokens=get_token_usage())
                trajectory.save()
                save_reflection(
                    task_description=description,
                    error="stopped_early",
                    reflection=f"Agent stopped without passing tests after {iteration} iterations. "
                               f"Consider: verify test output parsing, check if tests can run."
                )
                return _finalize_result(
                    False,
                    last_output,
                    iteration,
                    messages,
                    error="stopped_early",
                )
            messages.append({"role": "user", "content": TESTS_NOT_PASSED_NUDGE})
            continue

        
        # Parallel reads allowed, one write per turn
        _READ_TOOLS = frozenset({
            "list_files", "view_directory", "find_files", "read_file", "read_files",
            "view_file_range", "search_code", "search_codebase", "search_and_read",
            "explore_repo", "file_outline", "get_function", "git_status", "git_diff",
            "git_log", "read_scratchpad",
        })
        if len(tool_calls) > 1:
            # Allow multiple reads, but only one write
            reads = [tc for tc in tool_calls if tc["name"] in _READ_TOOLS]
            writes = [tc for tc in tool_calls if tc["name"] not in _READ_TOOLS]
            if writes:
                tool_calls = reads + writes[:1]
                skipped = writes[1:]
            else:
                tool_calls = reads
                skipped = []
            if skipped:
                extra_note = (
                    f"\nNote: Skipped write tools (one per turn): "
                    f"{', '.join(tc['name'] for tc in skipped)}."
                )
            else:
                extra_note = ""
        else:
            extra_note = ""

        # Execute all tool calls this turn (parallel reads, one write)
        for tc in tool_calls:
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}

            last_tool = tc["name"]
            last_result = execute_tool(last_tool, args, workspace)
            blocked_count = 0

            if last_tool == "write_file":
                has_written = True

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": last_result,
            })

            trajectory.log_step(
                iteration=iteration, state=state.value, tool=last_tool,
                args=args, result=last_result, tokens=get_token_usage(),
            )

        last_result = last_result + extra_note

        # Token budget check
        current_tokens = get_token_usage()
        if current_tokens and current_tokens.get("total", 0) > token_budget:
            trajectory.log_result(success=False, error="token_budget_exceeded", tokens=current_tokens)
            trajectory.save()
            return _finalize_result(
                False, last_output, iteration, messages,
                error=f"token_budget_exceeded ({current_tokens['total']:,} > {token_budget:,})"
            )
        elif current_tokens and current_tokens.get("total", 0) > token_budget * 0.8:
            messages.append({
                "role": "user",
                "content": "WARNING: You have used 80% of your token budget. "
                           "Finish your current approach quickly — do not start new explorations."
            })
            
        # FSM: transition to next state
        state = transition(state, last_tool, last_result, iteration)

        if state == AgentState.DONE:
            trajectory.log_result(success=True, tokens=get_token_usage())
            trajectory.save()
            return _finalize_result(True, last_output, iteration, messages)

    trajectory.log_result(success=False, error="max_iterations", tokens=get_token_usage())
    trajectory.save()
    save_reflection(
        task_description=description,
        error="max_iterations",
        reflection=f"Failed after {max_iterations} iterations. Last state: {state.value}. "
                   f"Last tool: {last_tool}. Tokens used: {get_token_usage().get('total', 0):,}. "
                   f"Consider: different approach, fewer file reads, more targeted searches."
    )
    return _finalize_result(
        False,
        last_output,
        max_iterations,
        messages,
        error="max_iterations",
    )