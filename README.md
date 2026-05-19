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

# Run full benchmark
python main.py --benchmark

# Run first N tasks only
python main.py --benchmark --limit 3
```

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
