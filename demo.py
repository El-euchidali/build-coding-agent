"""
Demo script for professor presentation.
Usage: python demo.py <command>

Commands:
  smoke           Test LLM connection
  show <id>       Show a HumanEval problem (e.g. python demo.py show 2)
  solve <id>      Solve a HumanEval problem live (e.g. python demo.py solve 0)
  custom <id>     Solve a custom benchmark task (e.g. python demo.py custom 001)
  solution <id>   Show what the agent wrote for a HumanEval task
  tests <id>      Show the hidden tests for a HumanEval task
  verify <id>     Run pytest on a HumanEval solution
  failure <id>    Show a failed solution and why it failed
  results         Show full HumanEval run summary
  compare         Show token/iteration improvement from FSM
  git             Show git history
  fsm             Show FSM states
"""

import sys
import json
import subprocess
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def separator(title: str = ""):
    width = 60
    if title:
        print(f"\n{'─' * 4} {title} {'─' * (width - len(title) - 6)}\n")
    else:
        print("─" * width)


def run(cmd: str):
    subprocess.run(cmd, shell=True)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_smoke():
    separator("Testing LLM Connection")
    run("python main.py --smoke")


def cmd_show(problem_id: str):
    separator(f"HumanEval/{problem_id} — Problem Statement")
    from human_eval.data import read_problems
    problems = read_problems()
    key = f"HumanEval/{problem_id}"
    if key not in problems:
        print(f"Problem {key} not found.")
        return
    p = problems[key]
    print(f"Task ID:     {p['task_id']}")
    print(f"Function:    {p['entry_point']}")
    separator("Prompt (what the agent sees in solution.py)")
    print(p["prompt"])
    separator("Hidden Tests (agent never sees these)")
    print(p["test"])
    separator("Canonical Solution (correct answer)")
    print(p["canonical_solution"])


def cmd_solve(problem_id: str):
    separator(f"Solving HumanEval/{problem_id} Live")
    offset = int(problem_id)
    run(f"python main.py --humaneval --limit 1 --offset {offset}")


def cmd_custom(task_id: str):
    separator(f"Solving Custom Task {task_id} Live")
    run(f"python main.py --task {task_id}")


def cmd_solution(problem_id: str):
    separator(f"Agent Solution — HumanEval/{problem_id}")
    path = Path(f"workspace/humaneval/HumanEval_{problem_id}/solution.py")
    if not path.exists():
        print(f"No solution found. Run: python demo.py solve {problem_id}")
        return
    print(path.read_text(encoding="utf-8"))


def cmd_tests(problem_id: str):
    separator(f"Test File — HumanEval/{problem_id}")
    path = Path(f"workspace/humaneval/HumanEval_{problem_id}/test_solution.py")
    if not path.exists():
        print(f"No test file found. Run: python demo.py solve {problem_id}")
        return
    print(path.read_text(encoding="utf-8"))


def cmd_verify(problem_id: str):
    separator(f"Running Tests — HumanEval/{problem_id}")
    path = Path(f"workspace/humaneval/HumanEval_{problem_id}")
    if not path.exists():
        print(f"No workspace found. Run: python demo.py solve {problem_id}")
        return
    run(f"python -m pytest workspace/humaneval/HumanEval_{problem_id}/test_solution.py -v")


def cmd_failure(problem_id: str):
    separator(f"Failure Analysis — HumanEval/{problem_id}")
    path = Path(f"workspace/humaneval/HumanEval_{problem_id}/solution.py")
    if not path.exists():
        print(f"No solution found.")
        return
    print("Agent wrote:\n")
    print(path.read_text(encoding="utf-8"))
    separator("Running tests to show failure")
    run(f"python -m pytest workspace/humaneval/HumanEval_{problem_id}/test_solution.py -v")


def cmd_results():
    separator("Full HumanEval Run Results")
    results_dir = Path("results")
    files = sorted(results_dir.glob("humaneval_*.json"))
    if not files:
        print("No results found. Run: python main.py --humaneval")
        return
    latest = files[-1]
    data = json.loads(latest.read_text(encoding="utf-8"))
    m = data["metrics"]
    print(f"Run file:    {latest.name}")
    print(f"Total:       {m['total']}")
    print(f"Passed:      {m['passed']}")
    print(f"Pass@1:      {m['success_rate']}%")
    print(f"Avg iter:    {m['avg_iterations']}")
    print(f"Total tokens:{m.get('total_tokens', 'N/A')}")
    if data.get("tasks"):
        failures = [t for t in data["tasks"] if not t["success"]]
        if failures:
            separator("Failures")
            for f in failures:
                print(f"  - {f['task_id']}: {f.get('failure_category', 'unknown')}")
                if f.get("error"):
                    print(f"    {f['error'][:100]}")


def cmd_compare():
    separator("FSM Impact — Before vs After")
    print(f"{'Metric':<25} {'Before FSM':>12} {'After FSM':>12} {'Change':>10}")
    separator()
    rows = [
        ("HumanEval Pass@1",    "100%",     "100%",    "same"),
        ("Avg iterations",      "3.0",      "2.0",     "-33%"),
        ("Tokens (10 tasks)",   "65,802",   "34,864",  "-47%"),
        ("Custom task 001",     "11 iter",  "5 iter",  "-55%"),
        ("Custom task 010",     "8 iter",   "6 iter",  "-25%"),
    ]
    for row in rows:
        print(f"{row[0]:<25} {row[1]:>12} {row[2]:>12} {row[3]:>10}")


def cmd_git():
    separator("Git History")
    run("git log --oneline")


def cmd_fsm():
    separator("Finite State Machine — States & Transitions")
    print("""
States:
  EXPLORE   → list_files, read_file, view_file_range, search_code
  IMPLEMENT → write_file, read_file, view_file_range
  VERIFY    → run_tests, run_code
  FIX       → str_replace, read_file, view_file_range, search_code, write_file
  DONE      → stop

Transitions:
  EXPLORE   → IMPLEMENT  (after reading files, iteration >= 2)
  IMPLEMENT → VERIFY     (after write_file)
  VERIFY    → FIX        (after run_tests fails)
  VERIFY    → DONE       (after run_tests passes)
  FIX       → VERIFY     (after str_replace or write_file)
  FIX       → DONE       (after run_tests passes)

Smart initialization (detect_initial_state):
  No solution.py        → start in EXPLORE
  Stub only (pass)      → start in IMPLEMENT
  Real code present     → start in VERIFY
""")


# ── Router ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    commands = {
        "smoke":    lambda: cmd_smoke(),
        "show":     lambda: cmd_show(arg or "0"),
        "solve":    lambda: cmd_solve(arg or "0"),
        "custom":   lambda: cmd_custom(arg or "001"),
        "solution": lambda: cmd_solution(arg or "0"),
        "tests":    lambda: cmd_tests(arg or "0"),
        "verify":   lambda: cmd_verify(arg or "0"),
        "failure":  lambda: cmd_failure(arg or "69"),
        "results":  lambda: cmd_results(),
        "compare":  lambda: cmd_compare(),
        "git":      lambda: cmd_git(),
        "fsm":      lambda: cmd_fsm(),
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return

    commands[cmd]()


if __name__ == "__main__":
    main()