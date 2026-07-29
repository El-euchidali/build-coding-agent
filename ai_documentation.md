# AI Usage Documentation — Coding Agent

**Project:** Autonomous Python Coding Agent  
**Team:** Mohamed Ali El Euchi , Haseeb Ramzan
**Period covered:** 2026-05-16 → 2026-07-30  
**Repository:** `build-coding-agent`

---

## Entry #1 - Initial Repository Scaffold and README

**Date:** 2026-05-16

**Team member(s):** Haseeb Ramzan

**AI Tool used:** ChatGPT

### Context

The project repository was created on the university GitLab. We needed a clear README that explained the intended coding-agent architecture before any agent code existed, so teammates and supervisors could understand the direction of the work.

### Prompt / Task

"Write a README for a university project that builds an autonomous Python coding agent. It should navigate codebases, fix bugs, run tests, and use an LLM API. Keep it concise, include setup steps with a venv, and leave placeholders for benchmarks we will add later."

### AI Output Summary

ChatGPT produced a structured README with sections for Overview, Setup, Usage, and Project Structure. It suggested a modular layout (`agent/`, `execution/`, `evaluation/`) that closely matched what we later implemented.

### Decision

- [x] Modified before use

### Reasoning

The suggested folder layout was useful, but the first draft assumed OpenAI as the only backend and included features we had not decided on (e.g. Docker sandboxing from day one). We rewrote the LLM section for the InnKube OpenAI-compatible endpoint and stripped speculative features. Verified against our `.env.example` plan before committing (`c66863b`, `2a0abd5`).

### Impact

Saved roughly an hour of boilerplate writing. More importantly, the AI-proposed module boundaries became the long-term project structure. We would use AI again for early scaffolding, but always strip vendor-specific assumptions.

---

## Entry #2 - Core File Tools and Context Trimming

**Date:** 2026-05-20

**Team member(s):** Mohamed Ali El Euchi, Haseeb Ramzan

**AI Tool used:** Cursor (Claude)

### Context

The agent could call an LLM but had almost no ability to inspect or edit a codebase. We needed a first tool set (`read_file`, `str_replace`, `list_files`, `view_file_range`, `search_code`) plus a way to keep conversation context from exploding across long runs.

### Prompt / Task

In Cursor: "Implement coding-agent tools with JSON schemas for an OpenAI-style tool-calling loop. Prefer exact string replacement over full-file rewrites for fixes. Also add a `trim_context` helper that drops old tool results while keeping the latest user goal and recent errors."

### AI Output Summary

Cursor generated tool schemas, Python handlers, and a context-trimming routine that summarized older assistant/tool turns. It also suggested enforcing `str_replace` for bug fixes so the model would not overwrite unrelated code.

### Decision

- [x] Modified before use

### Reasoning

Schemas and the `str_replace`-first policy were kept (commit `fcf6712`, `35c7d75`, merged `5f083b7`). Token tracking was added manually so we could measure cost per benchmark task. We rejected an AI suggestion to auto-apply edits without showing a diff in logs — we needed full trajectory visibility for evaluation.

### Impact

This became the foundation of the tool layer. Later we grew from ~5 tools to 33, but the early design decisions (exact replace, trim old results, track tokens) remained. Strong recommendation for AI on tool-schema boilerplate; weaker for safety policy — that needed human review.

---

## Entry #3 - HumanEval Runner and System Prompt Tuning

**Date:** 2026-05-21 → 2026-05-28

**Team member(s):** Haseeb Ramzan, Mohamed Ali El Euchi

**AI Tool used:** Cursor, ChatGPT

### Context

We needed a reproducible benchmark path. HumanEval (164 tasks) was chosen as the first quantitative target. Early runs failed on Windows path/process issues and weak prompts that caused the model to skip writing tests or invent APIs.

### Prompt / Task

1. "Generate a HumanEval evaluation runner that loads tasks, asks the agent to implement the function, runs the official tests, and records pass/fail plus token usage."
2. "Rewrite the system prompt so the agent must: (a) implement only the required function, (b) use the provided signature, (c) verify with tests before finishing."

### AI Output Summary

Cursor drafted `evaluation/humaneval_runner.py` and prompt revisions. ChatGPT helped debug Windows-specific subprocess and path quoting failures. Prompt iterations emphasized writing code into the expected file layout and not stopping after the first compile error.

### Decision

- [x] Modified before use

### Reasoning

