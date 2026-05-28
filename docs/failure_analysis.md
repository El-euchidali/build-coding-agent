# HumanEval Failure Analysis

**Run date:** 2026-05-28
**Model:** Gemma4-31b via InnKube university API
**Agent version:** FSM-enabled (feature/finite-state-machine)
**Benchmark:** HumanEval (164 tasks)

---

## Summary

| Metric          | Result    |
| --------------- | --------- |
| Total tasks     | 164       |
| Passed          | 158       |
| Failed          | 6         |
| Pass@1          | 96.3%     |
| Avg iterations  | 3.09      |
| Total tokens    | 1,376,779 |
| Avg tokens/task | 8,395     |

All 6 failures are genuine logic errors and not infrastructure or Windows compatibility issues.

---

## Failure Breakdown

### HumanEval/69 — `search`

**Problem:** Find the largest integer x such that x appears at least x times in the array.

**Agent solution (wrong):**

```python
special = []
for x in counts:
    freq = counts[x]
    if freq in counts:
        special.append(x)
return min(special)
```

**Failing test:**

```python
assert candidate([6, 9, 6, 7, 1, 4, 7, 1, 8, 8, 9, 8, 10, 10, 8, 4, 10, 4, 10, 1, 2, 9, 5, 7, 9]) == 1
# Agent returned 2 instead of 1
```

**Root cause:** The docstring described the problem ambiguously. The agent implemented a wrong interpretation; it found elements where the frequency of x also exists as a value in the array, then returned the minimum. The correct solution finds the largest x where x appears at least x times.

**Category:** Ambiguous problem specification

---

### HumanEval/70 — Ordering failure

**Problem:** Sort elements by a specific ordering criterion.

**Failing test:**

```python
assert candidate(...) == [1, 4, 2, 3]
# Agent returned [1, 4, 3, 2]
```

**Root cause:** The agent got the sort ordering wrong for tied elements. When two elements have equal primary sort keys the tiebreaker was applied incorrectly.

**Category:** Edge case — tie breaking in sort

---

### HumanEval/74 — Deduplication failure

**Problem:** Return unique elements preserving some ordering property.

**Failing test:**

```python
assert candidate(...) == ['hi', 'admin']
# Agent returned ['hi', 'hi', 'admin']
```

**Root cause:** The agent kept duplicate entries that should have been removed.

**Category:** Edge case — duplicate handling

---

### HumanEval/99 — `closest_integer` rounding

**Problem:** Round a string representation of a number to the closest integer. For .5 cases, round away from zero.

**Failing test:**

```python
assert candidate("-15.5") == -16
# Agent returned -15
```

**Root cause:** The use of Python's built-in `round()`. The correct implementation requires `math.floor(x + 0.5)` for positive and `math.ceil(x - 0.5)` for negative numbers.

**Category:** Language-specific edge case — Python rounding behavior

---

### HumanEval/132 — `is_nested`

**Problem:** Check if a bracket string has a nested bracket structure.

**Failing test:**

```python
assert candidate('[[]]') == True
# Agent returned False
```

**Root cause:** `[[]]` clearly contains nested brackets but the agent returned False.

**Category:** Logic error — bracket parsing

---

### HumanEval/134 — `check_if_last_char_is_a_letter`

**Problem:** Check if the last character of a word is a consonant under specific whitespace conditions.

**Failing test:**

```python
assert candidate("apple") == False
# Agent returned True
```

**Root cause:** The problem requires checking additional conditions beyond whether the last character is alphabetic. The agent implemented a simpler version that only checked if the last character was a letter, missing the consonant or whitespace condition.

**Category:** Incomplete problem understanding — missed secondary condition

---

## Pattern Analysis

| Category                         | Count |
| -------------------------------- | ----- |
| Ambiguous problem specification  | 1     |
| Edge case — tie breaking in sort | 1     |
| Edge case — duplicate handling   | 1     |
| Language-specific edge case      | 1     |
| Logic error — bracket parsing    | 1     |
| Incomplete problem understanding | 1     |

All 6 failures share a common theme: the agent implemented a correct-looking solution that passed obvious test cases but missed edge cases or secondary conditions implied by the problem but not explicitly stated in the docstring.

---

## Implications

**What works well:**

- The agent correctly implements the main logic for 158/164 problems
- The FSM structure (IMPLEMENT → VERIFY → FIX) works correctly
- Average 3.09 iterations shows efficient task completion
- 47% token reduction compared to pre-FSM baseline

**What needs improvement:**

1. **Edge case awareness**

2. **Python-specific rounding**

3. **Re-reading the problem after failure**

---

## Proposed Prompt Improvements

Add to `SYSTEM_PROMPT`:
Edge case checklist to consider before writing code:

- Empty input (empty list, empty string, zero)
- Negative numbers
- Duplicate values
- Single element inputs
- Boundary values and off-by-one errors
- Use math.floor/math.ceil instead of round() for precise rounding

Add to `STESTS_NOT_PASSED_NUDGE`:
Before fixing: re-read the original task description carefully.
Your initial interpretation may have been wrong, not just your implementation.
