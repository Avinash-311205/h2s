# Module 3 — Civic Data Processing

Part of **Niti-Setu**, a multilingual AI-powered Digital Public Infrastructure &
Governance platform. Module 3 turns the structured output of **Module 2 (AI
Understanding)** into a clean, standardized, validated, location-aware,
duplicate-aware civic record that the **National Data Mesh (Module 4)** can trust.

```text
CLEAN → NORMALIZE → GEOLOCATE → DEDUPLICATE → VALIDATE → SCORE DATA QUALITY → STORE → SEND DOWNSTREAM
```

---

## 1. What Module 3 does

| Capability | Detail |
| --- | --- |
| **Data cleaning** | Whitespace/punctuation/Unicode normalization, null-token handling, timestamp and coordinate parsing, malformed-record and missing-field detection. Raw input is preserved, never deleted. |
| **Data normalization** | Maps free text and Module 2 hints onto a canonical `(category, sub_category)` vocabulary — multilingual (English, Tamil, Hindi, Telugu, Bengali, Kannada, Malayalam) and data-driven. |
| **Geographic processing** | Pluggable geocoding abstraction with a mock gazetteer, OpenStreetMap/Nominatim, and a chained fallback. Produces village/ward/taluk/district/state/country plus a `location_status`. |
| **Duplicate detection** | Rule-based issue grouping on category + sub-category + proximity + time window + description similarity. Duplicates are **linked, never deleted**. |
| **Validation** | Required fields, coordinate ranges, severity range, allowed categories, timestamps and normalization outcomes → `VALID` / `NEEDS_REVIEW` / `INVALID`. |
| **Data quality score** | Explainable weighted score in `0.0 – 1.0` with per-factor breakdown. |
| **Storage & events** | PostgreSQL + PostGIS (SQLite for local dev), plus a transactional outbox that publishes `CIVIC_RECORD_PROCESSED` to Redis for Module 4. |
| **Fault tolerance & idempotency** | Geocoding, deduplication and Redis failures degrade quality instead of failing the request; reprocessing the same `request_id` never creates a second record. |

## 2. Why Module 3 exists

Module 2 tells us *what a citizen meant*. It does not tell us whether the record
is trustworthy, what district it belongs to, or whether four other citizens
already reported the same broken bridge. Without Module 3:

- the same road damage would be counted 50 times, inflating demand;
- `"road"`, `"ROAD"`, `"सड़क"` and `"damaged road"` would fragment national analytics;
- GPS-less and malformed reports would be silently dropped;
- downstream modules would have no signal for how much to trust a record.

Module 3 is the contract boundary: everything after it can assume a normalized,
geolocated, de-duplicated, quality-scored schema.

## 3. Architecture

```text
                    ┌──────────────────────────────┐
   Module 2 ───────▶│ FastAPI  app/api/routes.py   │
   (AI Understanding)└──────────────┬───────────────┘
                                    ▼
                    ┌──────────────────────────────┐
                    │ Processing Orchestrator      │
                    │ services/processing_service  │
                    └──────────────┬───────────────┘
        ┌───────────┬──────────────┼───────────────┬────────────┬─────────────┐
        ▼           ▼              ▼               ▼            ▼             ▼
   Cleaning   Normalization   Geocoding    Deduplication  Validation   Quality scoring
        │           │              │               │            │             │
        └───────────┴──────────────┼───────────────┴────────────┴─────────────┘
                                    ▼
                    ┌──────────────────────────────┐
                    │ Repositories (SQLAlchemy)    │
                    │ civic_repository / issue_... │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ PostgreSQL + PostGIS / SQLite│
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │ Event Publisher (outbox)     │
                    │ Redis  ──▶ Module 4          │
                    └──────────────────────────────┘
```

Every responsibility is an independent service class. `routes.py` only
validates input, delegates, and maps domain errors to HTTP status codes.

## 4. Processing pipeline

