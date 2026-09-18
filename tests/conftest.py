"""Shared setup for the smoke tests.

`agent/__init__.py` imports `agent.agent`, which pulls in `agent.llm` and
`agent.rag` — so importing *any* `agent.*` module drags in openai, dotenv,
chromadb and sentence_transformers. None of them are needed to exercise the
pure logic under test, and two of them are actively unwanted here:
`load_dotenv()` would read a real `.env`, and `SentenceTransformer(...)` would
download a model on first use.

We install minimal stand-ins in `sys.modules` before any `agent.*` import. The
stub clients raise on construction, so if a test ever reaches code that tries
to build a real LLM, vector-store or embedding client, it fails loudly instead
of quietly going to the network.
"""

import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class ClientConstructionForbidden(AssertionError):
    """Raised when test code tries to build a real network-backed client."""


def _forbidden(label):
    class _Forbidden:
        def __init__(self, *args, **kwargs):
            raise ClientConstructionForbidden(
                f"tests must not construct a real {label} client"
            )

    return _Forbidden


def _install_stub(name, **attrs):
    module = types.ModuleType(name)
    module.__test_stub__ = True
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# Installed unconditionally, so the suite behaves identically whether or not the
# real packages happen to be installed on the machine running it.
_install_stub("openai", OpenAI=_forbidden("OpenAI"))
_install_stub("dotenv", load_dotenv=lambda *a, **k: False)
_install_stub(
    "chromadb",
    Client=_forbidden("chromadb"),
    PersistentClient=_forbidden("chromadb"),
)
_install_stub(
    "sentence_transformers",
    SentenceTransformer=_forbidden("SentenceTransformer"),
)


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch):
    """Guarantee no test can pick up a real endpoint or key from the environment."""
    for var in (
        "INNKUBE_API_KEY",
        "INNKUBE_BASE_URL",
        "INNKUBE_MODEL",
        "INNKUBE_MODEL_LIGHT",
        "INNKUBE_TIMEOUT",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def workspace(tmp_path):
    """An empty workspace directory for tools that take one."""
    return tmp_path


def write(path: Path, text: str) -> Path:
    """Write `text` to `path`, creating parents. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
