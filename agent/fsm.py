"""Finite State Machine for agent control flow."""

from enum import Enum
from pathlib import Path


class AgentState(Enum):
    EXPLORE   = "explore"    # understand the codebase
    IMPLEMENT = "implement"  # write the first solution
    VERIFY    = "verify"     # run tests
    FIX       = "fix"        # fix after test failure
    DONE      = "done"       # task complete


STATE_TOOLS = {
    AgentState.EXPLORE: [
        "list_files",
        "view_directory",
        "find_files",
        "read_file",
        "view_file_range",
        "search_code",
        "git_log",
        "git_status",
    ],
    AgentState.IMPLEMENT: [
        "write_file",
        "read_file",
        "view_file_range",
        "insert_at_line",
        "create_directory",
    ],
    AgentState.VERIFY: [
        "run_tests",
        "run_code",
        "run_command",
        "git_diff",
        "git_status",
    ],
    AgentState.FIX: [
        "str_replace",
        "read_file",
        "view_file_range",
        "search_code",
        "find_files",
        "write_file",
        "insert_at_line",
        "delete_lines",
        "git_diff",
        "git_checkout_file",
    ],
    AgentState.DONE: [
        "git_commit",
    ],
}


def detect_initial_state(workspace: Path) -> AgentState:
    """
    Detect the right starting state based on workspace contents.

    - No solution.py        → EXPLORE
    - Stub only (pass)      → IMPLEMENT
    - Real code present     → VERIFY
    """
    solution = workspace / "solution.py"

    if not solution.exists():
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

    # Tests ran — decide based on result
    if last_tool == "run_tests":
        if "* ALL TESTS PASSED" in last_result:
            return AgentState.DONE
        else:
            return AgentState.FIX

    # Wrote or fixed code — go verify
    if last_tool == "write_file" and current in (AgentState.IMPLEMENT, AgentState.FIX):
        return AgentState.VERIFY

    if last_tool == "str_replace":
        return AgentState.VERIFY

    # Explored enough — move to implement
    if current == AgentState.EXPLORE and iteration >= 2:
        if last_tool in ("read_file", "view_file_range", "search_code", "list_files"):
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