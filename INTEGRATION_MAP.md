# Integration Map — Niti-Setu live pipeline

Derived from reading the repository, not from the README. This file records what
was found, what was built to close each gap, and what is still missing. Port
numbers are the ones the code actually uses.

**Status: Modules 1–4 are connected over Redis and verified end to end by
`scripts/e2e_pipeline.py`.** Modules 5–7 still read the upstream SQLite
database directly (see "Still not wired").

## Module 1 — Citizen Ingestion (`module-1-citizen-ingestion/backend`)
- **Purpose**: accept a citizen submission (text / voice / photo), persist it, announce it.
- **Database**: `citizen_requests` (SQLite by default, Postgres in compose).
- **Domain ID**: `request_id` = `REQ-{year}-{seq:06d}` → **this is the correlation_id**.
- **API**: `POST /api/v1/requests`, `GET /api/v1/requests/{id}`,
  `POST /api/v1/requests/{id}/media`, `GET /api/v1/health`.
- **Redis**: publishes `REQUEST_RECEIVED` on channel `citizen-requests`.

### Gaps found → closed
| Gap found | Resolution |
| --- | --- |
| Publish errors swallowed (`except RedisError: return`) | `redis_client.publish_request_received` raises `RedisUnavailable`; the request is still stored but marked `PUBLISH_FAILED` with the error, so a silent success is impossible |
| Event carried no `event_id` / `correlation_id` | Full envelope: `event_id`, `correlation_id == request_id`, `request_id`, `source_module`, `schema_version`, `timestamp`, `status`, `payload` |
| Health reported no dependency state | `/api/v1/health` reports database, Redis and object storage; `503` when Redis is down |
| Startup ignored a dead Redis | Startup gate refuses to serve without Redis |
| Media upload returned a fabricated URL when MinIO was down | Raises `StorageUnavailable` → `400` with the reason; no URL is invented |
| DB path was CWD-dependent (two databases possible) | Anchored to the module directory |
| Docker used port 8000, conflicting with Module 2's default 8001 | Dockerfile and compose now use `8001`; compose also installs `requirements-postgres.txt` |
| `psycopg2` was a hard dependency for the SQLite path | Split into `requirements-postgres.txt`; the Compose image installs both |

### Still open
- A request left in `PUBLISH_FAILED` is visible but nothing retries it. There is
  no Module 1 outbox; retries today are manual.

## Module 2 — AI Understanding (`module-2-ai-understanding`)
- **Purpose**: language detect, translate, classify category, score severity 1-5, extract entities.
- **Database**: `understanding_records` (`request_id` UNIQUE), `understanding_events` (outbox).
- **API**: `POST /api/v1/understand/text|audio|image|batch`, `GET /api/v1/understand`,
  `GET /api/v1/understand/{request_id}`, `GET /api/v1/health`, `GET /api/v1/capabilities`.

### Gaps found → closed
| Gap found | Resolution |
| --- | --- |
| `ingestion_base_url` referenced nowhere; no consumer existed | `app/events/consumer.py` subscribes to `citizen-requests` |
| Needed a Redis consumer | `UnderstandingConsumer` re-fetches the request from Module 1's API — the event is a signal, not the data — then calls the existing `understanding_service` |
| Needed to publish its own output event | Publishes `UNDERSTANDING_COMPLETED` on `civic.understanding.completed` with a full envelope |
| No durability for the output event | `understanding_events` outbox with `dispatch()` and `retry_pending()` on startup |
| A redelivered announcement would duplicate work | Skipped when a record or a published event already exists for the `request_id` |

## Module 3 — Civic Data Processing (`module-3-civic-data-processing`)
- **Purpose**: validate, clean, geocode, de-duplicate into a standardized civic record.
- **Database**: `civic_records`, `issue_groups`, `event_outbox`, `processing_errors`.
- **API**: `POST /api/v1/civic/process` accepts `Module2RecordIn`, the exact Module 2
  output contract, plus `GET /api/v1/civic/{request_id}` and `/civic/{id}/status`.
- **Events**: transactional outbox → `CIVIC_RECORD_PROCESSED` on `civic.records.processed`.

### Gaps found → closed
| Gap found | Resolution |
| --- | --- |
| `POST /civic/process` existed but nothing called it | `UnderstandingConsumer` calls this module's **own** endpoint over HTTP, so the consumer and the API cannot drift apart |
| No subscriber anywhere in the repo | `app/events/consumer.py` subscribes to `civic.understanding.completed` |
| Publisher emitted `record.payload` only — the event_id never left the service | `dispatch()` now publishes the full envelope; Module 4's lineage depends on this |
| Health could not tell you ingestion was dead | `/api/v1/health` reports the `upstream_consumer` state |
| Port/consumer settings absent | `port`, `self_base_url`, `understanding_channel`, `upstream_process_timeout_seconds`, `pipeline_consumer_enabled` in config |

