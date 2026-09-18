"""Loop-detection smoke tests: three identical tool results mean the agent is stuck."""

import pytest

from agent.agent import _detect_loop, _normalize_tool_result_for_loop


def tool(content):
    return {"role": "tool", "content": content}


def assistant(content="thinking"):
    return {"role": "assistant", "content": content}


# -- The core case: three identical results ----------------------------------

def test_three_identical_results_is_a_loop():
    messages = [tool("same error")] * 3
    assert _detect_loop(messages) is True


def test_two_identical_results_is_not_yet_a_loop():
    assert _detect_loop([tool("same error")] * 2) is False


def test_three_different_results_is_not_a_loop():
    messages = [tool("first"), tool("second"), tool("third")]
    assert _detect_loop(messages) is False


def test_a_single_different_result_breaks_the_loop():
    messages = [tool("same"), tool("same"), tool("different")]
    assert _detect_loop(messages) is False


def test_only_the_most_recent_results_count():
    """An older run of identical results must not trigger once the agent moves on."""
    messages = [tool("stuck"), tool("stuck"), tool("stuck"), tool("progress")]
    assert _detect_loop(messages) is False


def test_loop_is_detected_at_the_tail_of_a_longer_history():
    messages = [tool("a"), tool("b")] + [tool("stuck")] * 3
    assert _detect_loop(messages) is True


def test_empty_history_is_not_a_loop():
    assert _detect_loop([]) is False


def test_non_tool_messages_are_ignored():
    messages = [
        tool("stuck"), assistant(), tool("stuck"), assistant(), tool("stuck"),
    ]
    assert _detect_loop(messages) is True


@pytest.mark.parametrize("window", [2, 3, 4])
def test_window_size_is_respected(window):
    assert _detect_loop([tool("stuck")] * window, window=window) is True
    assert _detect_loop([tool("stuck")] * (window - 1), window=window) is False


# -- Normalisation: near-identical failures collapse to the same signature ----

@pytest.mark.parametrize(
    "prefix", ["[EDIT:FAILED]", "[EDIT:AMBIGUOUS]", "[EDIT:NOOP]"]
)
def test_edit_failures_collapse_regardless_of_detail(prefix):
    """Retry spirals differ only in the hint text, so they must still read as one."""
    messages = [
        tool(prefix + " old_str not found in a.py\nClosest lines: 1 | alpha"),
        tool(prefix + " old_str not found in b.py\nClosest lines: 9 | beta"),
        tool(prefix + " old_str not found in c.py\nClosest lines: 4 | gamma"),
    ]
    assert _detect_loop(messages) is True


def test_different_edit_failure_kinds_do_not_collapse():
    messages = [
        tool("[EDIT:FAILED] x"), tool("[EDIT:AMBIGUOUS] x"), tool("[EDIT:NOOP] x"),
    ]
    assert _detect_loop(messages) is False


def test_normalisation_truncates_ordinary_results_to_a_prefix():
    assert _normalize_tool_result_for_loop("z" * 500) == "z" * 100


def test_results_sharing_a_long_prefix_collapse():
    """Two long outputs identical in their first 100 chars count as the same result."""
    base = "identical output " * 10
    messages = [tool(base + str(i)) for i in range(3)]
    assert _detect_loop(messages) is True


@pytest.mark.parametrize(
    "content,expected",
    [
        ("[EDIT:FAILED] anything at all", "[EDIT:FAILED]"),
        ("[EDIT:AMBIGUOUS] anything", "[EDIT:AMBIGUOUS]"),
        ("[EDIT:NOOP] anything", "[EDIT:NOOP]"),
        ("[EDIT:OK] Replaced 1 exact match", "[EDIT:OK] Replaced 1 exact match"),
        ("short", "short"),
    ],
)
def test_normalisation_signatures(content, expected):
    assert _normalize_tool_result_for_loop(content) == expected
