# Niti-Setu

## 💡 Idea

**Niti-Setu** is a multilingual AI-powered Digital Public Good that converts fragmented citizen feedback into **data-driven infrastructure planning intelligence**.

Citizens can submit requests through **voice, text, photos, WhatsApp, Telegram or IVR** in their local language. AI understands the request, identifies the **problem, location, infrastructure category and severity**, and combines this information with **GIS, demographic, infrastructure and government investment data**.

The system then identifies **demand hotspots**, calculates **priority**, and recommends high-impact infrastructure projects to policymakers. 

### Core Flow

```text
Citizen Input
      ↓
AI Understanding
      ↓
Structured Civic Data
      ↓
Government + GIS Data
      ↓
Demand Hotspots
      ↓
Priority Analysis
      ↓
Project Recommendation
      ↓
Policymaker
```

---

# 🏗️ Architecture

```text
┌──────────────────────────────┐
│          CITIZENS            │
│ Voice | Text | Photo | Chat  │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 1. CITIZEN INGESTION         │
│ WhatsApp | Telegram | IVR    │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 2. AI UNDERSTANDING          │
│ ASR | Translation | NLP | CV │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 3. CIVIC DATA PROCESSING     │
│ Normalize | Geocode | Dedup  │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 4. NATIONAL DATA MESH        │◄── Census
│ Citizen + GIS + Infrastructure│◄─ GIS
│ + Investment Data             │◄─ Projects
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 5. CIVIC INTELLIGENCE        │
│ Hotspots | Gaps | Trends     │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 6. PRIORITY & RECOMMENDATION │
│ Priority Score | ROI | Projects│
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ 7. POLICYMAKER DASHBOARD     │
│ GIS Map | Hotspots | Projects │
└──────────────────────────────┘
```

---

# 🧩 Modules

| #     | Module                        | Function                                                                           |
| ----- | ----------------------------- | ---------------------------------------------------------------------------------- |
| **1** | **Citizen Ingestion**         | Collect voice, text, photo and messaging-app requests                              |
| **2** | **AI Understanding**          | Speech-to-text, translation, NLP/NER, severity and image analysis                  |
| **3** | **Civic Data Processing**     | Clean, normalize, geocode and deduplicate requests                                 |
| **4** | **National Data Mesh**        | Combine citizen feedback with GIS, demographic, infrastructure and investment data |
| **5** | **Civic Intelligence**        | Detect demand hotspots, infrastructure gaps and trends                             |
| **6** | **Priority & Recommendation** | Rank infrastructure needs and recommend high-impact projects                       |
| **7** | **Policymaker Dashboard**     | Visualize hotspots, priorities, recommendations and supporting evidence            |

The first four modules correspond closely to the ingestion, semantic NLP and national data-mesh components described in your solution, while modules 5–7 represent the intelligence and policymaker interface. 

---

# ✅ Implementation Status

| # | Module | Status | Tests | Port |
|---|--------|--------|-------|------|
| **1** | [Citizen Ingestion](module-1-citizen-ingestion/) | Implemented, wired into the pipeline | 7 passing | `8001` |
| **2** | [AI Understanding](module-2-ai-understanding/) | Implemented, wired into the pipeline | 113 passing | `8002` |
| **3** | [Civic Data Processing](module-3-civic-data-processing/) | Implemented, wired into the pipeline | 94 passing | `8003` |
| **4** | [National Data Mesh](module-4-national-data-mesh/) | Implemented, wired into the pipeline | 177 passing | `8004` |
| **5** | [Civic Intelligence](module-5-civic-intelligence/) | Implemented | 267 passing | `8005` |
| **6** | [Priority & Recommendation](module-6-priority-recommendation/) | Implemented | 112 passing | `8006` |
| **7** | [Policymaker Dashboard](module-7-policymaker-dashboard/) | Implemented | `npm run build` | `5173` |

**770 tests pass across the six Python modules.** Each suite runs from its own
module directory; there is no root test runner.

### How the modules are actually connected

Modules 1–4 are separate services that talk over **Redis pub/sub**, one channel
per hop, with a per-hop `event_id` and a single `correlation_id` (the Module 1
`request_id`) threaded through the whole chain:

```text
M1 ──citizen-requests──▶ M2 ──civic.understanding.completed──▶ M3 ──civic.records.processed──▶ M4
  request_id=REQ-…        re-fetches M1's API          calls its own            writes citizen_demand
  + event_id              for the stored text          POST /civic/process      + civic_record_lineage
```

| Hop | Channel | Subscriber does |
|-----|---------|-----------------|
| M1 → M2 | `citizen-requests` | Re-reads the request from M1's API (the event is a signal, not the data), understands it, publishes `UNDERSTANDING_COMPLETED` |
| M2 → M3 | `civic.understanding.completed` | Posts to M3's own `POST /api/v1/civic/process`, so the consumer and the API share one implementation |
| M3 → M4 | `civic.records.processed` | Folds the record into the `citizen_demand` aggregate **and** records it in `civic_record_lineage` |

Modules 5–7 keep reading the upstream SQLite database directly. Module 5 snapshots
Module 4 on a schedule (idempotent) so it can compute trends over consecutive
syncs; Module 6 scores Module 5's snapshots; Module 7 serves the decision view.
That is a deliberate choice, not an omission — see
[INTEGRATION_MAP.md](INTEGRATION_MAP.md) for the reasoning and for what is still
missing.

