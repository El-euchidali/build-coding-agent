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


if __name__ == "__main__":
    print(smoke_test())