The runner structure was accepted with local fixes for Windows (`30f38c8`, merged `4e8fa7c`). Prompt text from ChatGPT was too verbose and sometimes contradicted tool descriptions; we shortened it and aligned wording with actual tool names. Commit `b355a86` captured the first integrated HumanEval path; later analysis (`e5035c3`) documented 96.3% (158/164).

### Impact

Established our evaluation discipline: every architectural change is judged by HumanEval (and later SWE-bench), not vibes. AI accelerated runner scaffolding; prompt quality required several human iterations. Worth using AI, but always validate prompts against failing trajectories.

---

## Entry #4 - Finite State Machine Controller

**Date:** 2026-05-28

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Free-form tool use let the LLM jump straight to editing, skip verification, or thrash between tools. Token usage was high. We wanted a controller that restricts tools per phase: plan, explore, implement, verify, fix, done.

### Prompt / Task

"Design a finite state machine for a coding agent. States: PLAN, EXPLORE, IMPLEMENT, VERIFY, FIX, DONE. For each state list allowed tools and transition conditions. Then implement it in Python so the agent loop only exposes tools valid in the current state. Estimate how this could reduce tokens on HumanEval."

### AI Output Summary

Claude proposed the six-state workflow and tool gating tables. Cursor implemented `agent/fsm.py` and wired it into `agent/agent.py`. The design prevented edits during PLAN/EXPLORE and required tests during VERIFY.

### Decision

- [x] Modified before use

### Reasoning

The state machine concept was accepted (`19cef53`, merge `79ebb12`). We changed several AI defaults: transitions were initially too rigid (e.g. forced EXPLORE → IMPLEMENT after a fixed iteration count), and IMPLEMENT initially lacked execution tools — fixed later in `dbf82fa`. Measured outcome: ~47% token reduction on HumanEval versus the previous free-form loop.

### Impact

Largest architectural win of the project. FSM discipline is why the agent behaves like an engineering process rather than a chatty autocomplete. We recommend AI for FSM design brainstorming, but transition rules must be tuned against real trajectories.

---

## Entry #5 - HumanEval Failure Analysis Document

**Date:** 2026-05-28

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Coding Agent (self-analysis)

### Context

After reaching 158/164 on HumanEval, we needed to classify the six failures: infrastructure bugs vs. genuine reasoning/spec problems. This mattered for the progress report and for deciding whether further prompt hacks were worthwhile.

### Prompt / Task

"Given these six failing HumanEval trajectories and outputs, categorize each failure (ambiguous spec, edge case, rounding semantics, etc.) and draft a markdown failure analysis with tables and root-cause notes."

### AI Output Summary

Produced a categorized breakdown matching what became `docs/failure_analysis.md`: ambiguous specifications, tie-breaking, banker's rounding, nested structure edge cases. Suggested that remaining failures were not Windows/infra issues.

### Decision

- [x] Accepted as-is

### Reasoning

We manually re-checked each failing task against the official tests. The categories were accurate. Commit `e5035c3` / merge `f11dd41`. No code change was required — documentation only.

### Impact

Stopped us from wasting time chasing infra ghosts. Confirmed the ceiling was mostly problem ambiguity / rare edge cases under Gemma4-31b. Good use of AI for post-hoc analysis of logs.

---

## Entry #6 - Extended Tools: Git, Navigation, Shell

**Date:** 2026-06-01

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

HumanEval-style single-file tasks were solved, but real repositories need git awareness, richer navigation, and controlled shell access. We planned SWE-bench next and needed tools closer to a human developer's workflow.

### Prompt / Task

"Add agent tools for git_status, git_diff, git_commit, git_log, git_checkout_file, insert_at_line, delete_lines, find_files/view_directory style navigation, and a run_command tool. For run_command, discuss whitelist vs blacklist security."

### AI Output Summary

Cursor generated schemas and implementations for the extended tool set. It initially recommended a command blacklist (block `rm -rf`, `curl`, etc.).

### Decision

- [x] Modified before use

### Reasoning

Tool APIs landed in `d82dd2d` / `a931554`. We **rejected** the blacklist security model after reviewing bypasses (obfuscated shells, interpreters). Later reliability work (`9778959`) replaced it with deny-by-default whitelisting (`python`, `pip`, `pytest`, `git`, common safe commands). The AI code for git wrappers was mostly kept after testing on a sample repo.

### Impact

Enabled SWE-bench work. The security rethink is a good example of AI producing working code with a weak default policy — always review security-sensitive suggestions carefully.

