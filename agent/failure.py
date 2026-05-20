def tests_passed_in_history(messages: list[dict]) -> bool:
    """Check if any tool result reported all tests passed."""
    for msg in reversed(messages):
        if msg.get("role") == "tool" and "* ALL TESTS PASSED" in msg.get("content", ""):
            return True
    return False


def classify_failure(result, messages: list[dict] | None = None) -> str:
    """Assign a failure category for metrics and debugging."""
    if result.success:
        return "success"

    error = (result.error or "").lower()

    if error == "max_iterations":
        return "max_iterations"
    if error == "stopped_early":
        return "stopped_early"
    if "humaneval_check_failed" in error:
        return "humaneval_check_failed"
    if messages and not tests_passed_in_history(messages):
        if "str_replace" in error or "not found" in error:
            return "edit_error"
        return "tests_failed"
    if error == "tests_failed":
        return "tests_failed"

    return "unknown"
