# Coding Agent

An autonomous Python coding agent that navigates codebases, fixes bugs, writes code, and verifies solutions through tests. Built with a Finite State Machine controller, RAG-powered code search, and a conversational interface.

## Results

All SWE-bench numbers below come from the **official SWE-bench Docker evaluator**, not from the agent's own test runs.

| Benchmark                    | Model / config           | Patches | Resolved | Score                          |
| ---------------------------- | ------------------------ | ------- | -------- | ------------------------------ |
| **HumanEval** (164)          | gemma4-31b-it            | —       | 158/164  | **96.3%** (avg 2.0 iterations) |
| **SWE-bench Verified** (500) | gemma4-31b-it, pre-guard | 344     | 161/500  | 32.2% (precision 46.8%)        |
| **SWE-bench Verified** (500) | gemma4-31b-it, final     | 403     | 185/500  | **37.0%** (precision 45.9%)    |
| **SWE-bench Verified** (500) | qwen36-35b, final        | 313     | 173/500  | 34.6% (precision **55.3%**)    |

Token cost across the three full runs: 168M → 88M for the same model once the guard set was added, and 115M for Qwen. Precision is resolved ÷ non-empty patches. All runs used identical tasks, budgets, and evaluator.

### Finite State Machine

The agent follows a structured workflow instead of free-form tool use:

```
PLAN → EXPLORE → IMPLEMENT → VERIFY → FIX → DONE
```

Each state restricts which tools are available, preventing the LLM from skipping steps or making premature changes. Enforcement is mechanical: only the schemas legal in the current state are sent with each API call.

Transitions happen two ways:

- **Event-driven** (in code): a `run_tests` result of `[TEST_RESULT:PASS]` moves the agent to `DONE` from any state that exposes test execution; a failing run moves it to `FIX`; a completed edit forces re-verification; sustained searching pushes toward `IMPLEMENT`.
- **Model-requested**: `request_transition` is validated against the allowed map — an invalid target returns an error observation and the state does not change (e.g. `FIX → EXPLORE` when the original hypothesis was wrong).

The **entry state is detected**, not hardcoded: `detect_initial_state()` inspects the workspace once — more than 10 Python files → `PLAN`; a small workspace → `EXPLORE`; an existing `solution.py` → `IMPLEMENT` if it is a stub, `VERIFY` if it already contains real code. On SWE-bench this always resolves to `PLAN`; on HumanEval it saves 2–3 iterations per task.

## Setup

```bash
git clone https://git.fim.uni-passau.de/elleuchi/build-coding-agent
cd build-coding-agent
python -m venv venv
venv\Scripts\activate        # Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set INNKUBE_API_KEY
```

### Optional: SWE-bench evaluation (requires WSL/Linux + Docker)

```bash
pip install swebench
# Docker must be running for official evaluation
```

## Usage

### CLI — Terminal Chat (like Claude Code)

```bash
python -m agent.cli                    # chat in current directory
python -m agent.cli /path/to/project   # chat in a specific project
```

```
λ Coding Agent
Working in: /path/to/project
23 Python files indexed

You: Summarize this project
Agent: [reads files] This is a Flask app with...

You: Fix the bug in auth.py
Agent: [reads, edits, tests] Fixed — the token validation...
```

### Web IDE — Browser IDE with Terminal

The IDE UI provides a file tree, agent chat, and an interactive terminal (Xterm.js) in the selected project directory.

**Production (single server):**

```bash
cd ui/frontend && npm install && npm run build
cd ../..
python -m ui.app
# Open http://localhost:8000
```

On load the UI automatically opens the directory the server was launched from. To work somewhere else, enter a project path (existing or new — missing folders are created on open) or click **Browse…** to pick a folder. Opening a workspace simultaneously:

- Initializes the coding harness (`init_conversation`) in that directory
- Spawns a shell with CWD set to that path (Python PTY over WebSocket)
- Loads the file tree for the workspace

**Development (hot reload frontend):**

```bash
# Terminal 1 — backend
python -m ui.app

# Terminal 2 — Vite dev server (proxies /api and /ws to :8000)
cd ui/frontend && npm install && npm run dev
# Open http://localhost:5173
```

Optional: restrict openable paths with `ALLOWED_WORKSPACE_ROOTS=/path/a,/path/b`.

Features: nested file tree, chat with SSE streaming, collapsible tool cards, file preview panel, live token counter, Xterm.js terminal.

### Benchmarks

```bash
# Single task
python main.py --task 001

# HumanEval
python main.py --humaneval --limit 5
python main.py --humaneval                                  # all 164

# SWE-bench Verified — patch generation (no Docker needed)
python main.py --swebench --limit 1 --no-eval               # smoke test
python main.py --swebench --limit 10 --no-eval --max-iterations 30
python main.py --swebench --no-eval --max-iterations 30     # full 500-task run
```

Results are saved after every task, so a rerun resumes automatically and skips completed instance IDs. The published runs used `--max-iterations 30` explicitly (the CLI default is 20).

**Official evaluation** (WSL/Linux + Docker):

```bash
python main.py --swebench-eval --predictions results/PREDICTIONS.jsonl
# between batches, reclaim disk: docker system prune -a -f
```

Evaluating in per-repository batches keeps Docker image usage manageable on a laptop.

## Tools (33)

