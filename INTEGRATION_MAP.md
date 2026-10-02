# Integration Map — Niti-Setu live pipeline

Derived from reading the repository, not from the README. Ports are the ones the
code/config actually uses.

## Module 1 — Citizen Ingestion (`module-1-citizen-ingestion/backend`)
- **Purpose**: accept a citizen submission (text / voice / photo), persist it, announce it.
- **Database**: `citizen_requests` (SQLite by default, Postgres in compose).
- **Domain ID**: `request_id` = `REQ-{year}-{seq:06d}` → **this is the correlation_id**.
- **API**: `POST /api/v1/requests`, `GET /api/v1/requests/{id}`,
  `POST /api/v1/requests/{id}/media`, `GET /api/v1/health`.
- **Redis**: publishes `REQUEST_RECEIVED` on channel `citizen-requests`.
  `redis==5.0.8` **is already declared** in requirements.txt and in compose.
- **Gaps found**: publish errors are swallowed (`except RedisError: return`);
  event carries no `event_id`/`correlation_id`; health reports no dependency state;
  Docker uses port 8000 while Module 2 defaults to 8001.

## Module 2 — AI Understanding (`module-2-ai-understanding`)
- **Purpose**: language detect, translate, classify category, score severity 1-5, extract entities.
- **Database**: `understanding_records`, `request_id` **UNIQUE** → upstream link already modelled.
- **API**: `POST /api/v1/understand/text|audio|image|batch`, `GET /api/v1/understand`,
  `GET /api/v1/health`, `GET /api/v1/capabilities`.
- **Dependency on Module 1**: `ingestion_base_url` is declared in config but
  **referenced nowhere**. No consumer exists. Module 2 cannot currently receive
  anything from Module 1.
- **Gap**: needs a Redis consumer on `citizen-requests`, and needs to publish its own output event.

## Module 3 — Civic Data Processing (`module-3-civic-data-processing`)
- **Purpose**: validate, clean, geocode, de-duplicate into a standardized civic record.
- **Database**: `civic_records`, `issue_groups`, `event_outbox`, `processing_errors`.
- **API**: `POST /api/v1/civic/process` — already accepts `Module2RecordIn`, the exact
  Module 2 output contract, incl. `source_module`. **Nothing calls it.**
- **Events**: transactional outbox → `CIVIC_RECORD_PROCESSED` on `civic.records.processed`
  carrying `event_id`, `request_id`, `ward`, `district`, `category`, `severity`.
- **Redis**: publisher only. **No subscriber anywhere in the repo.**
- **Gap**: needs a Redis consumer on Module 2's output channel.

## Module 4 — National Data Mesh (`module-4-national-data-mesh`)
- **Purpose**: join citizen demand + GIS + assets + projects + census on `ward_code`, rank gaps.
- **Database**: `wards`, `infrastructure_assets`, `investment_projects`, `citizen_demand`,
  `census_indicators`, `data_products`, `gap_records`.
- **API**: reads + `POST /gaps/analyse`, `POST /mesh/locate`. **No ingest endpoint exists.**
- **Current data**: `seed_mesh.py` inserts `source="synthetic"` rows. No upstream link at all.
- **Gap**: needs an ingest endpoint for Module 3 events, plus per-request provenance —
  `citizen_demand` is an aggregate keyed `(ward_code, sector, category, window_days)`
  and **discards `request_id`**, so nothing is traceable today.

## Verified facts that shape the design
- **Zero Redis subscribers exist in the entire repository.** Both publishers
  (`citizen-requests`, `civic.records.processed`) deliver into the void.
- `geo_service.assign_to_ward(lat, lng)` already exists → this is how Module 3's
  coordinates become Module 4's `ward_code`.
- Module 3's outbox gives the Module 3→4 leg durability and a `retry_pending` endpoint.

## Port map (as found)
| Module | Configured | Compose | Note |
| --- | --- | --- | --- |
| 1 | none (Dockerfile EXPOSE) | `8000:8000` | conflicts with Module 2's default `8001` |
| 2 | none (CLI only) | — | README says 8002 |
| 3 | none (CLI only) | `8003:8000` | own Redis on `6380` |
| 4 | `8004` | — | |
| 5 | `8005` | — | |
| 6 | `8006` | — | |

Standardising on 8001/8002/8003/8004 for the live pipeline, which honours Module 2's
existing `ingestion_base_url` default of `localhost:8001`.

## Provenance design
`request_id` (`REQ-YYYY-NNNNNN`) is minted by Module 1 and is the single correlation_id:
already `UNIQUE` in Module 2, already indexed in Module 3's `civic_records`.
Each hop adds an `event_id`. Module 4 records each received request in a new
`civic_record_lineage` table so an aggregate score can be traced back to the
individual submissions that produced it.