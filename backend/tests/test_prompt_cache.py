"""Anthropic prompt-cache breakpoints, and providers that do not cache."""

from types import SimpleNamespace

from vigilus.providers.anthropic_provider import AnthropicProvider, _usage_from_message
from vigilus.providers.base import LLMMessage, ToolSpec
from vigilus.providers.openai_provider import OpenAIProvider


def _provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="sk-test", default_model="claude-opus-4-8")


def test_anthropic_cache_breakpoints_leave_volatile_text_uncached():
    raw = [{"type": "text", "text": "earlier"}]
    earlier = LLMMessage(role="assistant", content="earlier", raw={"content": raw})
    tools = [
        ToolSpec(name="ssh", description="run", input_schema={"type": "object"}),
        ToolSpec(name="read", description="read", input_schema={"type": "object"}),
    ]
    kwargs = _provider()._build_kwargs(
        [earlier, LLMMessage(role="user", content="now")],
        system="🟢 server just changed",
        tools=tools,
        temperature=0.0,
        max_tokens=16,
        model="claude-haiku-4-5",
        cached_system="stable identity",
        cache_conversation=True,
    )

    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["system"][0]["text"] == "stable identity"
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["system"][1]["text"] == "🟢 server just changed"
    assert "cache_control" not in kwargs["system"][1]

    assert kwargs["tools"][0]["name"] == "ssh"
    assert "cache_control" not in kwargs["tools"][0]
    assert kwargs["tools"][1]["name"] == "read"
    assert kwargs["tools"][1]["cache_control"] == {"type": "ephemeral"}

    last = kwargs["messages"][-1]["content"]
    assert last[0]["text"] == "now"
    assert last[0]["cache_control"] == {"type": "ephemeral"}
    # The stored raw blocks are what the next turn reloads. Marking the
    # request must not write cache_control into them.
    assert "cache_control" not in raw[0]


def test_anthropic_without_opt_in_sends_a_plain_prompt():
    tools = [ToolSpec(name="ssh", description="run", input_schema={"type": "object"})]
    kwargs = _provider()._build_kwargs(
        [LLMMessage(role="user", content="hi")],
        system="whole prompt",
        tools=tools,
        temperature=0.0,
        max_tokens=16,
    )

    assert kwargs["system"] == "whole prompt"
    assert kwargs["model"] == "claude-opus-4-8"
    assert "cache_control" not in kwargs["tools"][0]
    assert kwargs["messages"][0]["content"] == "hi"


def test_marking_the_last_message_copies_raw_blocks():
    raw = [{"type": "text", "text": "kept"}]
    msg = LLMMessage(role="assistant", content="kept", raw={"content": raw})
    kwargs = _provider()._build_kwargs(
        [msg],
        system=None,
        tools=None,
        temperature=0.0,
        max_tokens=16,
        cache_conversation=True,
    )
    sent = kwargs["messages"][0]["content"]
    assert sent[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in raw[0]
    assert "system" not in kwargs


def test_usage_reports_cache_tokens_only_when_present():
    with_cache = _usage_from_message(
        SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=50,
        )
    )
    assert with_cache == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": 100,
        "cache_write_tokens": 50,
    }
    plain = _usage_from_message(SimpleNamespace(input_tokens=3, output_tokens=1))
    assert plain == {"input_tokens": 3, "output_tokens": 1}


def test_openai_joins_the_cached_prefix_and_honors_model():
    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o")
    kwargs = provider._build_kwargs(
        [LLMMessage(role="user", content="hi")],
        system="volatile date",
        tools=None,
        temperature=0.0,
        max_tokens=16,
        model="gpt-4o-mini",
        cached_system="stable identity",
        cache_conversation=True,
    )
    assert kwargs["model"] == "gpt-4o-mini"
    assert kwargs["messages"][0]["content"] == "stable identity\n\nvolatile date"
    assert kwargs["messages"][1]["content"] == "hi"
