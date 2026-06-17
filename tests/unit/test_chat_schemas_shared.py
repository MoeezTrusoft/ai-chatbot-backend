"""Tests for shared chat schemas — circular import fix."""
from __future__ import annotations

from uuid import uuid4

from bookcraft.components.response.chat_schemas import ChatTurnRequest, ChatTurnResponse
from bookcraft.api.chat import ChatTurnRequest as ApiRequest, ChatTurnResponse as ApiResponse


class TestChatSchemasShared:
    def test_chat_turn_request_importable_from_shared(self):
        assert ChatTurnRequest is not None

    def test_chat_turn_response_importable_from_shared(self):
        assert ChatTurnResponse is not None

    def test_api_re_exports_same_classes(self):
        """api.chat re-exports from the shared module — should be the same objects."""
        assert ApiRequest is ChatTurnRequest
        assert ApiResponse is ChatTurnResponse

    def test_chat_turn_response_fields(self):
        resp = ChatTurnResponse(
            thread_id=uuid4(),
            bubbles=[],
            intent=None,
            language_status="en",
        )
        assert resp.blocked is False
        assert resp.input_disabled is False
        assert resp.system_message is None

    def test_chat_turn_request_fields(self):
        req = ChatTurnRequest(message="hello")
        assert req.thread_id is None
        assert req.customer_id is None
        assert req.correlation_id is None
        assert req.attachments == []

    def test_chat_turn_response_debug_event_ids_default(self):
        resp = ChatTurnResponse(
            thread_id=uuid4(),
            bubbles=[],
            intent=None,
            language_status="en",
        )
        assert resp.debug_event_ids == []

    def test_chat_turn_response_action_events_default(self):
        resp = ChatTurnResponse(
            thread_id=uuid4(),
            bubbles=[],
            intent=None,
            language_status="en",
        )
        assert resp.action_events == []

    def test_chat_turn_request_message_required(self):
        """A turn with neither text nor attachment must raise."""
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ChatTurnRequest()  # type: ignore[call-arg]

    def test_chat_turn_request_empty_message_without_attachment_rejected(self):
        """An empty message and no attachment is not a valid turn."""
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ChatTurnRequest(message="")
        with pytest.raises(ValidationError):
            ChatTurnRequest(message="   ")

    def test_chat_turn_request_attachment_only_allowed(self):
        """An attachment-only turn (empty message + a file) is valid."""
        req = ChatTurnRequest(
            message="",
            attachments=[{"filename": "manuscript.docx"}],
        )
        assert req.message == ""
        assert req.attachments[0].filename == "manuscript.docx"
