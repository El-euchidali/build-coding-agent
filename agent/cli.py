"""
Terminal chat interface for the coding agent.
Usage: python -m agent.cli [workspace_path]

Opens a conversational agent in your terminal.
If no workspace given, uses the current directory.
"""

import json
import sys
import time
from pathlib import Path

from agent.agent import init_conversation, handle_message
from agent.llm import get_token_usage


def colorize(text: str, color: str) -> str:
    """Add ANSI color codes."""
    colors = {
        "gray": "\033[90m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "red": "\033[31m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def print_tool_call(tc):
    """Pretty-print a tool call."""
    args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in tc.args.items())
    print(colorize(f"  ⚡ {tc.name}({args_str})", "cyan"), end="")
    print(colorize(f"  {tc.elapsed}s", "gray"))

    # Show truncated result
    result_preview = tc.result[:200].replace("\n", "\n    ")
    print(colorize(f"    {result_preview}", "gray"))
    if len(tc.result) > 200:
        print(colorize(f"    ... ({len(tc.result)} chars total)", "gray"))
    print()


def main():
    # Determine workspace
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()
    else:
        workspace = Path.cwd()

    if not workspace.exists():
        print(f"Error: {workspace} does not exist")
        sys.exit(1)

    # Initialize conversation
    print(colorize(f"\n  λ Coding Agent", "bold"))
    print(colorize(f"  Working in: {workspace}", "gray"))

    messages = init_conversation(workspace)

    # Count files
    try:
        py_count = sum(1 for f in workspace.rglob("*.py")
                       if ".git" not in f.parts and "venv" not in f.parts)
    except OSError:
        py_count = 0
    print(colorize(f"  {py_count} Python files indexed", "gray"))
    print(colorize(f"  Type 'exit' to quit, 'clear' for new chat\n", "gray"))

    total_tokens = 0

    while True:
        # User input
        try:
            user_input = input(colorize("You: ", "green"))
        except (KeyboardInterrupt, EOFError):
            print("\n")
            break

        user_input = user_input.strip()

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            break

        if user_input.lower() in ("clear", "reset", "new"):
            messages = init_conversation(workspace)
            total_tokens = 0
            print(colorize("\n  Chat cleared.\n", "gray"))
            continue

        # Get response
        print()
        start = time.time()

        try:
            response, messages = handle_message(
                user_message=user_input,
                messages=messages,
                workspace=workspace,
                max_tool_rounds=15,
            )
        except Exception as e:
            print(colorize(f"  Error: {e}\n", "red"))
            continue

        elapsed = round(time.time() - start, 1)

        # Show tool calls
        if response.tool_calls:
            for tc in response.tool_calls:
                print_tool_call(tc)

        # Show response
        if response.content:
            print(colorize("Agent: ", "magenta") + response.content)
        print()

        # Show stats
        if response.tokens:
            total_tokens = response.tokens.get("total", 0)
        tools_used = len(response.tool_calls)
        stats = f"  {elapsed}s"
        if tools_used:
            stats += f" · {tools_used} tool{'s' if tools_used > 1 else ''}"
        stats += f" · {total_tokens:,} tokens total"
        print(colorize(stats, "gray"))
        print()


if __name__ == "__main__":
    main()