---

## Entry #7 - RAG with ChromaDB and First SWE-bench Solve

**Date:** 2026-06-02

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

SWE-bench repos (e.g. astropy) are too large for naive `list_files` + `read_file` exploration. We needed semantic retrieval over code chunks and a SWE-bench evaluation runner.

### Prompt / Task

"Implement a RAG index over a Python repository using ChromaDB and sentence-transformers. Chunk by AST (functions/classes) when possible. Add a SWE-bench Verified runner that prepares an instance, runs the agent, and writes a patch. Explain single-stage vs two-stage retrieval."

### AI Output Summary

AI proposed ChromaDB collections, embedding of code chunks, and a runner skeleton (`agent/rag.py`, `evaluation/swebench_runner.py`). Early design was single-stage chunk search. ChatGPT compared single-stage vs file-then-chunk retrieval; we deferred two-step RAG to a later entry.

### Decision

- [x] Modified before use

### Reasoning

First version shipped in `7b034ea` / `a12ed70` and correctly solved `astropy__astropy-12907` (one-line fix in separability matrix logic), later confirmed by the official Docker evaluator. We kept ChromaDB but reworked chunk metadata and indexing triggers (auto-index when ≥3 Python files). Embedding model choice followed AI suggestion after a small local smoke test.

### Impact

Without RAG, SWE-bench exploration burned excessive tokens. First confirmed SWE-bench Verified success. Strong recommendation for AI on RAG scaffolding; retrieval quality still needed our own indexing experiments.

---

## Entry #8 - PLAN State, Batch Tools, Loop Detection

**Date:** 2026-06-02

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

After RAG, the agent still repeated failing edits and lacked a dedicated planning phase. Tool count needed to grow for multi-file edits (`edit_files`, `search_and_replace_all`, `edit_and_verify`).

### Prompt / Task

"Add a PLAN state to the FSM before EXPLORE. Implement batch editing tools and a loop detector that notices repeated identical failures and forces a different strategy. Target ~24 tools total."

### AI Output Summary

Cursor added PLAN, batch tools, and a repetition/loop heuristic. Suggested resetting to PLAN after N identical errors.

### Decision

- [x] Modified before use

### Reasoning

Merged as `92699de` / `f8b7954`. Loop detection thresholds were tuned down after false positives on legitimate retry-with-timeout patterns. Same day FSM fix (`dbf82fa`) restored execution tools in IMPLEMENT and added recovery when the model called a blocked tool.

### Impact

Reduced thrashing on hard tasks and made multi-file edits practical. Loop detection is valuable but sensitive — we recommend AI for the first implementation, then calibrate on logged trajectories.

---

## Entry #9 - Conversational Agent, CLI, and First Web UI

**Date:** 2026-06-05

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Benchmarks alone were not enough for demos and daily use. We needed a Claude-Code-like chat loop over a project directory, a FileSystem abstraction respecting `git ls-files`, and a simple browser UI.

### Prompt / Task

"Refactor the agent around `handle_message` for multi-turn chat. Add a FileSystem class based on git ls-files. Build `python -m agent.cli` and a FastAPI + HTML UI that streams chat and shows a file sidebar."

### AI Output Summary

Large generated change set: `agent/filesystem.py`, `agent/cli.py`, `ui/app.py`, and a monolithic `ui/index.html` chat page. Proposed mapping all file tools through the FileSystem layer.

### Decision

- [x] Modified before use

### Reasoning

Architecture accepted (`5826263` / `3e20096`). We simplified the first UI (basic bubbles + sidebar) and kept production concerns out of scope. FileSystem-on-git was kept because it automatically honors `.gitignore`. Some generated HTML/JS was rewritten for clarity.

### Impact

Turned the project from "benchmark harness" into a usable product surface. AI excelled at the bulk of CLI/UI glue; we still redesigned the UI substantially in July.

---

## Entry #10 - Reliability: Structured Tests, Whitelist, Trajectories

**Date:** 2026-06-05

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Coding Agent (self-modification) + Claude

### Context

The agent had analyzed its own codebase and flagged brittle string matching on test output, blacklist security, and lossy context trimming. We used those findings as a worklist.

### Prompt / Task

Asked our own coding agent (and Cursor for follow-ups) to implement: structured `[TEST_RESULT:PASS/FAIL]` markers, command whitelist, trajectory JSON logging, timeout escalation on slow suites, and a clearer permission model.

