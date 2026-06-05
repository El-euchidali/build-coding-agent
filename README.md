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

### Web UI — Browser Chat

```bash
python -m ui.app
# Open http://localhost:8000
```

Features: chat bubbles, collapsible tool cards, file sidebar, live token counter, typing indicator.

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

## Tools (24)

| Category       | Tools                                                                             |
| -------------- | --------------------------------------------------------------------------------- |
| **Navigation** | `list_files`, `view_directory`, `find_files`, `explore_repo`                      |
| **Reading**    | `read_file`, `read_files`, `view_file_range`, `file_outline`, `get_function`      |
| **Searching**  | `search_code`, `search_codebase` (RAG), `search_and_read`                         |
| **Editing**    | `write_file`, `str_replace`, `insert_at_line`, `delete_lines`, `create_directory` |
| **Batch**      | `edit_and_verify`, `edit_files`, `search_and_replace_all`                         |
| **Execution**  | `run_code`, `run_tests`, `run_command` (whitelisted)                              |
| **Git**        | `git_status`, `git_diff`, `git_commit`, `git_log`, `git_checkout_file`            |
| **Agent**      | `write_scratchpad`, `read_scratchpad`, `request_transition`, `report_confidence`  |

## Key Features

- **FileSystem foundation** — all file operations go through `git ls-files`, respecting `.gitignore` automatically
- **RAG via ChromaDB** — AST-based chunking, semantic search with sentence-transformers, auto-indexes codebases with 3+ Python files
- **Scratchpad memory** — agent saves notes that survive context trimming, preventing amnesia on long tasks
- **Structured test results** — `[TEST_RESULT:PASS] passed=X failed=Y errors=Z` instead of brittle string matching
- **Trajectory logging** — every agent run saved as structured JSON for analysis
- **Loop detection** — detects repeated failures and forces different approaches
- **Token budget** — configurable limit with 80% warning, prevents runaway API costs
- **Command whitelisting** — deny by default, only safe commands allowed
- **Reflexion** — stores failure reflections, retrieves them for similar future tasks
- **Timeout escalation** — retries with doubled timeout on slow test suites
- **Parallel reads** — multiple read tools per turn, one write per turn

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
├── app.py             # FastAPI web backend
├── index.html         # Chat UI frontend
docs/
├── failure_analysis.md # HumanEval failure analysis
```

## LLM

Uses the InnKube LLM Inference Endpoint with model `gemma4-31b-it` via an OpenAI-compatible API.
