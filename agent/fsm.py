"""Finite State Machine for agent control flow."""

from enum import Enum
from pathlib import Path


class AgentState(Enum):
    PLAN      = "plan"       # understand the task, search for relevant code
    EXPLORE   = "explore"    # read specific files in detail
    IMPLEMENT = "implement"  # write the first solution
    VERIFY    = "verify"     # run tests
    FIX       = "fix"        # fix after test failure
    DONE      = "done"       # task complete


# Tools available in each state
STATE_TOOLS = {
    AgentState.PLAN: [
        "explore_repo",
        "search_codebase",
        "search_and_read",
        "find_files",
        "file_outline",
        "git_log",
        "write_scratchpad",
        "read_scratchpad",
    ],
    AgentState.EXPLORE: [
        "list_files",
        "view_directory",
        "find_files",
        "read_file",
        "read_files",
        "view_file_range",
        "search_code",
        "search_codebase",
        "search_and_read",
        "explore_repo",
        "file_outline",
        "get_function",
        "run_tests",
        "run_command",
        "git_log",
        "git_status",
        "write_scratchpad",
        "read_scratchpad",
        "request_transition",
        "generate_test",
    ],
    AgentState.IMPLEMENT: [
        "write_file",
        "read_file",
        "read_files",
        "view_file_range",
        "file_outline",
        "get_function",
        "insert_at_line",
        "create_directory",
        "run_code",
        "run_tests",
        "run_command",
        "write_scratchpad",
        "read_scratchpad",
        "report_confidence",
    ],
    AgentState.VERIFY: [
        "run_tests",
        "run_code",
        "run_command",
        "git_diff",
        "git_status",
        "write_scratchpad",
        "read_scratchpad",
    ],
    AgentState.FIX: [
        "str_replace",
        "edit_and_verify",
        "edit_files",
        "search_and_replace_all",
        "read_file",
        "read_files",
        "view_file_range",
        "search_code",
        "search_codebase",
        "search_and_read",
        "find_files",
        "file_outline",
        "get_function",
        "write_file",
        "insert_at_line",
        "delete_lines",
        "run_tests",
        "run_command",
        "git_diff",
        "git_checkout_file",
        "write_scratchpad",
        "read_scratchpad",
        "request_transition",
        "report_confidence",
        "generate_test",
    ],
    AgentState.DONE: [
        "git_commit",
    ],
}


def detect_initial_state(workspace: Path) -> AgentState:
    """
    Detect the right starting state based on workspace contents.

    - No solution.py + large codebase  → PLAN
    - No solution.py + small codebase  → EXPLORE
    - Stub only (pass)                 → IMPLEMENT
    - Real code present                → VERIFY
    """
    solution = workspace / "solution.py"

    if not solution.exists():
        # Count Python files to determine codebase size
        py_files = list(workspace.rglob("*.py"))
        py_count = sum(1 for f in py_files if ".git" not in f.parts)
        if py_count > 10:
            return AgentState.PLAN
        return AgentState.EXPLORE

    content = solution.read_text(encoding="utf-8")

    # Parse real code lines — ignore docstrings, comments, blanks
    code_lines = []
    in_docstring = False
    for line in content.splitlines():
        stripped = line.strip()
        if '"""' in stripped or "'''" in stripped:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped and not stripped.startswith("#"):
            code_lines.append(stripped)

    real_code = [
        l for l in code_lines
        if l != "pass"
        and not l.startswith("def ")
        and not l.startswith("class ")
        and not l.startswith("from ")
        and not l.startswith("import ")
        and not l.startswith("return")
    ]

    if not real_code:
        return AgentState.IMPLEMENT

    if len(real_code) > 2:
        return AgentState.VERIFY

    return AgentState.EXPLORE


def transition(
    current: AgentState,
    last_tool: str | None,
    last_result: str,
    iteration: int,
) -> AgentState:
    """Decide next state based on what just happened."""

    # LLM-requested state transition
    if last_tool == "request_transition" and "[STATE_TRANSITION:" in last_result:
        import re
        match = re.search(r'\[STATE_TRANSITION:(\w+)\]', last_result)
        if match:
            target = match.group(1).lower()
            state_map = {
                "plan": AgentState.PLAN,
                "explore": AgentState.EXPLORE,
                "implement": AgentState.IMPLEMENT,
                "verify": AgentState.VERIFY,
                "fix": AgentState.FIX,
            }
            if target in state_map:
                return state_map[target]
            
    # Tests ran — decide based on result
    if last_tool == "run_tests":
        if "[TEST_RESULT:PASS]" in last_result:
            return AgentState.DONE
        else:
            return AgentState.FIX

    # Wrote code — go verify
    if last_tool == "write_file" and current in (
        AgentState.PLAN, AgentState.EXPLORE,
        AgentState.IMPLEMENT, AgentState.FIX,
    ):
        return AgentState.VERIFY

    # Made a surgical fix — go verify
    if last_tool in ("str_replace", "edit_and_verify", "edit_files", "search_and_replace_all"):
        return AgentState.VERIFY

    # PLAN → EXPLORE after first search/exploration
    if current == AgentState.PLAN:
        if last_tool in (
            "explore_repo", "search_codebase", "search_and_read",
            "find_files", "file_outline", "git_log",
        ):
            return AgentState.EXPLORE

    # EXPLORE → IMPLEMENT after reading enough (minimum 3 iterations)
    if current == AgentState.EXPLORE and iteration >= 3:
        if last_tool in (
            "read_file", "read_files", "view_file_range",
            "search_code", "search_codebase", "search_and_read",
            "list_files", "find_files", "view_directory",
            "file_outline", "get_function",
            "git_log", "git_status",
        ):
            return AgentState.IMPLEMENT

    return current


def get_tools_for_state(
    state: AgentState,
    all_schemas: list[dict],
    has_written: bool,
) -> list[dict]:
    """Filter tool schemas to only those valid in current state."""
    allowed = STATE_TOOLS[state].copy()

    # Block write_file in IMPLEMENT only after first write
    if state == AgentState.IMPLEMENT and has_written:
        allowed = [t for t in allowed if t != "write_file"]

    return [
        t for t in all_schemas
        if t["function"]["name"] in allowed
    ]