### Provenance and auditability

The demand aggregate that gap scores are computed from discards the individual
submissions behind it, so Module 4 keeps a request-level
`civic_record_lineage` row for every accepted record:

- `request_id` (from Module 1) and `source_event_id` (from Module 3) are stored
  per request, and `request_id` is unique, which makes ingest idempotent — a
  redelivered event refreshes the lineage row and does **not** double-count.
- `GET /api/v1/demand?ward_code=…&sector=…` returns the aggregate a gap was
  computed from; `GET /api/v1/lineage?ward_code=…&sector=…` lists the citizen
  requests behind it. Together they answer "which complaints produced this
  ranking?".
- Records that could not be located to a ward, or that Module 3 rejected on
  quality grounds, are still recorded in lineage (flagged
  `counted_in_demand: false`) so nothing disappears silently, but they never
  inflate a score.
- Seeded demo demand is labelled `source="synthetic"` and ingested demand
  `source="module-4-ingest"`. Synthetic rows never claim to have come from a
  citizen.

### Running the pipeline

Redis is the only external dependency for Modules 1–4 and it is free software:

```bash
brew install redis && redis-server          # or: docker compose up redis
./scripts/start-all.sh                      # starts M1–M4, waits for each to be healthy
python3 scripts/e2e_pipeline.py             # submits a real complaint and follows it to M4
./scripts/stop-all.sh
```

`start-all.sh` starts the services in order, skipping any that are already
listening, and fails loudly with the service's log tail if one never becomes
healthy. Health endpoints:

| Module | Health |
|--------|--------|
| M1–M4 | `GET /api/v1/health` |
| M5, M6 | `GET /health` |

M1's health reports database, Redis and object-storage state and returns `503`
when Redis is down, because the pipeline cannot run without it. M1 also refuses
to start without Redis rather than accepting requests it cannot publish.

The end-to-end test is the real proof the modules are connected: it posts a
citizen complaint to Module 1 over HTTP and then asserts the same `request_id`
reaches Module 4's `civic_record_lineage` with its `event_id` intact, that the
category was not relabelled in transit, and that the `citizen_demand` aggregate
lists the submission. It imports nothing between modules and writes to no
database directly.

### Modules 5–7

```bash
cd module-5-civic-intelligence && python seed_intelligence.py --reset   # hotspots, trends
cd ../module-6-priority-recommendation && python seed_priority.py       # scoring, funding plan
cd ../module-7-policymaker-dashboard && npm install && npm run dev     # decision view
```

Module 5 reads Module 4's database as a snapshot over time, so it keeps the
history that trends need and stays independent of whether Module 4 is currently
running. Syncing is idempotent: re-syncing unchanged data inserts nothing.

```bash
curl -X POST http://localhost:8005/api/v1/operations/sync
curl -X POST http://localhost:8006/api/v1/operations/recompute
```

Module 7 distinguishes the two ways its upstream can be unavailable: if Module 6
is unreachable it says so and names the command to start it, and if Module 6 is
up but has scored nothing it says that instead. A dashboard that reported both as
"error" would be hiding which one is actually wrong.

### Design principles shared across the implemented modules

- **Explainability over scores.** A gap, hotspot or risk stores the components
  and evidence that produced it. A reviewer can disagree with a threshold
  without reverse-engineering a number.
- **Refuse to invent a finding.** A trend with one observation is `UNKNOWN`, not
  a guess; a silent ward is not a hotspot; a spike needs an absolute floor so a
  jump from 1 to 3 is not an emergency.
- **Normalise before comparing.** Intensity is per 1,000 residents and demand is
  normalised per sector, so a dense ward or a noisy sector cannot distort the
  ranking for everyone else.
- **Boundaries between modules.** Module 4 ranks gaps; Module 5 detects
  hotspots, trends and emerging risks; Module 6 owns priority scoring and project
  recommendation. They meet at `ward_code` and `risk_code` rather than
  duplicating each other's reasoning.

Each module's README documents its own methodology, endpoints and known limits. 

---

# 🛠️ Tech Stack

### Frontend

* **React / Angular**
* **MapLibre / Leaflet**
* GIS-based visualization

### Backend

* **Python**
* **FastAPI**
* REST APIs
* Microservices

### AI/ML

* **Bhashini / AI4Bharat / Whisper** — Speech-to-Text
* Translation models
* **Transformer / LLM** — NLP & NER
* **CNN / YOLO-style models** — Infrastructure image analysis
* Clustering algorithms — Hotspot detection

### Database

* **PostgreSQL**
* **PostGIS** — Geospatial data
* **pgvector** — Semantic/vector search
* **Redis** — Cache/queue

### Storage

* S3-compatible object storage for:

  * Images
  * Audio
  * Documents

### Data

* Census / demographic datasets
* GIS datasets
* Infrastructure datasets
* Government investment/project data
* PM GatiShakti and related datasets

### Deployment

* **Docker**
* GitHub Actions
* Cloud infrastructure

The proposed source architecture specifically identifies **PostgreSQL + PostGIS, FastAPI, open-weight models, microservices and REST APIs** for the Digital Public Good implementation. 
