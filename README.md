# Build Your Own Coding Agent

A tool-augmented LLM agent that iteratively writes, executes, and fixes Python code in a sandboxed workspace.

## Setup

```bash
cd build-coding-agent
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set INNKUBE_API_KEY (get key from InnKube inference key manager)
```

## Usage

```bash
# Test LLM connection
python main.py --smoke

# Run a single benchmark task
python main.py --task 001

# Run full custom benchmark
python main.py --benchmark

# Run first N tasks only
python main.py --benchmark --limit 3

# HumanEval (official 164-problem dataset)
python main.py --humaneval --limit 5          # first 5 problems
python main.py --humaneval --offset 10 --limit 5
python main.py --humaneval-task HumanEval/0   # single problem
python main.py --humaneval                    # all 164 (slow + API cost)
```

Results are saved to `results/humaneval_<timestamp>.json` with **Pass@1** success rate and a **failure_breakdown** by category (`max_iterations`, `stopped_early`, `humaneval_check_failed`, etc.).

### Agent improvements

- Won't stop until tests pass (nudges the model up to 3 times if it tries to finish early)
- One tool executed per turn (reduces wasted parallel calls)
- Truncated pytest output with failure summary (saves tokens)
- Default 12 iterations per task
- HumanEval tasks sorted numerically (0, 1, 2 … not 0, 1, 10)

## Project structure

```
agent/          # LLM client, tools, agent loop, prompts
execution/      # Sandboxed code execution
evaluation/     # Benchmark runner and metrics
workspace/      # Per-task sandboxes (gitignored)
results/        # Run outputs (gitignored)
```

## InnKube LLM

Uses the [InnKube LLM Inference Endpoint](https://innkube.pages.gitlab.innkube.fim.uni-passau.de/documentation/our_infrastructure/llms-hub.html) with model `gemma4-31b-it` by default.
