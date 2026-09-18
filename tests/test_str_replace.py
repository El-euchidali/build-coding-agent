"""str_replace smoke tests: exact match, whitespace tolerance, closest-match hint."""

import pytest

from agent.tools import str_replace

SOURCE = (
    "def classify(x):\n"
    "    if x > 0:\n"
    "        return 1\n"
    "    return 0\n"
)


@pytest.fixture
def solution(workspace):
    path = workspace / "solution.py"
    path.write_text(SOURCE, encoding="utf-8")
    return path


# -- Exact match --------------------------------------------------------------

def test_exact_match_replaces_once(workspace, solution):
    result = str_replace(workspace, "solution.py", "        return 1\n", "        return 2\n")
    assert result.startswith("[EDIT:OK]")
    assert "exact match" in result
    assert "        return 2\n" in solution.read_text(encoding="utf-8")


def test_ambiguous_old_str_is_refused_and_changes_nothing(workspace):
    path = workspace / "dup.py"
    path.write_text("a = 1\nb = 2\na = 1\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = str_replace(workspace, "dup.py", "a = 1", "a = 3")
    assert result.startswith("[EDIT:AMBIGUOUS]")
    assert "2 times" in result
    assert path.read_text(encoding="utf-8") == before


# -- Whitespace tolerance -----------------------------------------------------

def test_wrong_indentation_still_matches(workspace, solution):
    """old_str is dedented relative to the file; the match should still land."""
    old = "if x > 0:\n    return 1"
    new = "if x > 0:\n    return 99"
    result = str_replace(workspace, "solution.py", old, new)
    assert result.startswith("[EDIT:OK]")
    assert "indentation normalized" in result


def test_whitespace_tolerant_edit_preserves_file_indentation(workspace, solution):
    """The rewritten lines must keep the file's own indentation, not old_str's."""
    str_replace(workspace, "solution.py", "if x > 0:\n    return 1",
                "if x > 0:\n    return 99")
    lines = solution.read_text(encoding="utf-8").splitlines()
    assert "    if x > 0:" in lines
    assert "        return 99" in lines


def test_extra_trailing_whitespace_is_tolerated(workspace, solution):
    result = str_replace(workspace, "solution.py", "        return 1   ", "        return 7")
    assert result.startswith("[EDIT:OK]")
    assert "return 7" in solution.read_text(encoding="utf-8")


def test_whitespace_tolerant_match_is_refused_when_ambiguous(workspace):
    path = workspace / "twice.py"
    path.write_text("def a():\n    return 1\n\ndef b():\n        return 1\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    result = str_replace(workspace, "twice.py", "return 1", "return 2")
    assert result.startswith("[EDIT:AMBIGUOUS]")
    assert path.read_text(encoding="utf-8") == before


# -- Closest-match hint on failure --------------------------------------------

def test_failed_match_returns_closest_lines_hint(workspace):
    path = workspace / "calc.py"
    path.write_text(
        "def run(x):\n"
        "    total = compute_value(x)\n"
        "    return total\n",
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    # One-character typo: close enough to be worth a hint, not close enough to match.
    result = str_replace(workspace, "calc.py", "    total = compute_valu(x)", "    total = 0")
    assert result.startswith("[EDIT:FAILED]")
    assert "Closest lines in the file" in result
    assert "compute_value(x)" in result
    assert path.read_text(encoding="utf-8") == before


def test_hint_includes_line_numbers(workspace):
    path = workspace / "calc.py"
    path.write_text("alpha = 1\nbeta = 2\ngamma = 3\n", encoding="utf-8")
    result = str_replace(workspace, "calc.py", "beta = 22222", "beta = 4")
    assert result.startswith("[EDIT:FAILED]")
    assert "2 | beta = 2" in result


def test_wholly_unrelated_old_str_gets_no_false_hint(workspace):
    path = workspace / "calc.py"
    path.write_text("alpha = 1\n", encoding="utf-8")
    result = str_replace(workspace, "calc.py", "zzzzzzzzzzzzzzzzzzzz", "q")
    assert result.startswith("[EDIT:FAILED]")
    assert "No similar lines found" in result


# -- Guard rails --------------------------------------------------------------

def test_missing_file_is_reported(workspace):
    result = str_replace(workspace, "nope.py", "a", "b")
    assert result.startswith("[EDIT:FAILED]")
    assert "not found" in result


def test_empty_old_str_is_refused(workspace, solution):
    result = str_replace(workspace, "solution.py", "", "b")
    assert result.startswith("[EDIT:FAILED]")
    assert "empty" in result


def test_identical_old_and_new_is_a_noop(workspace, solution):
    before = solution.read_text(encoding="utf-8")
    result = str_replace(workspace, "solution.py", "        return 1", "        return 1")
    assert result.startswith("[EDIT:NOOP]")
    assert solution.read_text(encoding="utf-8") == before