| # | Stage | Service | Failure behaviour |
| --- | --- | --- | --- |
| 1 | Clean | `CleaningService` | Never raises; records issues, keeps raw values |
| 2 | Persist raw request | `CivicRepository` | Hard failure → HTTP 500 (data is not yet safe) |
| 3 | Normalize | `NormalizationService` | Falls back to `UNKNOWN` / `UNCLASSIFIED` |
| 4 | Geocode | `GeocodingService` | Failure → `UNRESOLVED`, original location preserved |
| 5 | Deduplicate | `DeduplicationService` | Failure → record stored without an issue group |
| 6 | Validate | `ValidationService` | Produces findings, never rejects |
| 7 | Score | `QualityService` | Weighted, explainable score |
| 8 | Persist record + outbox | `CivicRepository` / `OutboxEventPublisher` | Single transaction |
| 9 | Publish event | `RedisEventTransport` | Redis down → event stays `PENDING` for retry |

The raw citizen request is committed **before** stage 3, so a downstream outage
can never lose citizen data.

## 5. API endpoints

Base path: `/api/v1`

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/civic/process` | Process a Module 2 record. `201` created, `200` on idempotent replay. |
| `GET` | `/civic/{request_id}` | Fetch the standardized civic record. |
| `GET` | `/civic/{request_id}/status` | Processing status, quality, findings and event delivery state. |
| `POST` | `/civic/reprocess/{request_id}` | Re-run the pipeline after processing logic changes. |
| `GET` | `/issues/{issue_group_id}` | Fetch the issue group (aggregate of duplicate reports). |
| `GET` | `/issues/{issue_group_id}/requests` | List every citizen request linked to an issue group. |
| `GET` | `/health` | Liveness/readiness plus dependency status. |
| `POST` | `/events/retry` | Internal: retry undelivered downstream events. |
| `GET` | `/events/pending` | Internal: inspect the outbox. |
| `GET` | `/docs` | OpenAPI documentation. |

### Status codes

| Code | Meaning |
| --- | --- |
| `201` | Record processed (may still be `NEEDS_REVIEW` / `REJECTED` — see payload) |
| `200` | Idempotent replay returned the existing record |
| `400` | Structurally invalid payload (schema violation) |
| `404` | Unknown `request_id` / `issue_group_id` |
| `413` | Payload larger than `MAX_REQUEST_BYTES` |
| `500` | Raw request could not be persisted, or a fatal processing error |

**Important:** an `INVALID` quality status is *not* an HTTP error. The record is
stored, flagged, and available for review.

## 6. Database schema

| Table | Purpose | Key columns |
| --- | --- | --- |
| `citizen_requests` | Immutable raw request from Module 1/2 | `request_id` (PK), `raw_payload`, `payload_hash`, `source_created_at`, `received_at` |
| `processed_civic_records` | The standardized record | `request_id` (FK, unique), `category`, `sub_category`, `language`, `severity`, `location_id`, denormalized `latitude/longitude/village/ward/taluk/district/state/country`, `issue_group_id`, `is_duplicate`, `data_quality_score`, `data_quality_status`, `processing_status`, `raw_data` |
| `issue_groups` | Aggregated civic issue | `issue_group_id` (PK), `category`, `sub_category`, `latitude`, `longitude`, `district/state/country`, `report_count`, `severity_avg/max`, `first/last_reported_at`, `representative_request_id` |
| `locations` | Deduplicated geography | `id` (PK), `location_key` (unique), `latitude`, `longitude`, `geom_wkt`, admin hierarchy, `location_status`, `provider`, `report_count` |
| `processing_errors` | Per-stage error log | `request_id`, `stage`, `error_code`, `severity`, `is_fatal` |
| `event_outbox` | Transactional event outbox | `event_id`, `event_type`, `channel`, `status`, `attempts`, `published_at` |

Indexes exist on `request_id`, `category`, `sub_category`, `district`, `state`,
`country`, `issue_group_id`, `created_at`, `last_reported_at`, `latitude/longitude`
and the quality/processing status columns.

When `POSTGIS_ENABLED=true` on PostgreSQL, `app/database/postgis.py` adds
`geometry(Point,4326)` columns and GiST spatial indexes to `locations` and
`issue_groups`.

### Migrations

```bash
alembic upgrade head      # create the schema (adds PostGIS when enabled)
alembic downgrade base    # drop everything
alembic current
```

## 7. Environment variables

See [`.env.example`](.env.example) for the full annotated list. Highlights:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./civic_processing.db` | SQLAlchemy URL (Postgres + PostGIS in production) |
| `AUTO_CREATE_SCHEMA` | `true` | `create_all` on startup (handy for dev) |
| `POSTGIS_ENABLED` | `false` | Enable PostGIS geometry columns + GiST indexes |
| `EVENT_PUBLISHER` | `redis` | `redis` or `noop` |
| `REDIS_URL` / `EVENT_CHANNEL` | `redis://localhost:6379/0` / `civic.records.processed` | Downstream event transport |
| `GEOCODING_PROVIDER` | `mock` | `mock`, `nominatim` or `chain` |
| `GEOCODING_FALLBACK_PROVIDER` | `mock` | Fallback when the primary provider fails |
| `DEDUP_STRATEGY` | `rule_based` | `rule_based`, or `embedding` once an ML model is wired in |
| `DEDUP_RADIUS_METERS` | `1000` | Proximity threshold for duplicate grouping |
| `DEDUP_TIME_WINDOW_HOURS` | `720` | Reports further apart than this never group |
| `MAX_REQUEST_BYTES` | `262144` | Request-size limit enforced by middleware |
| `LOG_JSON` | `true` | Structured JSON logging with `request_id` correlation |

