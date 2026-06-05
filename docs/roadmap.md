# Coding Agent — Improvement Roadmap

## Current Status
- HumanEval: 96.3% (158/164)
- SWE-bench Verified: 1/1 confirmed solve (astropy__astropy-12907)
- Tools: 24 total
- Architecture: FSM + RAG + FileSystem + Conversational mode

---

## Phase 1 — Before SWE-bench Batch Run (NOW)

| # | Improvement | Status | Files Changed |
|---|---|---|---|
| 1 | **Structured test results** — `run_tests` returns JSON `{success, failed_tests, output}`, FSM checks boolean not string | ⬜ TODO | `execution/sandbox.py`, `agent/fsm.py` |
| 4 | **Command whitelist** — replace blacklist with allowed commands, block everything else | ⬜ TODO | `agent/tools.py` |
| 5 | **Trajectory logging** — save every run as JSON: state, tool, input, output, tokens per step | ⬜ TODO | `agent/agent.py`, NEW `agent/trajectory.py` |
| 13 | **Timeout escalation** — if `run_tests` times out, retry with doubled timeout | ⬜ TODO | `execution/sandbox.py` |
| 15 | **Permission system** — ask user before dangerous actions (delete files, run unknown commands, git commit) | ⬜ TODO | `agent/agent.py`, `agent/tools.py` |

## Phase 2 — Agent Intelligence

| # | Improvement | Status | Files Changed |
|---|---|---|---|
| 2 | **LLM-driven state transitions** — `request_transition(target_state)` tool | ⬜ TODO | `agent/tools.py`, `agent/fsm.py` |
| 3 | **Semantic scratchpad** — `write_scratchpad` / `read_scratchpad` tools surviving context trim | ⬜ TODO | `agent/tools.py`, `agent/agent.py` |
| 9 | **Token budget** — max token limit per task, force strategy change when approaching limit | ⬜ TODO | `agent/agent.py`, `agent/llm.py` |
| 12 | **Diff-based context** — send git diff instead of full file content after edits | ⬜ TODO | `agent/agent.py` |

## Phase 3 — Learning & Optimization

| # | Improvement | Status | Files Changed |
|---|---|---|---|
| 10 | **Reflexion** — store failure reflections in ChromaDB, retrieve for similar tasks | ⬜ TODO | `agent/rag.py`, `agent/agent.py` |
| 14 | **Confidence scoring** — agent rates confidence 1-10 before committing | ⬜ TODO | `agent/agent.py`, `agent/prompts.py` |
| 6 | **Parallel read tools** — multiple read-only tools per turn, one write per turn | ⬜ TODO | `agent/agent.py` |

## Phase 4 — Advanced Features

| # | Improvement | Status | Files Changed |
|---|---|---|---|
| 7 | **Two-step RAG** — find relevant files first, then chunks within those files | ⬜ TODO | `agent/rag.py` |
| 8 | **Automated test generation** — agent writes reproduction script before fixing | ⬜ TODO | `agent/prompts.py`, `agent/tools.py` |
| 11 | **Multi-model routing** — cheap model for exploration, expensive model for implementation | ⬜ TODO | `agent/llm.py`, `agent/agent.py` |

---

## Completed Features

| Feature | Date | Commit |
|---|---|---|
| Finite State Machine (6 states) | 2026-06-01 | 79ebb12 |
| HumanEval 96.3% (158/164) | 2026-06-01 | 4e8fa7c |
| Extended tools (8→24) | 2026-06-01 | a931554 |
| RAG via ChromaDB | 2026-06-01 | a12ed70 |
| SWE-bench runner + first solve | 2026-06-02 | a12ed70 |
| PLAN state + batch tools | 2026-06-02 | merged |
| Loop detection + blocked-call recovery | 2026-06-02 | merged |
| FileSystem foundation (git ls-files) | 2026-06-02 | merged |
| Conversational mode (handle_message) | 2026-06-02 | merged |
| CLI chat interface | 2026-06-02 | merged |
| Web UI (FastAPI + SSE) | 2026-06-02 | merged |
