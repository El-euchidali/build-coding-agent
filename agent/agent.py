import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.llm import chat, get_token_usage, reset_token_counter, trim_context, set_system_section
from agent.prompts import SYSTEM_PROMPT, TESTS_NOT_PASSED_NUDGE
from agent.tools import TOOL_SCHEMAS, execute_tool, parse_tool_call_fallback
from agent.failure import classify_failure, tests_passed_in_history
from agent.rag import CodebaseIndex, set_current_index
from agent.filesystem import FileSystem
from agent.fsm import AgentState, STATE_TOOLS, get_tools_for_state, transition, detect_initial_state
from agent.trajectory import Trajectory
from agent.tools import reset_scratchpad, get_scratchpad
from agent.reflexion import save_reflection, get_past_reflections


MAX_NUDGES_WITHOUT_TESTS = 3

_READ_TOOLS = frozenset({
    "list_files", "view_directory", "find_files", "read_file", "read_files",
    "view_file_range", "search_code", "search_codebase", "search_and_read",
    "explore_repo", "file_outline", "get_function", "git_status", "git_diff",
    "git_log", "read_scratchpad",
})
_DEP_ERROR_PATTERNS = ["ModuleNotFoundError", "ImportError", "No module named",
                        "could not determine", "broken installation"]

# Surgical edits to existing files. Writing a brand-new file (e.g. a repro
# script) does NOT count — that keeps the agent from "satisfying" the
# decisiveness nudge without actually fixing source.
_EDIT_TOOLS = frozenset({
    "str_replace", "edit_and_verify", "edit_files",
    "search_and_replace_all", "insert_at_line", "delete_lines",
})

# Turns without a successful source edit before we nudge the agent to commit.
_EDIT_NUDGE_THRESHOLD = 6


def _edit_succeeded(tool: str, result: str) -> bool:
    """True only when an edit tool actually changed a file."""
    if tool in ("str_replace", "edit_and_verify"):
        return result.startswith("[EDIT:OK]")
    if tool == "edit_files":
        return "[EDIT:OK]" in result and "[EDIT:NOOP]" not in result
    if tool == "search_and_replace_all":
        return result.startswith("Replaced in")
    if tool == "insert_at_line":
        return result.startswith("Inserted")
    if tool == "delete_lines":
        return result.startswith("Deleted")
    return False


def _is_env_failure(tool: str, result: str) -> bool:
    """Detect tests/commands that failed due to a broken environment or deps."""
    if tool == "run_tests":
        return (
            any(p in result for p in _DEP_ERROR_PATTERNS)
            or "[TEST_RESULT:FAIL] passed=0 failed=0 errors=0" in result
        )
    if tool in ("run_command", "run_code"):
        return (
            any(p in result for p in _DEP_ERROR_PATTERNS)
            or "subprocess-exited-with" in result
            or "build_ext" in result
            or "build editable: finished with status 'error'" in result
        )
    return False


def _git_diff_has_changes(result: str) -> bool:
    """True when a git_diff tool result shows an actual patch."""
    text = result.strip()
    if not text or text in ("(no changes)", "(no output)"):
        return False
    return "diff --git" in text or text.startswith("--- ")


def _workspace_has_patch(workspace: Path) -> bool:
    """True when the workspace has unstaged changes to tracked files."""
    diff = FileSystem(workspace).git("diff")
    return _git_diff_has_changes(diff or "")


def _parse_confidence_score(result: str) -> int | None:
    import re
    match = re.search(r"Confidence (\d+)/10", result)
    return int(match.group(1)) if match else None


def _can_finish_without_tests(
    edit_made: bool,
    env_cannot_verify: bool,
    workspace: Path,
) -> bool:
    """
    Allow DONE when tests cannot run but a real source patch exists.

    Uses git diff on the workspace as the source of truth — not tool
    messages alone (edit_and_verify can report [EDIT:OK] for no-op replaces).
    """
    return edit_made and env_cannot_verify and _workspace_has_patch(workspace)


