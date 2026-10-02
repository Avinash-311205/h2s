"""Tests for Module 2 -> Module 3 wiring: the published envelope and the consumer.

The envelope matters more than it looks. Module 4 records the ``event_id`` it
received so a gap score can be traced back to the Module 3 record that produced
it, so publishing a bare payload would silently destroy provenance.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from app.core.config import settings
from app.events.consumer import UnderstandingConsumer
from app.events.publisher import (
    SOURCE_MODULE,
    CivicEvent,
    EventOutboxRepository,
    OutboxEventPublisher,
)


class RecordingTransport:
    """Captures published messages instead of touching Redis."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.messages.append((channel, message))


@pytest.fixture
def publisher(db_session) -> tuple[OutboxEventPublisher, RecordingTransport]:
    transport = RecordingTransport()
    repository = EventOutboxRepository(db_session)
    return OutboxEventPublisher(repository, transport=transport), transport


@pytest.fixture
def event() -> CivicEvent:
    return CivicEvent(
        event_type="CIVIC_RECORD_PROCESSED",
        channel=settings.event_channel,
        payload={
            "request_id": "REQ-2026-000042",
            "category": "WATER",
            "severity": 4,
            "description": "no water",
        },
        request_id="REQ-2026-000042",
        issue_group_id="group-1",
    )


# --- publisher ----------------------------------------------------------------
class TestPublishedEnvelope:
    def test_publishes_a_full_envelope_not_a_bare_payload(
        self, publisher, event: CivicEvent, db_session
    ) -> None:
        pub, transport = publisher
        pub.publish(event)

        channel, raw = transport.messages[0]
        envelope = json.loads(raw)

        assert channel == "civic.records.processed"
        # Provenance Module 4 depends on.
        assert envelope["event_id"] == event.event_id
        assert envelope["request_id"] == "REQ-2026-000042"
        assert envelope["correlation_id"] == "REQ-2026-000042"
        assert envelope["issue_group_id"] == "group-1"
        assert envelope["event"] == "CIVIC_RECORD_PROCESSED"
        assert envelope["event_type"] == "CIVIC_RECORD_PROCESSED"
        assert envelope["channel"] == "civic.records.processed"
        assert envelope["source_module"] == SOURCE_MODULE
        assert envelope["schema_version"]
        assert envelope["timestamp"]
        # The payload survives untouched underneath the envelope.
        assert envelope["payload"] == event.payload

    def test_payload_is_not_published_at_the_top_level(
        self, publisher, event: CivicEvent
    ) -> None:
        # Regression guard: publishing record.payload directly meant Module 4
        # received no event_id at all.
        pub, transport = publisher
        pub.publish(event)

        envelope = json.loads(transport.messages[0][1])
        assert "payload" in envelope
        assert envelope["payload"]["request_id"] == "REQ-2026-000042"
        assert "event_id" not in envelope["payload"]

    def test_each_publish_gets_its_own_event_id(self, publisher) -> None:
        pub, transport = publisher
        pub.publish(CivicEvent(event_type="CIVIC_RECORD_PROCESSED",
                               channel=settings.event_channel, payload={"request_id": "A"}))
        pub.publish(CivicEvent(event_type="CIVIC_RECORD_PROCESSED",
                               channel=settings.event_channel, payload={"request_id": "B"}))

        ids = {json.loads(m[1])["event_id"] for m in transport.messages}
        assert len(ids) == 2


# --- consumer -----------------------------------------------------------------
class _StubResponse:
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> Any:
        return self._body