| Category       | Tools                                                                             |
| -------------- | --------------------------------------------------------------------------------- |
| **Navigation** | `list_files`, `view_directory`, `find_files`, `explore_repo`                      |
| **Reading**    | `read_file`, `read_files`, `view_file_range`, `file_outline`, `get_function`      |
| **Searching**  | `search_code`, `search_codebase` (two-step RAG), `search_and_read`                |
| **Editing**    | `write_file`, `str_replace`, `insert_at_line`, `delete_lines`, `create_directory` |
| **Batch**      | `edit_and_verify`, `edit_files`, `search_and_replace_all`                         |
| **Execution**  | `run_code`, `run_tests`, `run_command` (whitelisted), `generate_test`             |
| **Git**        | `git_status`, `git_diff`, `git_commit`, `git_log`, `git_checkout_file`            |
| **Agent**      | `write_scratchpad`, `read_scratchpad`, `request_transition`, `report_confidence`  |

The FSM exposes only a state-specific subset on each turn, so an individual API call carries far fewer than 33 schemas.

## Key Features

- **FileSystem foundation** — repository enumeration uses `git ls-files` (respecting `.gitignore`), with an `rglob` fallback for non-git directories; every path is resolved and checked to stay inside the workspace root
- **Two-step RAG via ChromaDB** — step 1 ranks file summaries, step 2 ranks code chunks and promotes those originating from the step-1 files. AST-based chunking (functions and classes at any nesting depth, stored whole) with `all-MiniLM-L6-v2` embeddings; a fresh in-memory index is built per task for codebases with 3+ Python files
- **Scratchpad memory** — the agent writes notes via a tool; they are re-injected on every iteration, so they survive context trimming and prevent amnesia on long tasks
- **Reflexion** — failures are written to `reflections/reflection_{timestamp}.json`; the **3 most recent** are loaded at the start of the next task (recency-based; no similarity matching yet)
- **Structured test results** — `[TEST_RESULT:PASS] passed=X failed=Y errors=Z` instead of brittle string matching
- **Trajectory logging** — every agent run saved as structured JSON for analysis
- **Broken-environment detection** — recognizes dependency-error patterns (`ModuleNotFoundError`, `ImportError`, "No module named", "could not determine", "broken installation"); after 2 hits it stops futile test reruns and directs the agent to verify with `git_diff`
- **Loop detection** — if the last 3 tool results are identical, injects a change-approach directive
- **Edit resilience** — whitespace-tolerant `str_replace` with automatic re-indentation, plus a closest-match hint (via `difflib`) returned when an exact match fails
- **Token budget** — 200,000 tokens per task by default, with a warning threshold, preventing runaway consumption
- **Retry with backoff** — transient endpoint errors get up to 3 attempts with linear delays (5s → 10s → 15s)
- **Command whitelisting** — `run_command` is gated against a prefix allowlist (`python`, `pip`, `pytest`, `git`, `ls`, …); anything else is rejected before a subprocess is spawned
- **Parallel reads, single write per turn** — multiple read tools may run in one turn, but at most one write, preventing accidental multi-file clobbering
- **JSON fallback tool parsing** — tool calls are also parsed from raw text, so the agent works with OpenAI-compatible models that lack native tool-use support
- **Automated test generation** — the agent can write reproduction scripts to verify a bug exists before fixing it
- **Optional light-model routing** — set `INNKUBE_MODEL_LIGHT` to serve `PLAN`/`EXPLORE` from a cheaper model; unset by default, and all published runs used a single model throughout

## Project Structure

```
agent/
├── agent.py           # Core engine: handle_message + run_task
├── filesystem.py      # FileSystem foundation (git ls-files)
├── tools.py           # 33 tools with schemas and implementations
├── fsm.py             # Finite State Machine (6 states + detect_initial_state)
├── rag.py             # RAG index (ChromaDB + sentence-transformers)
├── llm.py             # LLM API client
├── prompts.py         # System prompts
├── failure.py         # Failure classification
├── trajectory.py      # Trajectory logging
├── reflexion.py       # Reflexion — learn from failures
├── cli.py             # Terminal chat interface
execution/
├── sandbox.py         # Sandboxed code execution
├── output_format.py   # Test output formatting
evaluation/
├── humaneval_runner.py # HumanEval benchmark
├── swebench_runner.py  # SWE-bench Verified benchmark
├── runner.py           # Custom benchmark runner
├── metrics.py          # Evaluation metrics
ui/
├── app.py             # FastAPI backend (REST + WebSocket + static)
├── session.py         # Workspace session + path validation
├── terminal.py        # PTY manager for Xterm.js
└── frontend/          # Vite + TypeScript IDE frontend
    ├── src/
    │   ├── components/  # pathPicker, fileTree, terminal, chat
    │   └── main.ts
    └── dist/          # Built assets (after npm run build)
docs/
├── failure_analysis.md # HumanEval failure analysis
```

## LLM

Uses the InnKube LLM Inference Endpoint through an OpenAI-compatible API. Configured via environment variables:

| Variable              | Default                                  | Purpose                                       |
| --------------------- | ---------------------------------------- | --------------------------------------------- |
| `INNKUBE_API_KEY`     | — (required)                             | authentication                                |
| `INNKUBE_BASE_URL`    | `https://llms.innkube.fim.uni-passau.de` | endpoint                                      |
| `INNKUBE_MODEL`       | `gemma4-31b-it`                          | primary model                                 |
| `INNKUBE_MODEL_LIGHT` | unset                                    | optional cheaper model for `PLAN` / `EXPLORE` |

Benchmarked models: `gemma4-31b-it` (dense, 31B parameters) and `qwen36-35b` (Qwen3.6-35B-A3B, mixture-of-experts, ~3B active parameters per token).
