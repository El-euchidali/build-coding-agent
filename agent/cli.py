"""
Terminal chat interface for the coding agent.
Usage: python -m agent.cli [workspace_path]

Conversational agent in your terminal — like Claude Code.
"""

import sys
import time
from pathlib import Path

from agent.agent import init_conversation, handle_message_streaming
from agent.filesystem import FileSystem
from agent.history import (
    create_conversation,
    delete_conversation,
    extract_turn_messages,
    list_conversations,
    load_conversation,
    migrate_legacy,
    sanitize_transcript_event,
    save_conversation,
    title_from_message,
)


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


def _persist(
    workspace: Path,
    conv_id: str,
    messages: list[dict],
    user_message: str | None = None,
    turn_events: list[dict] | None = None,
) -> None:
    conv = load_conversation(workspace, conv_id)
    if conv is None:
        conv = create_conversation(workspace)
        conv["id"] = conv_id
    conv["messages"] = extract_turn_messages(messages)
    if turn_events:
        transcript = conv.get("transcript", [])
        transcript.extend(turn_events)
        conv["transcript"] = transcript
    if conv.get("title", "New chat") == "New chat" and user_message:
        conv["title"] = title_from_message(user_message)
    save_conversation(workspace, conv)


def _switch_conversation(workspace: Path, arg: str) -> dict | None:
    """Resolve a conversation by list index (1-based) or id prefix."""
    convs = list_conversations(workspace)
    if not convs:
        return None
    if arg.isdigit():
        idx = int(arg) - 1
        if 0 <= idx < len(convs):
            return load_conversation(workspace, convs[idx]["id"])
        return None
    for meta in convs:
        if meta["id"].startswith(arg):
            return load_conversation(workspace, meta["id"])
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

    migrate_legacy(workspace)

    # Header
    print()
    print(f"  {bold('Coding Agent')}")
    print(f"  {gray(str(workspace))}")

    fs = FileSystem(workspace)
    py_count = len(fs.tracked_files("*.py"))
    print(f"  {gray(str(py_count) + ' Python files indexed')}")

    print(f"  {gray('Commands: exit, new, delete, chats, switch <n>')}")
    print()

    conv = create_conversation(workspace)
    current_conv_id = conv["id"]
    messages = init_conversation(workspace)
    saved = len(list_conversations(workspace))
    if saved:
        print(f"  {gray(str(saved) + ' saved chat(s) — use chats / switch <n> to resume')}")
        print()

    total_tokens = 0
    last_user_input = None

    while True:
        try:
            user_input = input(f"  {green('You:')} ")
        except (KeyboardInterrupt, EOFError):
            print()
            _persist(workspace, current_conv_id, messages, last_user_input)
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            _persist(workspace, current_conv_id, messages, last_user_input)
            break

        if user_input.lower() in ("clear", "reset", "new"):
            conv = create_conversation(workspace)
            current_conv_id = conv["id"]
            messages = init_conversation(workspace)
            total_tokens = 0
            last_user_input = None
            print(f"\n  {gray('Chat cleared.')}\n")
            continue

        if user_input.lower() in ("delete", "del"):
            delete_conversation(workspace, current_conv_id)
            conv = create_conversation(workspace)
            current_conv_id = conv["id"]
            messages = init_conversation(workspace)
            total_tokens = 0
            last_user_input = None
            print(f"\n  {gray('Conversation deleted.')}\n")
            continue

        if user_input.lower() in ("chats", "list"):
            convs = list_conversations(workspace)
            if not convs:
                print(f"\n  {gray('No saved conversations.')}\n")
            else:
                print()
                for i, c in enumerate(convs, 1):
                    marker = " *" if c["id"] == current_conv_id else ""
                    print(f"  {gray(str(i) + '. ' + c.get('title', 'New chat') + marker)}")
                print(f"  {gray('Use switch <n> to resume a chat.')}\n")
            continue

        if user_input.lower().startswith("switch "):
            arg = user_input[7:].strip()
            conv = _switch_conversation(workspace, arg)
            if conv is None:
                print(f"\n  {red('Conversation not found.')}\n")
                continue
            current_conv_id = conv["id"]
            messages = init_conversation(workspace)
            messages.extend(conv.get("messages", []))
            msg_count = len([m for m in conv.get("messages", []) if m["role"] == "user"])
            title = conv.get("title", "Chat")
            print(f"\n  {gray('Switched to: ' + title + ' (' + str(msg_count) + ' messages)')}\n")
            continue

        last_user_input = user_input
        print()
        start = time.time()
        tool_count = 0
        turn_events: list[dict] = [{"type": "user", "content": user_input}]

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
                    turn_events.append(sanitize_transcript_event(event))

                elif event["type"] == "thinking":
                    content = event.get("content", "")
                    if content:
                        print(f"  {dim(content[:200])}")
                        print()
                        turn_events.append({"type": "thinking", "content": content})

                elif event["type"] == "response":
                    content = event.get("content", "")
                    if content:
                        sys.stdout.write(f"  {magenta('Agent:')} ")
                        stream_text(content)
                        turn_events.append({"type": "response", "content": content})
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

        _persist(workspace, current_conv_id, messages, user_input, turn_events)


if __name__ == "__main__":
    main()