class TestConsumer:
    @pytest.fixture
    def calls(self, monkeypatch) -> list[tuple[str, dict]]:
        recorded: list[tuple[str, dict]] = []

        import httpx

        def fake_post(url: str, *, json: Any, timeout: float) -> _StubResponse:
            recorded.append((url, json))
            return _StubResponse(200, {"idempotent": False, "event_published": True})

        monkeypatch.setattr(httpx, "post", fake_post)
        return recorded

    @pytest.fixture
    def envelope(self) -> str:
        return json.dumps(
            {
                "event_id": "EVT-M2-0001",
                "event": "UNDERSTANDING_COMPLETED",
                "correlation_id": "REQ-2026-000042",
                "schema_version": "1.0",
                "payload": {"request_id": "REQ-2026-000042", "category": "WATER", "severity": 4},
            }
        )

    def test_completion_event_is_posted_to_our_own_endpoint(
        self, calls, envelope: str
    ) -> None:
        consumer = UnderstandingConsumer()

        request_id = consumer.handle(envelope)

        assert request_id == "REQ-2026-000042"
        url, body = calls[0]
        # Module 3 must reuse the endpoint it already exposes rather than
        # duplicating the processing logic in the consumer thread.
        assert url == "http://127.0.0.1:8003/api/v1/civic/process"
        assert body["request_id"] == "REQ-2026-000042"
        assert consumer.processed == 1

    def test_correlation_id_wins_over_a_conflicting_payload_request_id(
        self, calls
    ) -> None:
        raw = json.dumps(
            {
                "event": "UNDERSTANDING_COMPLETED",
                "correlation_id": "REQ-CORRECT",
                "payload": {"request_id": "REQ-WRONG", "category": "WATER"},
            }
        )

        UnderstandingConsumer().handle(raw)

        assert calls[0][1]["request_id"] == "REQ-CORRECT"

    def test_unrelated_event_types_are_ignored(self, calls) -> None:
        raw = json.dumps({"event": "SOMETHING_ELSE", "payload": {"request_id": "REQ-1"}})

        assert UnderstandingConsumer().handle(raw) is None
        assert calls == []

    @pytest.mark.parametrize(
        "raw",
        ["{not json", "[]", json.dumps({"event": "UNDERSTANDING_COMPLETED"})],
        ids=["bad_json", "not_an_object", "no_request_id"],
    )
    def test_unusable_events_are_dropped_without_calling_the_api(
        self, calls, raw: str
    ) -> None:
        assert UnderstandingConsumer().handle(raw) is None
        assert calls == []

    def test_a_replay_is_reported_as_idempotent(self, monkeypatch, envelope: str) -> None:
        import httpx

        # The endpoint owns de-duplication, so the consumer just counts what it
        # is told: a first delivery is new work, a replay is not.
        state = {"calls": 0}

        def post(url: str, *, json: Any, timeout: float) -> _StubResponse:
            state["calls"] += 1
            first = state["calls"] == 1
            return _StubResponse(
                200, {"idempotent": not first, "event_published": first}
            )

        monkeypatch.setattr(httpx, "post", post)
        consumer = UnderstandingConsumer()

        consumer.handle(envelope)
        consumer.handle(envelope)

        assert consumer.processed == 1
        assert consumer.idempotent == 1

    def test_a_5xx_is_recorded_as_a_failure_not_a_success(self, monkeypatch) -> None:
        import httpx

        monkeypatch.setattr(
            httpx, "post", lambda url, *, json, timeout: _StubResponse(503, {"detail": "down"})
        )
        consumer = UnderstandingConsumer()

        assert consumer.handle(json.dumps({
            "event": "UNDERSTANDING_COMPLETED",
            "correlation_id": "REQ-1",
            "payload": {"request_id": "REQ-1"},
        })) is None
        assert consumer.failed == 1
        assert "503" in (consumer.last_error or "")

    def test_a_400_is_recorded_as_a_rejection(self, monkeypatch) -> None:
        import httpx

        monkeypatch.setattr(
            httpx,
            "post",
            lambda url, *, json, timeout: _StubResponse(422, {"detail": "invalid payload"}),
        )
        consumer = UnderstandingConsumer()

        consumer.handle(json.dumps({
            "event": "UNDERSTANDING_COMPLETED",
            "correlation_id": "REQ-1",
            "payload": {"request_id": "REQ-1"},
        }))
        assert consumer.failed == 1
        assert "rejected" in (consumer.last_error or "")

    def test_an_unreachable_endpoint_is_recorded_not_swallowed(self, monkeypatch) -> None:
        import httpx

        def boom(url: str, *, json: Any, timeout: float):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "post", boom)
        consumer = UnderstandingConsumer()

        assert consumer.handle(json.dumps({
            "event": "UNDERSTANDING_COMPLETED",
            "correlation_id": "REQ-1",
            "payload": {"request_id": "REQ-1"},
        })) is None
        assert consumer.failed == 1
        assert "connection refused" in (consumer.last_error or "")

    def test_status_exposes_what_the_subscriber_is_doing(self) -> None:
        status = UnderstandingConsumer().status()

        assert status["channel"] == "civic.understanding.completed"
        assert status["target_endpoint"].endswith("/api/v1/civic/process")
        assert status["processed"] == 0
