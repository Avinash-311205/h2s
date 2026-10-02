"""Tests for the Module 1 -> Module 2 wiring.

Two properties are load-bearing and are what these tests pin:

1. Module 1's announcement is a *signal*, not the data. The citizen's actual text
   is re-fetched from Module 1's API, so the announcement cannot drift from what
   was stored.
2. A redelivered announcement must not produce a second understanding or a
   second downstream event.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from app.core.config import settings
from app.events.consumer import UnderstandingConsumer
from app.events.publisher import OutboxEventPublisher, build_completion_event
from app.ingestion.client import IngestionClient, IngestionUnavailable
from app.repositories.understanding_event_repository import UnderstandingEventRepository
from app.repositories.understanding_repository import UnderstandingRepository


class StubClient:
    """Stands in for the Module 1 HTTP API."""

    def __init__(self, request: Optional[dict[str, Any]] = None, error: Optional[Exception] = None):
        self.request = request or {
            "request_id": "REQ-2026-000042",
            "text": "There has been no water supply in our street for four days",
            "channel": "web",
        }
        self.error = error
        self.calls: list[str] = []

    def fetch_request(self, request_id: str) -> dict[str, Any]:
        self.calls.append(request_id)
        if self.error is not None:
            raise self.error
        return self.request

    def healthy(self) -> bool:
        return self.error is None


class RecordingTransport:
    def __init__(self, fail_times: int = 0):
        self.messages: list[tuple[str, str]] = []
        self.fail_times = fail_times

    def publish(self, channel: str, message: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("redis unavailable")
        self.messages.append((channel, message))


def announcement(request_id: str = "REQ-2026-000042", event_id: str = "EVT-M1-0001") -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "event": "REQUEST_RECEIVED",
            "event_type": "REQUEST_RECEIVED",
            "channel": "citizen-requests",
            "correlation_id": request_id,
            "request_id": request_id,
            "schema_version": "1.0",
            "source_module": "module-1-citizen-ingestion",
            "timestamp": "2026-01-15T10:00:00+00:00",
            "payload": {"request_id": request_id},
        }
    )


@pytest.fixture
def consumer() -> UnderstandingConsumer:
    return UnderstandingConsumer(client=StubClient())


# --- announcement handling ---------------------------------------------------
class TestConsumer:
    def test_announcement_triggers_understanding_and_a_completion_event(self, consumer) -> None:
        request_id = consumer.handle(announcement())

        assert request_id == "REQ-2026-000042"
        assert consumer.processed == 1
        assert consumer.failed == 0

    def test_module1_is_queried_by_request_id(self, consumer) -> None:
        # The event only says "go look"; the stored record is the source of truth.
        consumer.handle(announcement("REQ-2026-000099"))

        assert consumer.client.calls == ["REQ-2026-000099"]

    def test_a_redelivered_announcement_is_a_no_op(self, consumer) -> None:
        consumer.handle(announcement())
        consumer.handle(announcement())

        assert consumer.skipped == 1
        assert consumer.processed == 1
        # Module 1 was not even asked a second time.
        assert consumer.client.calls == ["REQ-2026-000042"]

    def test_an_unreachable_module1_is_recorded_as_a_failure(self) -> None:
        consumer = UnderstandingConsumer(
            client=StubClient(error=IngestionUnavailable("module 1 is down"))
        )

        assert consumer.handle(announcement()) is None
        assert consumer.failed == 1
        assert "down" in (consumer.last_error or "")

    def test_a_request_with_no_understandable_content_is_skipped_visibly(self) -> None:
        consumer = UnderstandingConsumer(
            client=StubClient(request={"request_id": "REQ-1", "text": None, "channel": "web"})
        )

        assert consumer.handle(announcement("REQ-1")) is None
        assert consumer.skipped == 1
        assert consumer.failed == 0

    @pytest.mark.parametrize(
        "raw",
        ["{not json", "[]", json.dumps({"event": "REQUEST_RECEIVED"})],
        ids=["bad_json", "not_an_object", "no_request_id"],
    )
    def test_unusable_announcements_are_dropped(self, consumer, raw: str) -> None:
        assert consumer.handle(raw) is None
        assert consumer.client.calls == []

    def test_other_event_types_are_ignored(self, consumer) -> None:
        raw = json.dumps({"event": "SOMETHING_ELSE", "correlation_id": "REQ-1"})
        assert consumer.handle(raw) is None
        assert consumer.client.calls == []

    def test_status_reports_the_channel_it_is_listening_on(self, consumer) -> None:
        status = consumer.status()
        assert status["channel"] == settings.ingestion_channel
        assert status["processed"] == 0


# --- completion event --------------------------------------------------------
def _stored_record(db_session, request_id: str = "REQ-2026-000042"):
    """Run the real pipeline and persist it, as the consumer does."""
    from app.services.understanding_service import understand

    result = understand(
        request_id=request_id,
        text="There has been no water supply in our street for four days",
        audio_bytes=None,
        image_bytes=None,
        hint_language=None,
    )
    return UnderstandingRepository(db_session).save(result, source_channel="WEB", processing_ms=42)


class TestCompletionEvent:
    def test_payload_carries_the_request_id_as_correlation_id(self, db_session) -> None:
        record = _stored_record(db_session)

        payload = build_completion_event(
            "REQ-2026-000042",
            record=record,
            source_event_id="EVT-M1-0001",
            source_channel="citizen-requests",
        )

        assert payload["request_id"] == "REQ-2026-000042"
        assert payload["correlation_id"] == "REQ-2026-000042"
        assert payload["event"] == settings.understanding_event_type

    def test_payload_matches_what_module3_expects(self, db_session) -> None:
        record = _stored_record(db_session)
        payload = build_completion_event(
            "REQ-2026-000042", record=record, source_event_id=None, source_channel=None
        )

        # Module 3's Module2RecordIn must accept this without relabelling, so
        # the keys it requires are pinned here rather than at the far end of a
        # pipeline run.
        assert {"request_id", "language", "category", "sub_category", "text",
                "severity", "latitude", "longitude"} <= set(payload)
        assert isinstance(payload["severity"], int)
        assert 1 <= payload["severity"] <= 5

    def test_payload_keeps_the_citizens_own_words(self, db_session) -> None:
        record = _stored_record(db_session)

        payload = build_completion_event(
            "REQ-2026-000042", record=record, source_event_id=None, source_channel=None
        )

        assert "water" in payload["text"].lower()


# --- outbox durability -------------------------------------------------------
class TestOutbox:
    def _enqueued(self, db_session, transport, request_id: str = "REQ-2026-000042") -> str:
        record = _stored_record(db_session, request_id)
        payload = build_completion_event(
            request_id, record=record, source_event_id="EVT-M1-0001", source_channel="citizen-requests"
        )
        publisher = OutboxEventPublisher(
            UnderstandingEventRepository(db_session), transport=transport
        )
        event_id = publisher.enqueue(
            event_id="EVT-M2-0001",
            request_id=request_id,
            payload=payload,
            source_event_id="EVT-M1-0001",
            source_channel="citizen-requests",
        )
        db_session.commit()
        return publisher, event_id

    def test_the_published_envelope_carries_event_identity(self, db_session) -> None:
        transport = RecordingTransport()
        publisher, event_id = self._enqueued(db_session, transport)

        assert publisher.dispatch(event_id) is True

        channel, raw = transport.messages[0]
        envelope = json.loads(raw)
        assert channel == "civic.understanding.completed"
        assert envelope["event_id"] == "EVT-M2-0001"
        assert envelope["request_id"] == "REQ-2026-000042"
        assert envelope["correlation_id"] == "REQ-2026-000042"
        assert envelope["source_event_id"] == "EVT-M1-0001"
        assert envelope["payload"]["request_id"] == "REQ-2026-000042"

    def test_a_redis_outage_leaves_the_event_in_the_outbox(self, db_session) -> None:
        transport = RecordingTransport(fail_times=99)
        publisher, event_id = self._enqueued(db_session, transport)

        assert publisher.dispatch(event_id) is False
        stored = UnderstandingEventRepository(db_session).get_by_request_id("REQ-2026-000042")
        assert stored is not None
        assert stored.status != "PUBLISHED"

    def test_pending_events_are_retried_later(self, db_session) -> None:
        transport = RecordingTransport(fail_times=1)
        publisher, event_id = self._enqueued(db_session, transport)

        assert publisher.dispatch(event_id) is False
        assert transport.messages == []

        publisher.retry_pending()
        # The event survived the outage and eventually reached the channel.
        assert transport.messages[0][0] == "civic.understanding.completed"
        assert json.loads(transport.messages[0][1])["event_id"] == "EVT-M2-0001"

    def test_a_published_event_is_not_published_twice(self, db_session) -> None:
        transport = RecordingTransport()
        publisher, event_id = self._enqueued(db_session, transport)

        assert publisher.dispatch(event_id) is True
        assert publisher.dispatch(event_id) is True
        assert len(transport.messages) == 1
