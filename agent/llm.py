import os
import re
import time
from types import SimpleNamespace
from dotenv import load_dotenv
from openai import OpenAI

_SECTION_MARKERS = ("## ", "[CONTEXT TRIMMED]")


def _remove_section_by_header(content: str, header: str) -> str:
    """Remove a labeled block from system content (header through next section)."""
    if header not in content:
        return content
    idx = content.index(header)
    before = content[:idx].rstrip()
    remainder = content[idx + len(header) :]
    match = re.search(
        r"\n(?:" + "|".join(re.escape(m) for m in _SECTION_MARKERS) + ")",
        remainder,
    )
    if match:
        after = remainder[match.start() :]
        return (before + after).strip()
    return before


def consolidate_system_messages(messages: list[dict]) -> list[dict]:
    """Merge all system messages into a single message at index 0."""
    system_parts: list[str] = []
    rest: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            part = msg.get("content", "")
            if part:
                system_parts.append(part)
        else:
            rest.append(msg)
    if not system_parts:
        return list(messages)
    return [{"role": "system", "content": "\n\n".join(system_parts)}] + rest


def set_system_section(messages: list[dict], header: str, section: str) -> list[dict]:
    """Replace a labeled section inside the leading system message."""
    messages = consolidate_system_messages(messages)
    if not messages:
        messages = [{"role": "system", "content": ""}]
    elif messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": ""})

    content = _remove_section_by_header(messages[0].get("content", ""), header)
    if section:
        content = f"{content.rstrip()}\n\n{section}".strip()
    messages[0] = {"role": "system", "content": content}
    return messages

load_dotenv()

_total_tokens = {"prompt": 0, "completion": 0, "total": 0}


def get_client() -> OpenAI:
    api_key = os.environ.get("INNKUBE_API_KEY")
    if not api_key:
        raise ValueError(
            "INNKUBE_API_KEY not set. Copy .env.example to .env and add your key."
        )
    base_url = os.environ.get(
        "INNKUBE_BASE_URL", "https://llms.innkube.fim.uni-passau.de"
    )
    timeout = float(os.environ.get("INNKUBE_TIMEOUT", "600"))
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def reset_token_counter() -> None:
    """Reset the token counter at the start of a new task."""
    _total_tokens["prompt"] = 0
    _total_tokens["completion"] = 0
    _total_tokens["total"] = 0


def get_token_usage() -> dict:
    """Return accumulated token usage."""
    return dict(_total_tokens)


def count_tokens(messages: list[dict]) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    total = 0
    for m in messages:
        total += len(str(m.get("content", "")))
        if m.get("tool_calls"):
            total += len(str(m["tool_calls"]))
    return total // 4


def trim_context(
    messages: list[dict], max_tokens: int = 50000, keep_recent: int = 24
) -> list[dict]:
    """
    Trim conversation when over token budget.
    
    Keeps: system prompt, original task, recent messages.
    Dropped messages are summarized into a compact note so the agent
    does not lose critical context.
    """
    if count_tokens(messages) <= max_tokens:
        return messages

    if len(messages) <= 2 + keep_recent:
        return messages

    # Split into: [system, task, ...middle..., ...recent...]
    head = messages[:2]
    middle = messages[2:-keep_recent] if len(messages) > 2 + keep_recent else []
    recent = messages[-keep_recent:]

    # Summarize dropped middle messages into a compact note
    if middle:
        tools_used = []
        files_read = []
        files_edited = []
        errors = []

        for msg in middle:
            content = msg.get("content", "")
            if msg.get("role") == "tool":
                # Extract key info from tool results
                if "Error:" in content:
                    errors.append(content[:100])
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    name = tc.get("function", {}).get("name", "")
                    args_str = tc.get("function", {}).get("arguments", "")
                    tools_used.append(name)
                    if name in ("read_file", "view_file_range", "file_outline", "get_function"):
                        try:
                            import json
                            a = json.loads(args_str)
                            files_read.append(a.get("filepath", ""))
                        except Exception:
                            pass
                    elif name in ("write_file", "str_replace", "edit_and_verify"):
                        try:
                            import json
                            a = json.loads(args_str)
                            files_edited.append(a.get("filepath", ""))
                        except Exception:
                            pass

        summary = "[CONTEXT TRIMMED] Earlier in this conversation:\n"
        if tools_used:
            summary += f"- Tools used: {', '.join(dict.fromkeys(tools_used))}\n"
        if files_read:
            summary += f"- Files read: {', '.join(dict.fromkeys(files_read))}\n"
        if files_edited:
            summary += f"- Files edited: {', '.join(dict.fromkeys(files_edited))}\n"
        if errors:
            summary += f"- Errors encountered: {len(errors)}\n"
        summary += f"- {len(middle)} messages trimmed to save context space.\n"

        head = set_system_section(head, "[CONTEXT TRIMMED]", summary)

    trimmed = head + recent

    # If still over budget, drop oldest recent messages
    while count_tokens(trimmed) > max_tokens and len(trimmed) > 4:
        trimmed = trimmed[:3] + trimmed[4:]

    return trimmed