### AI Output Summary

Patches across `execution/`, `agent/tools.py`, `agent/trajectory.py`, and failure handling. The agent edited its own modules through the conversational interface; changes were verified with benchmark smoke runs.

### Decision

- [x] Modified before use

### Reasoning

Shipped in `9778959` / `b1a1eba`. We kept structured test results and trajectory logging nearly as proposed. Permission prompts were simplified for CLI UX. This is one of the rare cases where the product under development helped implement its own hardening.

### Impact

Fewer false VERIFY transitions; safer `run_command`; reproducible run logs for debugging. High confidence recommending AI (including the agent itself) for mechanical reliability refactors when tests exist.

---

## Entry #11 - Scratchpad, Token Budget, LLM-Driven Transitions

**Date:** 2026-06-05

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Long SWE-bench sessions still "forgot" bug locations after context trim. Token spend could runaway. Hardcoded FSM iteration limits were too rigid for complex bugs.

### Prompt / Task

"Design a scratchpad memory that survives context trimming. Add a configurable token budget with an 80% warning. Replace hardcoded EXPLORE→IMPLEMENT iteration limits with an LLM-callable `request_transition` tool. Prefer storing diffs instead of full file bodies in context where possible."

### AI Output Summary

Design notes plus implementations for `write_scratchpad` / `read_scratchpad`, budget checks, diff-oriented context, and transition requests (`40b2a25` / `fbbb1a7`).

### Decision

- [x] Accepted as-is

### Reasoning

After integrating, we ran the same astropy instance and observed large token drops across successive architecture revisions (documented later in the progress report: hundreds of thousands of tokens down to ~211k for a correct patch). Scratchpad keys were left as free-form strings; that flexibility worked well enough.

### Impact

Addressed the amnesia problem directly. LLM-driven transitions fixed a weakness the agent had diagnosed in itself. Strong recommendation for AI on this class of agent-memory design.

---

## Entry #12 - Parallel Reads, Reflexion, Confidence, RAG Fixes

**Date:** 2026-06-05

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Exploration was still sequential (one read per turn). We also wanted the agent to learn from failed tasks (Reflexion) and to report confidence before finishing.

### Prompt / Task

"Allow multiple read-only tools in one turn but only one write. Implement a reflexion store that saves failure reflections and injects relevant ones into future prompts. Add `report_confidence`. Fix RAG indexing edge cases we hit on empty or tiny repos."

### AI Output Summary

Parallel tool scheduling rules, `agent/reflexion.py`, confidence reporting, and RAG guard fixes (`dfdde15` / `0430fb3`).

### Decision

- [x] Modified before use

### Reasoning

Parallel reads were accepted. We limited injection to the 3 most recent reflections. Retrieval is recency-based only — similarity matching remains future work. Confidence scoring was kept as advisory (does not alone block DONE).

### Impact

Exploration became 2–3× faster in practice. Reflexion helped on repeated similar failures but needed filtering. Mixed recommendation: parallel tool policy — yes; naive reflexion injection — tune carefully.

---

## Entry #13 - Two-Step RAG, Test Generation, Multi-Model Routing

**Date:** 2026-06-05

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Single-stage chunk retrieval still returned noisy snippets on huge repos. We wanted file-level recall first, then chunk search inside shortlisted files. Also planned `generate_test` and lighter models for exploration.

### Prompt / Task

"Implement two-step RAG: (1) embed file summaries (imports + defs) to pick files, (2) search chunks inside those files. Add a generate_test tool so the agent can write a reproduction script before fixing. Route EXPLORE to a lighter model and IMPLEMENT/FIX to the full model."

### AI Output Summary

Two-step retrieval pipeline, test-generation tool, and model routing hooks (`460dc68` / `b7ff33b`). README updated the same day to document the expanded tool list and features.

### Decision

- [x] Modified before use

### Reasoning

Two-step RAG matched earlier Claude advice from Entry #7 and worked better on astropy-scale code. The shipped implementation differs from the prompt: step 2 does not restrict the search to the shortlisted files, it searches all chunks and promotes those originating from the step-1 files to the top of the results — a re-ranking rather than a hard metadata filter. Multi-model routing was implemented but never activated: the light-model environment variable (INNKUBE_MODEL_LIGHT) has no default, and it was unset in every published run, so all benchmark runs used a single model throughout. We kept the abstraction for future use. `generate_test` prompts were shortened after the model wrote overly large fixtures.

