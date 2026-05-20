import re


def truncate_lines(text: str, max_lines: int = 45) -> str:
    """Keep the tail of long output (where tracebacks and failures usually are)."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - max_lines
    return f"... ({omitted} lines omitted) ...\n" + "\n".join(lines[-max_lines:])


def format_pytest_output(raw: str) -> str:
    """Truncate pytest output and add a short failure summary."""
    output = truncate_lines(raw)
    summary = extract_pytest_failure(raw)
    if summary:
        output = f"=== Failure summary ===\n{summary}\n\n=== Full output (truncated) ===\n{output}"
    return output


def extract_pytest_failure(output: str) -> str:
    """Pull the most useful lines from pytest output."""
    lines = output.splitlines()
    highlights: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            highlights.append(stripped)
        elif "AssertionError" in stripped:
            highlights.append(stripped)
        elif stripped.startswith("E   "):
            highlights.append(stripped)
        elif "assert " in stripped and "==" in stripped:
            highlights.append(stripped)

    if not highlights:
        for line in lines:
            if "FAILED" in line or "Error" in line:
                highlights.append(line.strip())
                if len(highlights) >= 5:
                    break

    return "\n".join(highlights[:8]) if highlights else ""
