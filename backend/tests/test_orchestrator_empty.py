"""Orchestrator loop behavior when the LLM returns an empty final reply.

Reasoning models occasionally return empty content (e.g. the whole token
budget went to reasoning). The loop must retry once with a nudge and, if the
reply is still empty, fall back to the last operator report instead of
persisting a blank assistant message.
"""

from __future__ import annotations

import json

from vigilus.api.chat import _run_orchestrator
from vigilus.providers.base import LLMMessage, LLMResponse


class ScriptedProvider:
    """Returns canned responses in order; records every call's messages."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self, messages, *, system=None, tools=None, temperature=0.0, **kwargs
    ) -> LLMResponse:
        self.calls.append(list(messages))
        return LLMResponse(content=self.replies.pop(0))


def _delegation_json() -> str:
    return json.dumps(
        {
            "delegate": "Security Monitor",
            "task": "Run an nmap scan of 10.0.0.0/24",
        }
    )


async def _fake_delegation(*args, **kwargs):
    return {
        "status": "success",
        "operator": "Security Monitor",
        "response": "Scan complete: 3 hosts up, ports 22/80 open.",
        "tool_calls": [],
    }


async def test_empty_final_reply_is_retried(db_session, monkeypatch):
    monkeypatch.setattr("vigilus.api.chat.execute_delegation", _fake_delegation)
    provider = ScriptedProvider(
        [
            _delegation_json(),  # 1: delegate
            "",  # 2: empty final reply → nudge + retry
            "All done — 3 hosts up.",  # 3: recovered summary
        ]
    )

    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="scan my network")],
        provider,
        "system prompt",
        db=db_session,
    )

    final = msgs[-1]
    assert final["role"] == "assistant"
    assert final["content"] == "All done — 3 hosts up."
    # The retry call must carry the nudge as the newest message.
    assert len(provider.calls) == 3
    assert "previous reply was empty" in str(provider.calls[2][-1].content)


async def test_empty_twice_falls_back_to_operator_report(db_session, monkeypatch):
    monkeypatch.setattr("vigilus.api.chat.execute_delegation", _fake_delegation)
    provider = ScriptedProvider([_delegation_json(), "", ""])

    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="scan my network")],
        provider,
        "system prompt",
        db=db_session,
    )

    final = msgs[-1]
    assert final["role"] == "assistant"
    assert final["content"].strip() != ""
    assert "Scan complete: 3 hosts up" in final["content"]


async def test_empty_without_delegation_gets_notice(db_session):
    provider = ScriptedProvider(["", "  \n "])

    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="hello")],
        provider,
        "system prompt",
        db=db_session,
    )

    final = msgs[-1]
    assert final["role"] == "assistant"
    assert "empty reply" in final["content"]


async def test_nonempty_reply_unaffected(db_session):
    provider = ScriptedProvider(["Just a normal answer."])

    msgs = await _run_orchestrator(
        [LLMMessage(role="user", content="hello")],
        provider,
        "system prompt",
        db=db_session,
    )

    assert len(msgs) == 1
    assert msgs[0] == {"role": "assistant", "content": "Just a normal answer."}
