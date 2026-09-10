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
