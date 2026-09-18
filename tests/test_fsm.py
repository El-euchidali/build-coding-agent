"""FSM smoke tests: transition-map consistency, transitions, initial-state detection."""

import pytest

from agent.fsm import (
    STATE_TOOLS,
    AgentState,
    detect_initial_state,
    get_tools_for_state,
    transition,
)
from agent.tools import TOOL_SCHEMAS, request_transition

ALL_TOOL_NAMES = {schema["function"]["name"] for schema in TOOL_SCHEMAS}


# -- 1. Transition-map consistency --------------------------------------------

def test_every_state_has_a_tool_list():
    assert set(STATE_TOOLS) == set(AgentState)


@pytest.mark.parametrize("state", list(AgentState))
def test_state_tools_reference_real_tools(state):
    """Catches typos: a name in STATE_TOOLS with no matching schema is dead config."""
    unknown = sorted(set(STATE_TOOLS[state]) - ALL_TOOL_NAMES)
    assert not unknown, f"{state.name} lists tools with no schema: {unknown}"


@pytest.mark.parametrize("state", list(AgentState))
def test_state_tool_lists_have_no_duplicates(state):
    names = STATE_TOOLS[state]
    assert len(names) == len(set(names)), f"{state.name} repeats a tool"


@pytest.mark.parametrize("state", list(AgentState))
def test_get_tools_for_state_returns_only_allowed_schemas(state):
    returned = {s["function"]["name"]
                for s in get_tools_for_state(state, TOOL_SCHEMAS, False)}
    assert returned <= set(STATE_TOOLS[state])


def test_write_file_is_withdrawn_after_first_write_in_implement():
    before = {s["function"]["name"]
              for s in get_tools_for_state(AgentState.IMPLEMENT, TOOL_SCHEMAS, False)}
    after = {s["function"]["name"]
             for s in get_tools_for_state(AgentState.IMPLEMENT, TOOL_SCHEMAS, True)}
    assert "write_file" in before
    assert "write_file" not in after
    assert before - after == {"write_file"}


def test_get_tools_for_state_does_not_mutate_the_map():
    snapshot = list(STATE_TOOLS[AgentState.IMPLEMENT])
    get_tools_for_state(AgentState.IMPLEMENT, TOOL_SCHEMAS, True)
    assert STATE_TOOLS[AgentState.IMPLEMENT] == snapshot


# -- 1b. request_transition: valid targets drive the FSM, invalid ones rejected --

VALID_TARGETS = ["plan", "explore", "implement", "verify", "fix"]


@pytest.mark.parametrize("target", VALID_TARGETS)
def test_request_transition_accepts_valid_state(workspace, target):
    result = request_transition(workspace, target, "because")
    assert result.startswith("[STATE_TRANSITION:" + target.upper() + "]")


@pytest.mark.parametrize("target", VALID_TARGETS)
def test_valid_request_transition_moves_the_fsm(workspace, target):
    result = request_transition(workspace, target, "because")
    landed = transition(AgentState.EXPLORE, "request_transition", result, iteration=1)
    assert landed is AgentState[target.upper()]


@pytest.mark.parametrize("bad", ["done", "DONE", "banana", "", "implement ", "fix;plan"])
def test_request_transition_rejects_invalid_state(workspace, bad):
    result = request_transition(workspace, bad, "because")
    assert result.startswith("Error: invalid state")
    assert "[STATE_TRANSITION:" not in result


@pytest.mark.parametrize("bad", ["banana", "done", ""])
def test_rejected_transition_leaves_the_state_unchanged(workspace, bad):
    result = request_transition(workspace, bad, "because")
    assert transition(AgentState.EXPLORE, "request_transition", result, 1) is AgentState.EXPLORE


def test_malformed_transition_marker_is_ignored():
    """A marker naming an unknown state must not move the FSM."""
    marker = "[STATE_TRANSITION:BANANA] Reason: x"
    assert transition(AgentState.EXPLORE, "request_transition", marker, 1) is AgentState.EXPLORE


# -- 1c. A few core transitions -----------------------------------------------

def test_passing_tests_go_to_done():
    result = "[TEST_RESULT:PASS] passed=1 failed=0"
    assert transition(AgentState.VERIFY, "run_tests", result, 5) is AgentState.DONE


def test_failing_tests_go_to_fix():
    result = "[TEST_RESULT:FAIL] passed=0 failed=1"
    assert transition(AgentState.VERIFY, "run_tests", result, 5) is AgentState.FIX


@pytest.mark.parametrize(
    "tool", ["str_replace", "edit_and_verify", "edit_files", "search_and_replace_all"]
)
def test_surgical_edits_go_to_verify(tool):
    assert transition(AgentState.FIX, tool, "[EDIT:OK] Replaced 1 exact match", 4) is AgentState.VERIFY


def test_unrecognised_tool_keeps_the_current_state():
    assert transition(AgentState.EXPLORE, "read_file", "contents", iteration=1) is AgentState.EXPLORE


# -- 2. detect_initial_state on real directories ------------------------------

STUB = "def solve(n):\n    pass\n"

REAL = (
    "def solve(n):\n"
    "    total = 0\n"
    "    for i in range(n):\n"
    "        total += i\n"
    "    return total\n"
)


def test_large_codebase_without_solution_starts_in_plan(tmp_path):
    for i in range(11):
        (tmp_path / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    assert not (tmp_path / "solution.py").exists()
    assert detect_initial_state(tmp_path) is AgentState.PLAN


def test_small_codebase_without_solution_starts_in_explore(tmp_path):
    for i in range(3):
        (tmp_path / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.EXPLORE


def test_empty_workspace_starts_in_explore(tmp_path):
    assert detect_initial_state(tmp_path) is AgentState.EXPLORE


def test_plan_threshold_is_more_than_ten_files(tmp_path):
    """Exactly 10 is still EXPLORE; the 11th tips it into PLAN."""
    for i in range(10):
        (tmp_path / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.EXPLORE
    (tmp_path / "module_10.py").write_text("x = 1\n", encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.PLAN


def test_nested_python_files_count_towards_the_threshold(tmp_path):
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    for i in range(11):
        (nested / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.PLAN


def test_stub_solution_starts_in_implement(tmp_path):
    (tmp_path / "solution.py").write_text(STUB, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.IMPLEMENT


def test_stub_solution_wins_over_a_large_codebase(tmp_path):
    """solution.py is checked first -- file count must not override it."""
    for i in range(11):
        (tmp_path / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "solution.py").write_text(STUB, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.IMPLEMENT


def test_imports_and_comments_alone_still_count_as_a_stub(tmp_path):
    source = (
        "import math\n"
        "from typing import List\n"
        "\n"
        "# TODO: implement\n"
        "def solve(n):\n"
        "    pass\n"
    )
    (tmp_path / "solution.py").write_text(source, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.IMPLEMENT


def test_real_solution_starts_in_verify(tmp_path):
    (tmp_path / "solution.py").write_text(REAL, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.VERIFY


def test_real_solution_wins_over_a_large_codebase(tmp_path):
    for i in range(11):
        (tmp_path / f"module_{i}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "solution.py").write_text(REAL, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.VERIFY


def test_real_solution_with_a_single_line_docstring_is_verify(tmp_path):
    source = (
        "def solve(n):\n"
        '    """Sum the first n integers."""\n'
        "    total = 0\n"
        "    for i in range(n):\n"
        "        total += i\n"
        "    return total\n"
    )
    (tmp_path / "solution.py").write_text(source, encoding="utf-8")
    assert detect_initial_state(tmp_path) is AgentState.VERIFY
