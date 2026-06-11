# Progress Report: Autonomous Coding Agent

## 1. What Is Built

A coding agent that navigates codebases, finds bugs, applies fixes, and verifies solutions through tests. Not a wrapper around an LLM — a structured engineering system.

### Architecture

- **Finite State Machine** — 6 states controlling the agent's workflow:
  `PLAN → EXPLORE → IMPLEMENT → VERIFY → FIX → DONE`
  Each state restricts which tools are available, preventing the LLM from skipping steps.

- **29 tools** across 8 categories:
  - Navigation: `explore_repo`, `view_directory`, `find_files`, `list_files`
  - Reading: `read_file`, `read_files`, `view_file_range`, `file_outline`, `get_function`
  - Searching: `search_code`, `search_codebase` (semantic RAG), `search_and_read`
  - Editing: `write_file`, `str_replace`, `insert_at_line`, `delete_lines`, `create_directory`
  - Batch: `edit_and_verify`, `edit_files`, `search_and_replace_all`
  - Execution: `run_code`, `run_tests`, `run_command` (whitelisted), `generate_test`
  - Git: `git_status`, `git_diff`, `git_commit`, `git_log`, `git_checkout_file`
  - Agent: `write_scratchpad`, `read_scratchpad`, `request_transition`, `report_confidence`

- **Two interfaces:**
  - CLI terminal chat
  - Web UI with chat bubbles, file sidebar, live token counter(still basic UI)

---

## 2. Benchmark Results

### HumanEval — 96.3% (158/164)

HumanEval is a dataset of 164 Python programming problems from OpenAI. Each task requires implementing a function from a docstring specification.

- **Pass@1: 96.3%** — 158 out of 164 tasks solved on the first attempt
- **Average iterations: 2.0** — the agent writes code and passes tests in 2 turns
- **6 failures** are genuine edge cases (ambiguous specs, Python banker's rounding, complex nesting) — documented in `docs/failure_analysis.md`

### SWE-bench Verified — 1/1 confirmed

SWE-bench Verified is a curated set of 500 real GitHub issues from major Python repositories.

- **Task solved: `astropy__astropy-12907`** — a bug in the separability matrix computation for nested compound models
- **Repository: astropy** — 910 Python files, 17,854 code chunks indexed by RAG
- **Fix: one line changed** — `cright[-right.shape[0]:, -right.shape[1]:] = 1` → `= right`
- **Officially confirmed** by the SWE-bench Docker evaluator: `Instances resolved: 1, Instances unresolved: 0`

---

## 3. Key Engineering Decisions

What makes this agent different from a simple LLM wrapper:

### FSM Controller (`agent/fsm.py`)

The LLM cannot edit code during PLAN, cannot skip tests during VERIFY, cannot commit without passing tests. The FSM enforces discipline. The agent can request state transitions via `request_transition` when it needs more exploration or wants to re-plan.

### Two-Step RAG (`agent/rag.py`)

Step 1: Embed file-level summaries (imports + function names) to find relevant files. Step 2: Search for specific code chunks within those files. Built with ChromaDB and sentence-transformers. Indexes 17,854 chunks from astropy in under 2 minutes.

### Scratchpad Memory (`agent/tools.py`)

Key-value store that survives context trimming. The agent writes `write_scratchpad("bug_location", "separable.py line 245")` and reads it back after the context window is pruned. Prevents the amnesia problem we observed during multi-file tasks.

### Reflexion (`agent/reflexion.py`)

After a failed task, the agent stores a reflection: what went wrong, which approach failed, what to try differently. On future similar tasks, past reflections are injected into the system prompt. The agent learns from its own mistakes.

### Command Whitelisting (`agent/tools.py`)

Deny by default — only `python`, `pip`, `pytest`, `git`, and common shell commands are allowed. Everything else is blocked. A blacklist approach is easy to bypass; a whitelist is secure by design.

---

## 4. Token Efficiency

Token consumption across SWE-bench runs on the same task (astropy\_\_astropy-12907):

| Run   | Architecture              | Tokens  | Patch         |
| ----- | ------------------------- | ------- | ------------- |
| Run 1 | RAG, old FSM              | 768,825 | Correct patch |
| Run 3 | RAG, new FSM, batch tools | 250,535 | Correct patch |
| Run 4 | All improvements          | 211,428 | Correct patch |

**72% reduction** from Run 1 to Run 3 — same correct result, 3.6x fewer tokens.

How it works:

- **Smart context trimming** — summarizes dropped messages instead of discarding them silently. Keeps a record of files read, files edited, and errors encountered.
- **Token budget** — configurable limit (default 200k) with 80% warning. Prevents runaway API costs.
- **Parallel reads** — multiple read tools per turn, one write per turn. Exploration is 2-3x faster.
- **Dependency failure detection** — if `run_tests` fails with `ModuleNotFoundError` twice, the agent stops retrying and uses `git_diff` to verify instead. This alone saved 10 wasted iterations on astropy.
- **Trajectory logging** — every run saved as structured JSON for post-hoc analysis.

---

## 5. Self-Improvement: The Agent Analyzes Itself

I asked the agent to analyze its own codebase. Without being told what features exist, it:

1. **Read the code** — called `explore_repo`, `file_outline`, `get_function` across 24 Python files
2. **Found real weaknesses:**
   - _"The FSM transition from EXPLORE to IMPLEMENT is hardcoded at iteration 2 — too rigid for complex bugs"_
   - _"String matching for test results is brittle — if the test runner changes output format, the FSM breaks"_
   - _"The blacklist security approach is easy to bypass"_
   - _"Context trimming drops critical information — the agent forgets what it was fixing"_
3. **All four issues were real.** I used the agent to implement fixes for each one:
   - Structured test results replaced string matching
   - Command whitelist replaced blacklist
   - Scratchpad memory survives context trimming
   - LLM-driven state transitions via `request_transition`

The agent modified its own source files — `sandbox.py`, `fsm.py`, `failure.py`, `prompts.py` — through the conversational interface. Each change was verified by running the benchmark suite.

---

## 6. What is Next

| Priority | Task                                    | Status                                        |
| -------- | --------------------------------------- | --------------------------------------------- |
| 1        | Run full SWE-bench Verified (500 tasks) | Waiting for compute container                 |
| 2        | Batch Docker evaluation on laptop       | Script ready (`evaluation/batch_evaluate.py`) |

---

_This report, until point 5, was generated by the coding agent itself — analyzing its own codebase and summarizing what it found._
