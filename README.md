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

| # | Module | Status | Tests | Run locally |
|---|--------|--------|-------|-------------|
| **2** | [AI Understanding](module-2-ai-understanding/) | Implemented | 96 passing | `python seed_understanding.py` → port `8002` |
| **4** | [National Data Mesh](module-4-national-data-mesh/) | Implemented | 139 passing | `python seed_mesh.py --reset` → port `8004` |
| **5** | [Civic Intelligence](module-5-civic-intelligence/) | Implemented | 262 passing | `python seed_intelligence.py --reset` → port `8005` |
| **1**, **3**, **6**, **7** | — | Not yet built | — | — |

Each module is a self-contained FastAPI + SQLAlchemy service with its own
database, seed script and test suite. Modules 2, 4 and 5 run with no external
services and no API keys: heavy AI dependencies fall back to deterministic local
implementations, and SQLite stands in for PostgreSQL locally. Every module
creates its schema on startup, so a fresh clone runs with no migration step.

### Running the implemented chain

```bash
# 2. AI Understanding - turn raw citizen input into structured records
cd module-2-ai-understanding && python seed_understanding.py

# 4. National Data Mesh - join demand, GIS, assets, projects, census on ward_code
cd ../module-4-national-data-mesh && python seed_mesh.py --reset

# 5. Civic Intelligence - read the mesh, detect hotspots, trends and emerging risks
cd ../module-5-civic-intelligence && python seed_intelligence.py --reset

python -m uvicorn app.main:app --port 8005     # then open /docs
```

Module 5 reads Module 4's database as a **snapshot over time** rather than
calling its API, so it keeps the history that trends need and stays independent
of whether Module 4 is currently running. Syncing is idempotent: re-syncing
unchanged data inserts nothing.

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