### Impact

Completed the "advanced features" layer of the agent. Retrieval quality and pre-fix reproduction improved SWE-bench reliability. Good example of revisiting an earlier AI suggestion once we had evidence.

---

## Entry #14 - Developer Experience Pass

**Date:** 2026-06-06

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

CLI users needed streaming output, smarter context trimming summaries, API retry on transient InnKube errors, chat persistence, and clearer recovery when tools failed mid-turn.

### Prompt / Task

"Improve developer experience: stream assistant tokens in the CLI, summarize dropped context instead of silent deletion, retry LLM HTTP calls with backoff, persist chat sessions, and recover cleanly from tool exceptions."

### AI Output Summary

Streaming CLI updates, trim summarization, retry wrapper around the LLM client, and session persistence helpers (`1802f7a` / `a897b61`).

### Decision

- [x] Accepted as-is

### Reasoning

Changes were mostly orthogonal and low-risk. We smoke-tested CLI streaming and forced API failures to confirm retries. No major redesign needed.

### Impact

Made daily demos and debugging far less painful. Ideal AI use case: cross-cutting DX improvements with clear acceptance checks.

---

## Entry #15 - Agent Chat History Module

**Date:** 2026-06-12

**Team member(s):** Haseeb Ramzan

**AI Tool used:** Cursor + Coding Agent (self-modification)

### Context

Conversations needed durable history the agent and UI could reload — not only in-memory message lists. Required for continuing work across CLI/UI sessions.

### Prompt / Task

"Add a history module for the coding agent so past turns can be stored and reloaded for CLI and the web UI. Integrate with `handle_message` without breaking benchmark `run_task` mode."

### AI Output Summary

New `agent/history.py` and wiring in `agent/agent.py`, `agent/cli.py`, `ui/app.py` (`c7c94ea` / merge `1f0d2ab`).

### Decision

- [x] Modified before use

### Reasoning

Persistence format was simplified (we did not need full export/import features the AI suggested). Ensured benchmark mode stayed isolated from chat history files.

### Impact

Enabled longer conversational workflows and later UI chat runs. Moderate time save; integration testing took longer than generation.

---

## Entry #16 - Chat Mode Dropping Extra Tool Calls

**Date:** 2026-06-15

**Team member(s):** Haseeb Ramzan

**AI Tool used:** Cursor

### Context

In chat mode, when the model returned multiple tool calls in one turn, extras were dropped — breaking parallel reads and multi-step plans that worked in benchmark mode.

### Prompt / Task

"Bug: chat/handle_message path only executes the first tool call in a turn. Find why and fix it so all tool calls in the assistant message are executed, respecting parallel-read / single-write rules."

### AI Output Summary

Cursor located the early-return / single-call handling in the chat loop and proposed executing the full tool-call list with the same scheduling rules as `run_task` (`ad8ce69` / `df8969a`).

### Decision

- [x] Accepted as-is

### Reasoning

Reproduced with a prompt that requested two `read_file` calls; before the fix only one ran. After the fix both ran. Small, targeted diff — reviewed carefully because tool loops are easy to break.

### Impact

Restored feature parity between chat and benchmark modes. Excellent fit for AI debugging when you can provide a clear reproduction.

---

## Entry #17 - SWE-bench Optimizations and Guardrails

**Date:** 2026-06-17 → 2026-06-18

**Team member(s):** Mohamed Ali El Euchi

**AI Tool used:** Claude

### Context

Large SWE-bench repos exhausted the default token budget. Broken test environments caused endless VERIFY retries. The agent sometimes analyzed forever without editing ("analysis paralysis") or edited without a clear protocol.

### Prompt / Task

Several related prompts over two days:

- Raise SWE-bench token budget for large repos.
- Detect `ModuleNotFoundError` / broken envs and stop useless test retries.
- Add `--instance-ids` selection in `main.py`.
- Add an editing protocol to the SWE-bench prompt.
- Detect analysis paralysis (many reads, no writes).
- Fix system prompt inconsistencies (Haseeb).

### AI Output Summary

Patches across `evaluation/swebench_runner.py`, `agent/agent.py`, `agent/prompts.py`, `main.py` (`08710b4`, `55c0883`, `6e43fac`, `ab5d3b3`, `042925b`, `38db2b2`, `457483b`).

### Decision

- [x] Modified before use

### Reasoning

