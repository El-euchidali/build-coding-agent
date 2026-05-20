import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


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


def chat(messages: list[dict], tools: list | None = None):
    """Send chat completion and return the assistant message object."""
    client = get_client()
    model = os.environ.get("INNKUBE_MODEL", "gemma4-31b-it")
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message


def smoke_test() -> str:
    """Send a simple request to verify the LLM connection."""
    message = chat(
        [{"role": "user", "content": "Reply with exactly: connection ok"}]
    )
    return message.content or ""

def count_tokens(messages: list[dict]) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    total = sum(len(str(m.get("content", ""))) for m in messages)
    return total // 4


def trim_context(messages: list[dict], max_tokens: int = 50000) -> list[dict]:
    """
    Trim conversation history when token budget is exceeded.

    Always keeps:
    - messages[0] — system prompt
    - messages[1] — original task
    - messages[-1] — most recent message

    Drops oldest middle messages until under budget.
    """
    if count_tokens(messages) <= max_tokens:
        return messages

    # Need at least 3 messages to trim (system, task, latest)
    while count_tokens(messages) > max_tokens and len(messages) > 3:
        # Keep [0] system, [1] task, drop [2] (oldest middle), keep [-1] latest
        messages = [messages[0], messages[1]] + messages[3:]

    return messages

# Module-level token counter
_total_tokens = {"prompt": 0, "completion": 0, "total": 0}


def reset_token_counter():
    """Reset the token counter at the start of a new task."""
    _total_tokens["prompt"] = 0
    _total_tokens["completion"] = 0
    _total_tokens["total"] = 0


def get_token_usage() -> dict:
    """Return accumulated token usage."""
    return dict(_total_tokens)


def chat(messages: list[dict], tools: list | None = None):
    """Send chat completion and return the assistant message object."""
    client = get_client()
    model = os.environ.get("INNKUBE_MODEL", "gemma4-31b-it")
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)

    # Accumulate token usage
    if response.usage:
        _total_tokens["prompt"]     += response.usage.prompt_tokens
        _total_tokens["completion"] += response.usage.completion_tokens
        _total_tokens["total"]      += response.usage.total_tokens

    return response.choices[0].message


if __name__ == "__main__":
    print(smoke_test())
