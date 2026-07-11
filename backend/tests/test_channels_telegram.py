"""Tests for the Telegram adapter's message parsing (no network)."""

from __future__ import annotations

from vigilus.integrations.telegram import TelegramAdapter, _extract_attachments


def _adapter():
    # We never call start(); we only exercise the pure parsing helpers.
    return TelegramAdapter(token="fake", on_message=lambda _: None)


def _msg(*, chat_type="private", text="hi", username="alice", uid=1, caption=None):
    return {
        "message_id": 1,
        "chat": {"id": 99, "type": chat_type},
        "from": {"id": uid, "username": username, "first_name": "Alice"},
        "text": text,
        "caption": caption,
    }


class TestToInbound:
    def test_dm_is_not_group(self):
        ib = _adapter()._to_inbound(_msg())
        assert ib.platform == "telegram"
        assert ib.chat_id == "99"
        assert ib.user_id == "1"
        assert ib.user_name == "alice"
        assert ib.is_group is False
        assert ib.mentioned is False

    def test_group_detection(self):
        for t in ("group", "supergroup"):
            ib = _adapter()._to_inbound(_msg(chat_type=t))
            assert ib.is_group is True

    def test_mention_detection(self):
        a = _adapter()
        a._bot_username = "VigilusBot"
        ib = a._to_inbound(_msg(text="hey @vigilusbot do something"))
        assert ib.mentioned is True
        ib2 = a._to_inbound(_msg(text="no mention here"))
        assert ib2.mentioned is False

    def test_username_falls_back_to_first_name(self):
        ib = _adapter()._to_inbound(_msg(username=None))
        # first_name "Alice" is used when username is missing
        assert ib.user_name == "Alice"

    def test_caption_used_when_no_text(self):
        ib = _adapter()._to_inbound(_msg(text=None, caption="see this file", chat_type="private"))
        assert ib.text == "see this file"


class TestAttachments:
    def test_document_extracted(self):
        msg = _msg(text=None, caption="report")
        msg["document"] = {
            "file_id": "fid1",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "file_size": 1234,
        }
        atts = _extract_attachments(msg)
        assert len(atts) == 1
        assert atts[0]["filename"] == "report.pdf"
        assert atts[0]["file_id"] == "fid1"

    def test_photo_picks_largest(self):
        msg = _msg(text=None)
        msg["photo"] = [
            {"file_id": "small", "file_size": 100},
            {"file_id": "big", "file_size": 5000},
            {"file_id": "mid", "file_size": 1000},
        ]
        atts = _extract_attachments(msg)
        assert len(atts) == 1
        assert atts[0]["file_id"] == "big"

    def test_no_attachments(self):
        assert _extract_attachments(_msg()) == []


class TestConcurrentDispatch:
    """A paused message turn must not block an Approve/Deny callback (deadlock fix)."""

    async def test_callback_not_blocked_by_in_flight_turn(self):
        import asyncio

        release = asyncio.Event()
        message_started = asyncio.Event()
        resolved: list[tuple[str, bool, str]] = []

        async def slow_on_message(_inbound):
            # Simulate a turn that pauses waiting for JIT approval.
            message_started.set()
            await release.wait()

        adapter = TelegramAdapter(token="fake", on_message=slow_on_message)

        # answerCallbackQuery / editMessageText would hit the network — stub them.
        async def fake_call(method, payload=None):
            return {"ok": True, "result": {}}

        adapter._call = fake_call  # type: ignore[assignment]

        async def resolver(request_id, approved, approver):
            resolved.append((request_id, approved, approver))

        adapter._jit_resolver = resolver

        # 1. Dispatch a message turn that blocks.
        adapter._dispatch(slow_on_message(None))
        await asyncio.wait_for(message_started.wait(), timeout=1)

        # 2. While it's blocked, an Approve tap must still be handled.
        cbq = {
            "id": "cbq1",
            "data": "jit:approve:req-123",
            "from": {"id": 7, "username": "cam"},
            "message": {"chat": {"id": 99}, "message_id": 5},
        }
        adapter._dispatch(adapter._handle_callback(cbq))

        # The callback resolves promptly even though the turn is still paused.
        for _ in range(50):
            if resolved:
                break
            await asyncio.sleep(0.01)
        assert resolved == [("req-123", True, "telegram:cam")]
        assert not release.is_set()  # the message turn is still blocked

        release.set()  # let the blocked turn finish so the task can drain

    async def test_handler_error_is_swallowed(self):
        async def boom():
            raise RuntimeError("kaboom")

        adapter = TelegramAdapter(token="fake", on_message=lambda _: None)
        # Should not raise — _run_handler logs and swallows.
        await adapter._run_handler(boom())