Secrets are never committed — copy `.env.example` to `.env` locally.

## 8. Installation

```bash
cd module-3-civic-data-processing

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env               # optional; sensible defaults are built in

alembic upgrade head               # or rely on AUTO_CREATE_SCHEMA=true

uvicorn app.main:app --reload --port 8001
```

Then open <http://localhost:8001/docs>.

With the defaults the service runs with **zero external dependencies**: SQLite,
an offline gazetteer and a no-op event transport.

## 9. Docker

```bash
docker compose up --build
```

Starts:

| Service | Address |
| --- | --- |
| API | <http://localhost:8001> |
| PostgreSQL + PostGIS | `localhost:5433` |
| Redis | `localhost:6380` |

The container runs `alembic upgrade head` on boot (best effort) and falls back
to `AUTO_CREATE_SCHEMA`, so the service still starts if migrations cannot run.

## 10. Running tests

```bash
pytest                 # 80 tests
pytest -v
pytest tests/test_deduplication.py
```

Tests use a throwaway SQLite database, the mock geocoder and the no-op event
transport, so they require no network and no running services.

| Suite | Covers |
| --- | --- |
| `test_cleaning.py` | Messy input → clean record; malformed/missing field detection |
| `test_normalization.py` | `"pothole road"` → `ROAD / DAMAGED_ROAD`; 7 languages; dictionary extensibility |
| `test_validation.py` | Valid/invalid coordinates, severity, category, timestamp, location |
| `test_geocoding.py` | Text/coordinate resolution, multi-country gazetteer, provider failure isolation, chained fallback |
| `test_deduplication.py` | Close + similar → same group; far/different → new group; time window; non-actionable records |
| `test_quality.py` | Complete vs incomplete scores, explainable factors, configurable weights |
| `test_idempotency.py` | Same `request_id` twice → one record; reprocess does not inflate counts |
| `test_api.py` | All endpoints, error codes, duplicate grouping, request-size limit |

## 11. Example request

```bash
curl -X POST http://localhost:8001/api/v1/civic/process \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "REQ-10023",
    "language": "ta",
    "category": "road",
    "sub_category": "pothole",
    "description": "இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன",
    "severity": 4,
    "location": { "latitude": 12.9249, "longitude": 80.1000 },
    "created_at": "2026-09-13T10:30:00Z"
  }'
```

## 12. Example response

