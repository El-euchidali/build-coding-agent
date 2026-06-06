"""
Terminal chat interface for the coding agent.
Usage: python -m agent.cli [workspace_path]

Conversational agent in your terminal — like Claude Code.
"""

import json
import sys
import time
from pathlib import Path

from agent.agent import init_conversation, handle_message_streaming
from agent.filesystem import FileSystem


# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _c(text, code):
    return f"\033[{code}m{text}\033[0m"

def gray(t): return _c(t, "90")
def green(t): return _c(t, "32")
def magenta(t): return _c(t, "35")
def cyan(t): return _c(t, "36")
def red(t): return _c(t, "31")
def yellow(t): return _c(t, "33")
def bold(t): return _c(t, "1")
def dim(t): return _c(t, "2")


# ── Display helpers ───────────────────────────────────────────────────────────

def print_tool_call(event):
    """Pretty-print a tool call with result."""
    tool = event.get("tool", "")
    args = event.get("args", {})
    elapsed = event.get("tool_elapsed", 0)

    args_parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:40] + "..."
        args_parts.append(v_str)
    args_str = ", ".join(args_parts)

    print(f"  {cyan('>')} {bold(tool)}({gray(args_str)})  {dim(str(elapsed) + 's')}")

    result = event.get("result", "")
    if result:
        lines = result.split("\n")
        for line in lines[:5]:
            print(f"    {dim(line[:120])}")
        if len(lines) > 5:
            print(f"    {dim('... (' + str(len(lines)) + ' lines)')}")
    print()


def stream_text(content):
    """Print response with word-by-word streaming effect."""
    words = content.split(" ")
    for i, word in enumerate(words):
        if i > 0:
            sys.stdout.write(" ")
        sys.stdout.write(word)
        sys.stdout.flush()
        if "\n" in word:
            time.sleep(0.02)
        else:
            time.sleep(0.015)
    print()


# ── History persistence ───────────────────────────────────────────────────────

def save_history(messages, workspace):
    """Save conversation history to disk."""
    history_dir = workspace / ".agent_history"
    history_dir.mkdir(exist_ok=True)
    path = history_dir / "last_conversation.json"
    saveable = [
        {"role": m["role"], "content": m.get("content", "")[:500]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    try:
        path.write_text(json.dumps(saveable, indent=2), encoding="utf-8")
    except OSError:
        pass


def archive_history(messages, workspace):
    """Archive current conversation with timestamp."""
    history_dir = workspace / ".agent_history"
    history_dir.mkdir(exist_ok=True)
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = history_dir / f"conversation_{timestamp}.json"
    saveable = [
        {"role": m["role"], "content": m.get("content", "")[:500]}
        for m in messages
        if m.get("role") in ("user", "assistant")
    ]
    if saveable:
        try:
            path.write_text(json.dumps(saveable, indent=2), encoding="utf-8")
        except OSError:
            pass

def load_history(workspace):
    """Load previous conversation if it exists."""
    path = workspace / ".agent_history" / "last_conversation.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()
    else:
        workspace = Path.cwd()

    if not workspace.exists():
        print(f"Error: {workspace} does not exist")
        sys.exit(1)

    # Header
    print()
    print(f"  {bold('Coding Agent')}")
    print(f"  {gray(str(workspace))}")

    fs = FileSystem(workspace)
    py_count = len(fs.tracked_files("*.py"))
    print(f"  {gray(str(py_count) + ' Python files indexed')}")


    print(f"  {gray('Commands: exit, clear')}")
    print()

    messages = init_conversation(workspace)
    # Auto-resume last conversation
    prev = load_history(workspace)
    if prev:
        for msg in prev:
            messages.append(msg)
        msg_count = len([m for m in prev if m["role"] == "user"])
        print(f"  {gray('Resumed previous conversation (' + str(msg_count) + ' messages)')}")
        print()
        
    total_tokens = 0

    while True:
        try:
            user_input = input(f"  {green('You:')} ")
        except (KeyboardInterrupt, EOFError):
            print()
            save_history(messages, workspace)
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            save_history(messages, workspace)
            break

        if user_input.lower() in ("clear", "reset", "new"):
            messages = init_conversation(workspace)
            total_tokens = 0
            print(f"\n  {gray('Chat cleared.')}\n")
            continue

        

        print()
        start = time.time()
        tool_count = 0

        try:
            for event in handle_message_streaming(
                user_message=user_input,
                messages=messages,
                workspace=workspace,
                max_tool_rounds=30,
            ):
                if event["type"] == "tool_call":
                    print_tool_call(event)
                    tool_count += 1

                elif event["type"] == "thinking":
                    content = event.get("content", "")
                    if content:
                        print(f"  {dim(content[:200])}")
                        print()

                elif event["type"] == "response":
                    content = event.get("content", "")
                    if content:
                        sys.stdout.write(f"  {magenta('Agent:')} ")
                        stream_text(content)
                    total_tokens = event.get("tokens", {}).get("total", total_tokens)

        except KeyboardInterrupt:
            print(f"\n  {yellow('Interrupted.')}\n")
            continue
        except Exception as e:
            print(f"\n  {red('Error: ' + str(e))}\n")
            continue

        elapsed = round(time.time() - start, 1)
        tool_label = str(tool_count) + " tool" + ("s" if tool_count != 1 else "")
        stats_parts = [str(elapsed) + "s"]
        if tool_count:
            stats_parts.append(tool_label)
        stats_parts.append(f"{total_tokens:,} tokens")
        print(f"  {dim(' . '.join(stats_parts))}")
        print()

        save_history(messages, workspace)
        
        


if __name__ == "__main__":
    main()