def chat(
    messages: list[dict],
    tools: list | None = None,
    use_light: bool = False,
    temperature: float | None = None,
):
    """
    Send chat completion with automatic retry on transient errors.
    Retries up to 3 times with exponential backoff.

    `temperature` is passed through when provided — use a low value (e.g. 0.0)
    for deterministic editing/fixing steps to reduce run-to-run variance.
    """
    client = get_client()
    model = os.environ.get("INNKUBE_MODEL", "gemma4-31b-it")

    if use_light:
        light_model = os.environ.get("INNKUBE_MODEL_LIGHT", "")
        if light_model:
            model = light_model

    messages = consolidate_system_messages(messages)
    kwargs: dict = {"model": model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    max_attempts = max(1, int(os.environ.get("INNKUBE_RETRIES", "3")))
    last_error = None
    for attempt in range(max_attempts):
        try:
            response = client.chat.completions.create(**kwargs)

            if response.usage:
                _total_tokens["prompt"] += response.usage.prompt_tokens
                _total_tokens["completion"] += response.usage.completion_tokens
                _total_tokens["total"] += response.usage.total_tokens

            return response.choices[0].message

        except Exception as e:
            last_error = e
            error_str = str(e)
            error_lower = error_str.lower()
            # Retry on transient errors (rate limit, server error, timeout)
            transient = any(
                marker in error_lower
                for marker in [
                    "429",
                    "500",
                    "502",
                    "503",
                    "timeout",
                    "timed out",
                    "connection",
                    "temporarily unavailable",
                ]
            )
            if transient and attempt < max_attempts - 1:
                wait = (attempt + 1) * 5
                print(f"  [LLM] Retry {attempt + 1}/{max_attempts} after {wait}s — {type(e).__name__}: {error_str[:100]}")
                time.sleep(wait)
                continue
            # Non-transient error — raise immediately
            raise

    raise last_error


def _build_stream_message(content_parts: list[str], tool_calls_acc: dict[int, dict]):
    """Assemble a message object compatible with agent._get_tool_calls."""
    tool_calls_list = None
    if tool_calls_acc:
        tool_calls_list = []
        for idx in sorted(tool_calls_acc):
            tc = tool_calls_acc[idx]
            tool_calls_list.append(
                SimpleNamespace(
                    id=tc["id"] or f"call_{idx}",
                    function=SimpleNamespace(
                        name=tc["name"],
                        arguments=tc["arguments"],
                    ),
                )
            )
    text = "".join(content_parts)
    return SimpleNamespace(content=text or None, tool_calls=tool_calls_list)


def chat_stream(
    messages: list[dict],
    tools: list | None = None,
    use_light: bool = False,
    temperature: float | None = None,
):
    """
    Streaming chat for the UI only. Yields ('token', str) chunks, then ('done', message).
    Does not affect the blocking chat() used by benchmarks and evaluators.
    """
    client = get_client()
    model = os.environ.get("INNKUBE_MODEL", "gemma4-31b-it")
    if use_light:
        light_model = os.environ.get("INNKUBE_MODEL_LIGHT", "")
        if light_model:
            model = light_model

    messages = consolidate_system_messages(messages)
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    max_attempts = max(1, int(os.environ.get("INNKUBE_RETRIES", "3")))
    last_error = None
    for attempt in range(max_attempts):
        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict] = {}
        got_usage = False
        try:
            try:
                stream = client.chat.completions.create(**kwargs)
            except TypeError:
                kwargs.pop("stream_options", None)
                stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage and (usage.total_tokens or usage.prompt_tokens or usage.completion_tokens):
                    got_usage = True
                    _total_tokens["prompt"] += usage.prompt_tokens or 0
                    _total_tokens["completion"] += usage.completion_tokens or 0
                    _total_tokens["total"] += usage.total_tokens or 0
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    content_parts.append(delta.content)
                    yield ("token", delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_acc[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments
            if not got_usage:
                response_text = "".join(content_parts)
                prompt_est = count_tokens(messages)
                completion_est = count_tokens([{"content": response_text}]) if response_text else 0
                _total_tokens["prompt"] += prompt_est
                _total_tokens["completion"] += completion_est
                _total_tokens["total"] += prompt_est + completion_est
            yield ("done", _build_stream_message(content_parts, tool_calls_acc))
            return
        except Exception as e:
            last_error = e
            error_lower = str(e).lower()
            transient = any(
                marker in error_lower
                for marker in (
                    "429", "500", "502", "503", "timeout", "timed out",
                    "connection", "temporarily unavailable",
                )
            )
            if transient and attempt < max_attempts - 1:
                wait = (attempt + 1) * 5
                print(
                    f"  [LLM stream] Retry {attempt + 1}/{max_attempts} after {wait}s — "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
                time.sleep(wait)
                continue
            raise

    raise last_error


def smoke_test() -> str:
    """Send a simple request to verify the LLM connection."""
    message = chat(
        [{"role": "user", "content": "Reply with exactly: connection ok"}]
    )
    return message.content or ""


if __name__ == "__main__":
    print(smoke_test())