```json
{
  "idempotent": false,
  "payload_changed": false,
  "event_published": true,
  "event_type": "CIVIC_RECORD_PROCESSED",
  "errors": [],
  "record": {
    "request_id": "REQ-10023",
    "category": "ROAD",
    "sub_category": "DAMAGED_ROAD",
    "description": "இந்த சாலையில் நிறைய பள்ளங்கள் உள்ளன",
    "language": "ta",
    "severity": 4,
    "location": {
      "latitude": 12.9249,
      "longitude": 80.1,
      "village": null,
      "ward": null,
      "taluk": "Chengalpattu",
      "district": "Chengalpattu",
      "state": "Tamil Nadu",
      "country": "India",
      "pincode": null,
      "location_status": "RESOLVED",
      "provider": "mock"
    },
    "location_status": "RESOLVED",
    "issue_group_id": "ISSUE-00001",
    "is_duplicate": false,
    "report_count": 1,
    "data_quality_score": 0.992,
    "data_quality_status": "VALID",
    "processing_status": "PROCESSED",
    "normalization_confidence": 0.95,
    "normalization_source": "explicit",
    "matched_keywords": ["pothole"],
    "processing_version": "1.0.0",
    "reprocess_count": 0,
    "created_at": "2026-09-13T10:30:00Z",
    "processed_at": "2026-09-13T10:31:02.481000Z",
    "processing_issues": [],
    "validation_findings": [],
    "raw_data": { "...": "the untouched Module 2 payload" }
  }
}
```

## 13. How Module 2 connects to Module 3

Module 2 (AI Understanding) POSTs its structured output to
`POST /api/v1/civic/process`. Module 3 expects:

```json
{
  "request_id": "REQ-10023",
  "language": "ta",
  "category": "road",
  "sub_category": "pothole",
  "description": "Road has many potholes",
  "severity": 4,
  "location": { "latitude": 12.9249, "longitude": 80.1000 },
  "created_at": "2026-09-13T10:30:00Z"
}
```

All fields are optional at the HTTP layer — Module 3's job is to *detect* and
*flag* incomplete data rather than reject it. Extra fields are allowed and
preserved in `raw_data`. Module 3 keeps the original `request_id` from Module 1
so the whole pipeline stays traceable.

Module 3 does **not** implement any Module 2 responsibility: no speech-to-text,
translation, OCR, LLM understanding or sentiment analysis. It only standardizes
information that was already understood.

## 14. How Module 3 connects to Module 4

After a record is committed, Module 3 enqueues an event in `event_outbox` and
publishes it to Redis (`EVENT_CHANNEL`, default `civic.records.processed`):

```json
{
  "event": "CIVIC_RECORD_PROCESSED",
  "schema_version": "1.0",
  "request_id": "REQ-10023",
  "issue_group_id": "ISSUE-00001",
  "category": "ROAD",
  "sub_category": "DAMAGED_ROAD",
  "severity": 4,
  "latitude": 12.9249,
  "longitude": 80.1,
  "district": "Chengalpattu",
  "state": "Tamil Nadu",
  "country": "India",
  "is_duplicate": false,
  "report_count": 1,
  "data_quality_score": 0.992,
  "data_quality_status": "VALID",
  "processing_status": "PROCESSED",
  "processed_at": "2026-09-13T10:31:02+00:00"
}
```

Event types:

| Event | When |
| --- | --- |
| `CIVIC_RECORD_PROCESSED` | `data_quality_status = VALID` |
| `CIVIC_RECORD_NEEDS_REVIEW` | `data_quality_status = NEEDS_REVIEW` |
| `CIVIC_RECORD_REJECTED` | `data_quality_status = INVALID` |

**Delivery guarantee.** The event is written to `event_outbox` in the same
transaction as the record, then delivered. If Redis is unavailable, the record is
still persisted and the event stays `PENDING`/`FAILED`; undelivered events are
retried on startup and via `POST /api/v1/events/retry`. Swapping in Kafka or
RabbitMQ means implementing one `EventTransport`.

## 15. Design decisions