## Module 4 — National Data Mesh (`module-4-national-data-mesh`)
- **Purpose**: join citizen demand + GIS + assets + projects + census on `ward_code`, rank gaps.
- **Database**: `wards`, `infrastructure_assets`, `investment_projects`, `citizen_demand`,
  `census_indicators`, `data_products`, `gap_records`, **`civic_record_lineage`** (new).
- **API**: reads, `POST /api/v1/gaps/analyse`, `POST /api/v1/mesh/locate`,
  **`POST /api/v1/ingest/civic-records`** (new), **`GET /api/v1/lineage/{request_id}`** (new),
  **`GET /api/v1/lineage`** (new), **`GET /api/v1/demand`** (new).

### Gaps found → closed
| Gap found | Resolution |
| --- | --- |
| No ingest endpoint | `POST /api/v1/ingest/civic-records` runs the same service the Redis consumer uses |
| No subscriber | `app/events/consumer.py` subscribes to `civic.records.processed` |
| `citizen_demand` discarded `request_id`, so no score was traceable | `civic_record_lineage` keeps one row per `request_id` with `source_event_id`, ward, category, severity and payload |
| Redelivery would double-count the aggregate | `request_id` is unique in lineage; a replay refreshes the row and skips the aggregate |
| Unlocatable or quality-rejected records would vanish | Still recorded in lineage with `counted_in_demand: false` |
| Synthetic seed data was labelled `source="module-2"` | Relabelled `source="synthetic"`; ingested rows are `source="module-4-ingest"` |
| Health was not on the platform path | `/api/v1/health` added (the old `/health` remains as an alias) |

## Verified facts that shaped the design
- `geo_service.assign_to_ward(lat, lng, wards)` already existed → this is how a
  citizen's coordinates become a `ward_code`. Module 3's resolved ward label is
  preferred when present, with a coordinate fallback.
- Module 2's `Category` taxonomy and Module 4's `Sector` taxonomy have identical
  values, so category → sector is an identity map, with `UNKNOWN` for anything
  unrecognised rather than a guess.
- Module 3's outbox already gave the M3→M4 leg durability and a retry endpoint.

## Port map (current)
| Module | Configured | Compose | Note |
| --- | --- | --- | --- |
| 1 | `8001` | `8001:8001` | was 8000 |
| 2 | `8002` | — | |
| 3 | `8003` | `8003:8000` | container port stays 8000 internally |
| 4 | `8004` | — | |
| 5 | `8005` | — | health at `/health` |
| 6 | `8006` | — | health at `/health` |
| Redis | `6379` | `6379:6379` | shared by M1–M4 so the pipeline behaves the same with or without Compose |

## Provenance design
`request_id` (`REQ-YYYY-NNNNNN`) is minted by Module 1 and is the single
correlation_id: UNIQUE in Module 2's `understanding_records`, indexed in Module 3's
`civic_records`, and UNIQUE in Module 4's `civic_record_lineage`. Each hop adds an
`event_id`; Module 4 stores the Module 3 `event_id` as `source_event_id`, so an
aggregate gap score can be traced back to the individual submissions that produced
it.

Verified live: `REQ-2026-000006` submitted to Module 1 → understood as
`WATER`/severity 4 → `ISSUE-00001` in Module 3 → lineage row in Module 4 with
`ward_code=CHN-02` and its Module 3 `event_id` intact, counted in the
`citizen_demand` aggregate that Module 5 then synced.

## Still not wired
- **Modules 5–7 read upstream SQLite files, not the APIs.** Module 5 snapshots
  Module 4's database on a schedule and Module 6 scores Module 5's snapshots.
  This works and is idempotent, but it is file coupling: it breaks under
  Postgres, across hosts, and it cannot be rate-limited or replayed through a
  broker. Moving 5–7 to HTTP (or to consuming the same channels) is the next
  structural step.
- **Redis pub/sub has no delivery guarantee.** If Module 4 is down when Module 3
  publishes, the event is gone: pub/sub has no redelivery and Module 3's outbox
  only knows "published", not "consumed". A durable inbox (or a broker with
  acknowledgements) is required before this can be called lossless. Today the
  reconciliation path is `GET /api/v1/lineage/{request_id}` plus Module 3's
  `event_outbox` and `POST /api/v1/events/retry`.
- **Module 1 has no retry for `PUBLISH_FAILED` requests.**
- **No auth on the ingest endpoint.** Any client that can reach Module 4 can post
  a civic record. Acceptable on localhost, not on a shared network.

## Verifying this yourself
```bash
./scripts/start-all.sh        # M1-M4 on 8001-8004 against Redis 6379
python3 scripts/e2e_pipeline.py
```
The end-to-end test imports nothing between modules and writes to no database
directly: it posts to Module 1's HTTP API and asserts the request reaches
Module 4's lineage and demand aggregate with its identity intact.
