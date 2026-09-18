# Module 6: Priority & Recommendation

FastAPI service that turns raw citizen requests (SQLite) into **prioritised,
explainable infrastructure hotspots** for policymakers.

- **DBSCAN** clustering (haversine distance in metres) groups requests into
  geographic hotspots, computed **per category**.
- Transparent **Priority Score (0-100)** is a weighted sum of four factors,
  each with a documented normalisation and weight in `scoring.py` -- fully
  auditable, not a black box.
- Plain-language recommendations are generated from deterministic templates.

## What it does

```
citizen_requests.db (requests table)
        |
        v
  [database.py]  read-only sqlite3 access
        |
        v
  [clustering.py]  DBSCAN per category, haversine metres, region geofence
        |
        v
  [scoring.py]     weighted sum: volume(0.35) + severity(0.30)
                   + population density(0.15) + days open(0.20)  -> 0-100
        |
        v
  FastAPI  ->  GET /hotspots        (score + factor breakdown + evidence)
               GET /recommendations (top N, ranked, plain-language copy)
```

## Quick Start

```bash
# 1) (Optional but recommended) create a virtual environment
python -m venv .venv && source .venv/bin/activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) Start the API (default port 8006)
python main.py
# or: uvicorn api.main:app --host 0.0.0.0 --port 8006 --reload
```

The API expects `citizen_requests.db` one level above this module (i.e.
`../citizen_requests.db`), as created by module-1-citizen-ingestion. Override
with the `CITIZEN_DB_PATH` environment variable if yours lives elsewhere:

```bash
CITIZEN_DB_PATH=/path/to/citizen_requests.db python main.py
```

Open interactive docs at http://localhost:8006/docs

## API Endpoints

| Method | Endpoint           | Description                                           |
| ------ | ------------------ | ----------------------------------------------------- |
| GET    | `/health`          | Liveness probe + DB availability                      |
| GET    | `/hotspots`        | All hotspots: centroid, category, count, score, factor breakdown, sample complaint texts |
| GET    | `/recommendations` | Top N hotspots ranked by `priority_score`, each with a plain-language recommendation string |

## Priority Score (0-100) -- how it works

Each hotspot's score is the weighted sum of **four normalised factors**:

| Factor             | Weight | Raw input                    | Why it matters                               |
| ------------------ | ------ | ---------------------------- | -------------------------------------------- |
| Volume             | 0.35   | `request_count`              | More citizens complaining = more affected    |
| Severity           | 0.30   | `avg_severity` (1-5)         | Critical issues need faster response         |
| Population density | 0.15   | mock region -> people/sq.km  | Same issue hits more people in dense wards   |
| Days open          | 0.20   | `avg_days_open`              | Ignored complaints signal service failure    |

Each raw factor is normalised to 0-100 against a documented reference maximum,
then multiplied by its weight. Because the weights sum to 1.0, the result is
already a 0-100 score. Every factor's `raw`, `normalised`, `weight`, and
`contribution` are returned in the API so the total is fully derivable by hand.

> **Note on population density**: currently a static mock lookup (`scoring.py`
> `POPULATION_DENSITY`) keyed by the region geofence in `clustering.py`.
> Swap in a real census/LGD dataset to productionise.

## Folder Structure

```
module-6-priority-recommendation/
├── main.py            # uvicorn entrypoint
├── scoring.py         # transparent weighted priority logic (judge-facing)
├── clustering.py      # DBSCAN + haversine + region geofence
├── database.py        # read-only sqlite3 access
├── api/
│   ├── __init__.py
│   ├── main.py        # FastAPI routes
│   └── schemas.py     # Pydantic response models
├── requirements.txt
└── README.md
```

## License

Academic project.