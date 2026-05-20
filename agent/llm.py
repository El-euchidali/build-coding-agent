import os

from dotenv import load_dotenv
from openai import OpenAI

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
    return OpenAI(api_key=api_key, base_url=base_url)


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

    Always keeps system prompt, original task, and the most recent messages.
    """
    if count_tokens(messages) <= max_tokens:
        return messages

    if len(messages) <= 2 + keep_recent:
        return messages

    trimmed = [messages[0], messages[1]] + messages[-keep_recent:]
    while count_tokens(trimmed) > max_tokens and len(trimmed) > 4:
        # Drop oldest middle message (never system or task)
        trimmed = [trimmed[0], trimmed[1]] + trimmed[3:]

    return trimmed


def chat(messages: list[dict], tools: list | None = None):
    """Send chat completion and return the assistant message object."""
    client = get_client()
    model = os.environ.get("INNKUBE_MODEL", "gemma4-31b-it")
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)

    if response.usage:
        _total_tokens["prompt"] += response.usage.prompt_tokens
        _total_tokens["completion"] += response.usage.completion_tokens
        _total_tokens["total"] += response.usage.total_tokens

    return response.choices[0].message


def smoke_test() -> str:
    """Send a simple request to verify the LLM connection."""
    message = chat(
        [{"role": "user", "content": "Reply with exactly: connection ok"}]
    )
    return message.content or ""


if __name__ == "__main__":
    print(smoke_test())
