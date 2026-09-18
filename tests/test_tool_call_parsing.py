"""Fallback tool-call parsing: used when the model emits JSON instead of tool_calls."""

import pytest

from agent.tools import parse_tool_call_fallback


# -- Well-formed input --------------------------------------------------------

def test_bare_json_object_with_args():
    parsed = parse_tool_call_fallback('{"name": "read_file", "args": {"filepath": "a.py"}}')
    assert parsed == [{"name": "read_file", "arguments": {"filepath": "a.py"}}]


def test_arguments_key_is_accepted_as_well_as_args():
    parsed = parse_tool_call_fallback(
        '{"name": "read_file", "arguments": {"filepath": "a.py"}}'
    )
    assert parsed == [{"name": "read_file", "arguments": {"filepath": "a.py"}}]


def test_missing_args_defaults_to_empty_dict():
    assert parse_tool_call_fallback('{"name": "git_status"}') == [
        {"name": "git_status", "arguments": {}}
    ]


def test_surrounding_whitespace_is_tolerated():
    parsed = parse_tool_call_fallback('\n\n  {"name": "git_status"}  \n')
    assert parsed == [{"name": "git_status", "arguments": {}}]


def test_json_fenced_block():
    text = 'Here you go:\n```json\n{"name": "run_tests", "args": {}}\n```\nDone.'
    assert parse_tool_call_fallback(text) == [{"name": "run_tests", "arguments": {}}]


def test_plain_fenced_block():
    text = '```\n{"name": "run_tests", "args": {}}\n```'
    assert parse_tool_call_fallback(text) == [{"name": "run_tests", "arguments": {}}]


def test_list_of_calls_is_preserved_in_order():
    text = '[{"name": "read_file", "args": {"filepath": "a.py"}}, {"name": "git_status"}]'
    assert parse_tool_call_fallback(text) == [
        {"name": "read_file", "arguments": {"filepath": "a.py"}},
        {"name": "git_status", "arguments": {}},
    ]


def test_list_inside_a_json_fence():
    text = '```json\n[{"name": "git_status"}, {"name": "git_diff"}]\n```'
    parsed = parse_tool_call_fallback(text)
    assert [call["name"] for call in parsed] == ["git_status", "git_diff"]


def test_malformed_entries_in_a_list_are_dropped():
    text = '[{"name": "git_status"}, {"no_name": 1}, "junk", 42]'
    assert parse_tool_call_fallback(text) == [{"name": "git_status", "arguments": {}}]


# -- Malformed / unparseable input returns None, never raises ------------------

@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "I will now read the file.",
        "{not json at all}",
        '{"name": "read_file",}',
        '{"name": "read_file"',
        '{"tool": "read_file"}',
        '"just a string"',
        "42",
        "null",
        '```json\n{"name": "read_file"\n```',
    ],
)
def test_unparseable_input_returns_none(text):
    assert parse_tool_call_fallback(text) is None


def test_empty_json_list_returns_empty_list():
    assert parse_tool_call_fallback("[]") == []


@pytest.mark.parametrize("text", ["[1, 2, 3]", '["a", "b"]', "[null]"])
def test_json_list_of_non_calls_yields_no_calls(text):
    """Valid JSON, wrong shape: every element is filtered out, leaving an empty list.

    This is distinct from None (unparseable) -- callers see "no tool calls" either way.
    """
    assert parse_tool_call_fallback(text) == []


def test_parser_never_raises_on_arbitrary_text():
    samples = [
        "```json```",
        "```",
        "{",
        "}",
        "\x00\x01",
        "{'name': 'read_file'}",  # single quotes are not valid JSON
    ]
    for sample in samples:
        parse_tool_call_fallback(sample)