200k token budget (`55c0883`) was accepted for large instances after cost discussion. Analysis-paralysis detection thresholds were adjusted to avoid false positives on legitimately large explorations. Prompt fixes (`457483b`) were human-edited after AI drafts drifted from tool names again. The implemented detector compares the last three tool results rather than counting reads, which also catches repeated failing edits.

### Impact

Fewer wasted iterations on dependency-broken environments; more controllable eval runs via instance IDs. AI was useful for generating detectors quickly; threshold tuning remained empirical.

---

## Entry #18 - Edit Reliability and Agent Exit Logic

**Date:** 2026-06-25

**Team member(s):** Haseeb Ramzan

**AI Tool used:** Cursor

### Context

On SWE-bench, `str_replace` / edit tools still failed when context drifted slightly from disk, and the agent sometimes failed to exit cleanly after a successful patch or after an unrecoverable state.

### Prompt / Task

"Improve edit-tool reliability for SWE-bench (better mismatch errors, safer apply path) and fix agent exit logic so successful or terminal states don't leave the loop hanging. Touch agent.py, tools.py, llm.py, swebench_runner.py as needed."

### AI Output Summary

More precise edit failure messages, tighter apply logic, and clearer termination conditions (`6ec4bb2` / `98da814`).

### Decision

- [x] Modified before use

### Reasoning

Accepted the edit diagnostics; simplified one proposed retry strategy that could mask real patch mistakes. Verified on a previously flaky edit trajectory.

### Impact

Fewer false "couldn't find string" dead-ends and cleaner eval completion. Recommend AI for this kind of surgical reliability fix when logs are available.

---

## Entry #19 - Web IDE Rebuild (Vite + TypeScript + Xterm)

**Date:** 2026-07-04

**Team member(s):** Haseeb Ramzan, Mohamed Ali El Euchi

**AI Tool used:** Cursor (Claude)

### Context

The June HTML UI was too limited for demos: no real terminal, weak file tree, and hard-to-maintain frontend. We rebuilt into a Vite + TypeScript IDE with chat, file tree, path picker, and an Xterm.js terminal backed by a PTY on the server.

### Prompt / Task

"Replace ui/index.html with a Vite TypeScript frontend. FastAPI should serve API + static build, expose SSE chat, workspace session validation, and a WebSocket PTY terminal for Xterm.js. Include nested file tree, collapsible tool cards, file preview, and live token counter."

### AI Output Summary

Large scaffold: `ui/frontend/**`, `ui/session.py`, `ui/terminal.py`, `ui/chat_runs.py`, backend routes in `ui/app.py`, and README updates (`a87a9a2`). Roughly 6.7k insertions in the commit.

### Decision

- [x] Modified before use

### Reasoning

Overall architecture accepted (SSE chat, WS terminal, session path checks, `ALLOWED_WORKSPACE_ROOTS`). We reworked CSS/layout and several frontend components for clarity, and hardened path validation beyond the first AI draft. Dependency pins needed a follow-up (`1babc9a` on 2026-07-09).

### Impact

Biggest UX leap of the project. AI made the rebuild feasible in a short window; security-sensitive path/PTY code required careful human review. Would use AI again for UI scaffolding, with explicit security review checklist for workspace roots and shell access.

---

## Entry #20 - Windows Terminal / PTY Compatibility Fix

**Date:** 2026-07-29

**Team member(s):** Mohamed Ali El Euchi, Haseeb Ramzan

**AI Tool used:** Cursor(claude)

### Context

The IDE terminal worked conceptually on Unix-style PTYs but failed or degraded on Windows, blocking local demos on the primary development OS.

### Prompt / Task

"Fix terminal access for Windows in ui/terminal.py and related frontend/backend pieces. Prefer a Windows-compatible PTY/conpty approach, keep the WebSocket protocol stable, update requirements if a new dependency is needed."

### AI Output Summary

Windows-oriented terminal manager changes, session/app wiring, and small frontend adjustments (`32de859`). Added the required dependency in `requirements.txt`.

### Decision

- [x] Modified before use

### Reasoning

Accepted the ConPTY/Windows path after manual testing in the IDE (open workspace → terminal spawn → basic shell commands). Adjusted error handling when a terminal cannot start so the rest of the UI still works. Did not accept an AI suggestion to disable the terminal entirely on Windows.

### Impact

Unblocked Windows demos of the full IDE (chat + tree + terminal). Platform-compatibility fixes are a strong AI use case when the failure mode is explicit.

---