| Decision | Reason |
| --- | --- |
| Raw request committed before processing | Never lose citizen data, even if every stage fails |
| Issues are *flagged*, not rejected | A malformed report is still evidence of citizen demand |
| Duplicates linked, never deleted | Every citizen report stays auditable; `issue_groups` aggregates |
| `category` from Module 2 wins over description inference | A coarse hint remains stable; mismatches are reported, not silently overridden |
| Dictionary is JSON, not code | Adding a language or keyword needs no code change |
| Geocoding behind an interface | Government GIS / Nominatim / PostGIS can be swapped per deployment |
| Rule-based dedup first | Deterministic, explainable and testable; embedding strategy is the documented next step |

### Known limitations

- **Cross-language duplicates** (`"Bridge damaged"` vs `"சேதமடைந்த பாலம்"`) do not
  group under the rule-based strategy because comparison is lexical. Set
  `DEDUP_STRATEGY=embedding` once a multilingual embedding model is wired into
  `EmbeddingDeduplicationStrategy`; the interface and fallback already exist.
- A payload with **no `request_id`** gets a generated placeholder, so it cannot be
  matched on replay. Idempotency requires a stable upstream identifier.
- Reprocessing appends to `processing_errors` after deleting that request's
  previous rows, so the table reflects the latest run.

## 16. Future scalability improvements

1. **Traceability** — publish events through Kafka/Redpanda with the outbox as
   the producer; keep Redis as the low-latency local transport.
2. **Batch processing** — add a queue consumer that processes records
   concurrently, with the same idempotency guarantees.
3. **Semantic dedup at scale** — `pgvector` sentence embeddings behind the
   existing `DeduplicationStrategy` interface.
4. **Real geocoding chain** — government GIS primary, Nominatim fallback,
   PostGIS-backed gazetteer cached locally; add bulk pre-resolution for
   administrative boundaries.
5. **PostGIS-native queries** — move proximity/bbox filters to `ST_DWithin` once
   `POSTGIS_ENABLED=true` is the default in production.
6. **Quality model** — replace the weighted heuristic in `QualityService` with a
   calibrated model while keeping the factor breakdown for explainability.
7. **Observability** — OpenTelemetry traces per pipeline stage, plus outbox lag
   and per-stage success-rate metrics.
8. **Schema evolution** — version the event schema and standard record
   (`processing_version`) to allow rolling reprocesses.
9. **Scale-out writes** — partition `processed_civic_records` by `state`, and use
   read replicas for the analytics-facing endpoints.

---

## Project structure

```text
module-3-civic-data-processing/
├── app/
│   ├── main.py                     # FastAPI app, middleware, error handlers
│   ├── api/
│   │   ├── routes.py               # REST endpoints (thin controllers)
│   │   └── schemas.py              # Pydantic request/response models
│   ├── core/
│   │   ├── config.py               # settings
│   │   ├── enums.py                # categories, statuses, language aliases
│   │   ├── geo.py                  # haversine, bbox, WKT
│   │   ├── logging.py              # structured logging with request context
│   │   └── utils.py                # text/text-normalization/hash helpers
│   ├── database/
│   │   ├── base.py                 # declarative base
│   │   ├── connection.py           # engine, session, dependency
│   │   ├── postgis.py              # optional PostGIS bootstrap
│   │   └── migrations/             # Alembic environment + initial migration
│   ├── models/                     # citizen_request, civic_record, issue_group, location,
│   │                               # processing_error, event_outbox
│   ├── repositories/               # civic_repository, issue_repository, event_repository
│   ├── resources/
│   │   └── normalization_dictionary.json   # multilingual vocabulary
│   ├── services/
│   │   ├── cleaning_service.py
│   │   ├── normalization_service.py
│   │   ├── geocoding_service.py
│   │   ├── deduplication_service.py
│   │   ├── validation_service.py
│   │   ├── quality_service.py
│   │   ├── processing_service.py   # orchestrator
│   │   └── health_service.py
│   └── events/publisher.py         # transports + transactional outbox
├── tests/                          # 80 tests
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── pytest.ini
├── requirements.txt
└── .env.example
```
