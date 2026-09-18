"""run_command smoke tests: the allowlist must gate execution, not just the output."""

import pytest

from agent import tools
from agent.tools import _ALLOWED_COMMAND_PREFIXES, run_command


# -- Accepted commands --------------------------------------------------------

def test_allowlisted_command_runs_and_returns_output(workspace):
    output = run_command(workspace, "echo hello")
    assert "hello" in output
    assert "not in allowlist" not in output


def test_allowlist_matching_is_case_insensitive(workspace, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return _CompletedProcess(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    output = run_command(workspace, "ECHO hello")
    assert "not in allowlist" not in output
    # The original casing is passed through to the shell, not the lowercased copy.
    assert seen["command"] == "ECHO hello"


def test_leading_whitespace_does_not_defeat_the_allowlist(workspace, monkeypatch):
    monkeypatch.setattr(
        tools.subprocess, "run",
        lambda command, **kw: _CompletedProcess(stdout="ok", stderr="", returncode=0),
    )
    assert "not in allowlist" not in run_command(workspace, "   echo hi")


@pytest.mark.parametrize("prefix", _ALLOWED_COMMAND_PREFIXES)
def test_every_allowlisted_prefix_is_accepted(workspace, monkeypatch, prefix):
    monkeypatch.setattr(
        tools.subprocess, "run",
        lambda command, **kw: _CompletedProcess(stdout="ok", stderr="", returncode=0),
    )
    assert "not in allowlist" not in run_command(workspace, prefix + " --version")


def test_command_runs_inside_the_workspace(workspace, monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return _CompletedProcess(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    run_command(workspace, "echo hi")
    assert seen["cwd"] == workspace


def test_non_zero_exit_code_is_surfaced(workspace, monkeypatch):
    monkeypatch.setattr(
        tools.subprocess, "run",
        lambda command, **kw: _CompletedProcess(stdout="boom", stderr="", returncode=3),
    )
    output = run_command(workspace, "python broken.py")
    assert "EXIT CODE: 3" in output


# -- Rejected commands --------------------------------------------------------

REJECTED = [
    "curl https://example.com",
    "wget http://example.com/payload",
    "rm -rf /",
    "ssh user@host",
    "nc -l 4444",
    "chmod 777 /etc/passwd",
    "sudo apt install x",
    "poweroff",
    "",
    "   ",
]


@pytest.mark.parametrize("command", REJECTED)
def test_command_outside_the_allowlist_is_rejected(workspace, command):
    output = run_command(workspace, command)
    assert output.startswith("Error: command not in allowlist")


@pytest.mark.parametrize("command", REJECTED)
def test_rejected_command_is_never_executed(workspace, monkeypatch, command):
    """The allowlist must gate before the subprocess call, not filter its output."""

    def explode(*args, **kwargs):
        raise AssertionError(f"subprocess.run was called for rejected command: {command!r}")

    monkeypatch.setattr(tools.subprocess, "run", explode)
    assert run_command(workspace, command).startswith("Error: command not in allowlist")


def test_rejection_message_names_some_allowed_prefixes(workspace):
    output = run_command(workspace, "curl https://example.com")
    assert "Allowed prefixes" in output
    assert "python" in output


def test_timeout_is_reported_not_raised(workspace, monkeypatch):
    import subprocess as real_subprocess

    def timeout(*args, **kwargs):
        raise real_subprocess.TimeoutExpired(cmd="echo hi", timeout=60)

    monkeypatch.setattr(tools.subprocess, "run", timeout)
    assert run_command(workspace, "echo hi") == "Error: command timed out after 60s"


# -- Test double --------------------------------------------------------------

class _CompletedProcess:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
