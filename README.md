# Coding Agent

An autonomous Python coding agent that navigates codebases, fixes bugs, writes code, and verifies solutions through tests. Built with a Finite State Machine controller, RAG-powered code search, and a conversational interface.

## Results

| Benchmark              | Score           | Details                                                           |
| ---------------------- | --------------- | ----------------------------------------------------------------- |
| **HumanEval**          | 96.3% (158/164) | 6 failures are genuine edge cases                                 |
| **SWE-bench Verified** | 1/1 confirmed   | Correct patch for astropy — verified by official Docker evaluator |

### Finite State Machine

The agent follows a structured workflow instead of free-form tool use:

```
PLAN → EXPLORE → IMPLEMENT → VERIFY → FIX → DONE
```

Each state restricts which tools are available, preventing the LLM from skipping steps or making premature changes. The agent can also request state transitions via the `request_transition` tool when it needs more exploration or wants to re-plan.

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

Enter a project path (existing or new — missing folders are created on open), or click **Browse…** to pick a folder. Clicking **Open** simultaneously:
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
python main.py --humaneval                    # all 164

# SWE-bench Verified
python main.py --swebench --limit 1 --no-eval # generate patches
# Official evaluation requires WSL/Linux + Docker
```

## Tools (29)

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

## Key Features

- **FileSystem foundation** — all file operations go through `git ls-files`, respecting `.gitignore` automatically
- **Two-step RAG via ChromaDB** — first finds relevant files, then retrieves specific chunks within those files. AST-based chunking, auto-indexes codebases with 3+ Python files
- **Scratchpad memory** — agent saves notes that survive context trimming, preventing amnesia on long tasks
- **Structured test results** — `[TEST_RESULT:PASS] passed=X failed=Y errors=Z` instead of brittle string matching
- **Trajectory logging** — every agent run saved as structured JSON for analysis
- **Loop detection** — detects repeated failures and forces different approaches
- **Token budget** — configurable limit with 80% warning, prevents runaway API costs
- **Command whitelisting** — deny by default, only safe commands allowed
- **Reflexion** — stores failure reflections, retrieves them for similar future tasks
- **Timeout escalation** — retries with doubled timeout on slow test suites
- **Parallel reads** — multiple read tools per turn, one write per turn
- **Multi-model routing** — uses lighter model for exploration, full model for implementation
- **Automated test generation** — agent can write reproduction scripts to verify bugs exist before fixing

## Project Structure

```
agent/
├── agent.py           # Core engine: handle_message + run_task
├── filesystem.py      # FileSystem foundation (git ls-files)
├── tools.py           # 24 tools with schemas and implementations
├── fsm.py             # Finite State Machine (6 states)
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

Uses the InnKube LLM Inference Endpoint with model `gemma4-31b-it` via an OpenAI-compatible API.