_SKIP_WRITE_MSG = "Skipped: only one write tool allowed per turn. Call this tool again next turn."

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


def _plan_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], list[dict]]:
    """Parallel reads allowed; at most one write per turn."""
    if len(tool_calls) <= 1:
        return tool_calls, []
    reads = [tc for tc in tool_calls if tc["name"] in _READ_TOOLS]
    writes = [tc for tc in tool_calls if tc["name"] not in _READ_TOOLS]
    if writes:
        return reads + writes[:1], writes[1:]
    return reads, []


def _parse_tool_args(tc: dict) -> dict:
    try:
        return json.loads(tc["arguments"])
    except json.JSONDecodeError:
        return {}


def _execute_tool_turn(
    tool_calls: list[dict],
    workspace: Path,
) -> tuple[list[dict], list[ToolCall], dict[str, float]]:
    """
    Execute tool calls for one turn. Every tool_call id gets a matching tool message.
    Returns (tool messages in original order, history for executed calls, elapsed by id).
    """
    to_execute, skipped = _plan_tool_calls(tool_calls)
    skipped_ids = {tc["id"] for tc in skipped}
    results_by_id: dict[str, str] = {}
    elapsed_by_id: dict[str, float] = {}
    history: list[ToolCall] = []

    for tc in to_execute:
        args = _parse_tool_args(tc)
        tool_start = time.time()
        result = execute_tool(tc["name"], args, workspace)
        tool_elapsed = round(time.time() - tool_start, 1)
        results_by_id[tc["id"]] = result
        elapsed_by_id[tc["id"]] = tool_elapsed
        history.append(ToolCall(
            name=tc["name"],
            args=args,
            result=result[:800],
            elapsed=tool_elapsed,
        ))

    tool_messages = []
    for tc in tool_calls:
        if tc["id"] in skipped_ids:
            content = _SKIP_WRITE_MSG
        else:
            content = results_by_id[tc["id"]]
        tool_messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": content,
        })

    return tool_messages, history, elapsed_by_id


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

        tool_messages, round_history, _ = _execute_tool_turn(tool_calls, workspace)
        tool_history.extend(round_history)
        messages.extend(tool_messages)

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
        messages = set_system_section(messages, "## Your Scratchpad Notes", pad_content)

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

        if message.content:
            yield {
                "type": "thinking",
                "content": message.content,
            }

        tool_messages, _, elapsed_by_id = _execute_tool_turn(tool_calls, workspace)
        for tc, msg in zip(tool_calls, tool_messages):
            yield {
                "type": "tool_call",
                "tool": tc["name"],
                "args": _parse_tool_args(tc),
                "result": msg["content"][:800],
                "elapsed": elapsed,
                "tool_elapsed": elapsed_by_id.get(tc["id"], 0),
                "round": round_num + 1,
                "tokens": get_token_usage(),
            }
        messages.extend(tool_messages)

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
    system_prompt: str | None = None,
    task_id: str = "interactive",
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
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Task:\n{description}\n\nWorkspace: {workspace.resolve()}",
        },
    ]
    
    # Inject past reflections if available
    past = get_past_reflections(description)
    if past:
        messages = set_system_section(messages, "## Lessons from Past Failures", past)

    last_output = ""
    nudge_count = 0
    state = detect_initial_state(workspace)
    last_tool: str | None = None
    last_result: str = ""
    has_written = False
    blocked_count = 0
    dep_fail_count = 0
    iters_since_edit = 0
    edit_made = False
    env_cannot_verify = False
    patch_verified = False

    trajectory = Trajectory(task_id=task_id)

    for iteration in range(1, max_iterations + 1):
        messages = trim_context(messages)
        # Inject scratchpad so it survives context trimming
        scratchpad = get_scratchpad()
        if scratchpad:
            pad_content = "## Your Scratchpad Notes\n"
            for key, value in scratchpad.items():
                pad_content += f"### {key}\n{value}\n\n"
            messages = set_system_section(messages, "## Your Scratchpad Notes", pad_content)

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

        # Use light model for exploration, full model for implementation/fixing.
        # Low temperature for editing states reduces run-to-run variance.
        use_light = state in (AgentState.PLAN, AgentState.EXPLORE)
        temperature = 0.0 if state in (AgentState.IMPLEMENT, AgentState.FIX) else None
        message = chat(messages, tools=tools, use_light=use_light, temperature=temperature)
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

        to_execute, skipped = _plan_tool_calls(tool_calls)
        skipped_ids = {tc["id"] for tc in skipped}
        results_by_id: dict[str, str] = {}
        pending_nudges: list[str] = []

        for tc in to_execute:
            args = _parse_tool_args(tc)
            last_tool = tc["name"]
            last_result = execute_tool(last_tool, args, workspace)
            results_by_id[tc["id"]] = last_result
            blocked_count = 0

            if last_tool == "write_file":
                has_written = True

            if last_tool in _EDIT_TOOLS and _edit_succeeded(last_tool, last_result):
                edit_made = True
                iters_since_edit = 0

            if last_tool == "git_diff" and _git_diff_has_changes(last_result):
                patch_verified = True
            elif last_tool == "git_diff" and edit_made and not _workspace_has_patch(workspace):
                pending_nudges.append(
                    "git_diff shows no changes to tracked source files. "
                    "Your last edit may not have applied — if you saw [EDIT:FAILED] or "
                    "[EDIT:NOOP], copy the exact lines and retry str_replace with a real change."
                )

            if last_tool == "report_confidence":
                score = _parse_confidence_score(last_result)
                if score is not None and score >= 8 and edit_made and _workspace_has_patch(workspace):
                    patch_verified = True

            trajectory.log_step(
                iteration=iteration, state=state.value, tool=last_tool,
                args=args, result=last_result, tokens=get_token_usage(),
            )
            # Count tests AND failed build/install commands toward the dep cap
            if _is_env_failure(last_tool, last_result):
                dep_fail_count += 1
                if dep_fail_count >= 2:
                    env_cannot_verify = True
                    pending_nudges.append(
                        "The environment cannot run tests or installs (broken config or missing dependencies). "
                        "STOP running tests and pip/build commands. Apply your fix with str_replace, "
                        "verify with git_diff, then stop — a correct patch is enough when tests cannot run."
                    )

        for tc in tool_calls:
            content = _SKIP_WRITE_MSG if tc["id"] in skipped_ids else results_by_id[tc["id"]]
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": content,
            })

        if to_execute:
            last_tool = to_execute[-1]["name"]
            last_result = results_by_id[to_execute[-1]["id"]]
        if skipped:
            last_result = last_result + (
                f"\nNote: Skipped write tools (one per turn): "
                f"{', '.join(tc['name'] for tc in skipped)}."
            )

        for nudge in pending_nudges:
            messages.append({"role": "user", "content": nudge})

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

        # Finish when tests cannot run but git shows a real patch in the workspace
        if _can_finish_without_tests(edit_made, env_cannot_verify, workspace):
            trajectory.log_result(success=True, tokens=get_token_usage())
            trajectory.save()
            return _finalize_result(True, last_output, iteration, messages)

        # Decisiveness: nudge if too many turns pass without a successful source
        # edit. Counts ANY non-editing turn (reads, scratchpad, commands), so
        # interleaving them no longer hides analysis paralysis.
        iters_since_edit += 1
        if iters_since_edit >= _EDIT_NUDGE_THRESHOLD and state != AgentState.DONE:
            verb = "resume editing" if edit_made else "make your first edit"
            messages.append({
                "role": "user",
                "content": f"You have gone {iters_since_edit} turns without successfully editing a source file. "
                           f"STOP exploring and running commands — {verb} NOW with str_replace on the specific "
                           "file and lines. If a previous edit returned [EDIT:FAILED], copy the exact lines it "
                           "showed you. Do not write new scratch/repro files to satisfy this."
            })
            iters_since_edit = 0
        
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