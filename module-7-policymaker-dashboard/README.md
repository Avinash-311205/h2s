# Module 7: Policymaker Dashboard

React + Vite visualisation layer for Niti-Setu. It consumes Module 6's Priority
& Recommendation API and shows policymakers **where** to act (map), **what** to
fund (ranked plan), and **why** (the full score breakdown).

- **Leaflet + OpenStreetMap** free tiles (no API key); hotspots are circle
  markers sized by score and coloured by the band Module 6 assigned.
- **Funding plan** in rank order with a running cumulative envelope.
- **District overview** with the band mix and the mean score per district.
- Zero paid services, plain CSS (no UI kit).

## Prereqs

- Node.js 18+ (tested on 22)
- Module 6 running on `:8006` **with something already scored**. See below.

## Quick Start

```bash
# 1) Install dependencies
npm install

# 2) Point the dashboard at your Module 6 API (defaults to :8006)
cp .env.example .env
#    edit VITE_API_BASE_URL if Module 6 runs elsewhere

# 3) Start the dev server
npm run dev
#    open http://localhost:5173
```

For a production build:

```bash
npm run build    # outputs static files to dist/
npm run preview  # serve the built bundle locally
```

### Preparing the data

The dashboard has nothing to show until Module 6 has produced a ranking. From
the repository root:

```bash
# 1) Seed Module 5's intelligence database (writes civic_intelligence.db)
python module-5-civic-intelligence/seed_intelligence.py

# 2) Start Module 6
cd module-6-priority-recommendation && uvicorn app.main:app --port 8006

# 3) Score. Startup only initialises the database, so /health answers straight
#    away but the ranking stays empty until a run completes.
curl -X POST http://localhost:8006/api/v1/operations/recompute
```

Step 3 is separate from startup on purpose: `/health` has to be answerable
whether or not anything has been scored, so Module 6 does not score implicitly
on boot. Re-run the `recompute` call whenever the ranking should be refreshed.

## What you'll see

| Area | What it shows |
| ---- | ------------- |
| Stat tiles | Hotspots ranked, districts, people and complaints covered, the indicative envelope, the mean score, and a count of hotspots with unmeasured factors. |
| Map | Every hotspot as a circle sized by score and coloured by band. Click a marker to open its panel. The map auto-fits the data. |
| Side panel | The hotspot's score, band, rank, and **every factor** with its raw value, normalised component and weight. Factors Module 6 could not measure are called out explicitly. |
| Funding plan | The top hotspots in rank order with the recommendation and both the per-item and cumulative envelope. Filter by band. |
| District overview | How many hotspots fall in each band, and the mean score per district. |

## Two failure modes, two different messages

The dashboard distinguishes these deliberately, because the fix is different:

| Situation | What you see |
| --------- | ------------ |
| Module 6 is not running or unreachable | "Module 6 is not responding", with the command to start it |
| Module 6 is running but nothing has been scored | "Nothing has been scored yet", with the command to seed Module 5 |

## Where the band comes from

Priority **bands are Module 6's decision and are used verbatim.** The cut-offs
are deliberately *not* duplicated in `utils/priority.js`; a second copy would
drift and eventually colour a hotspot the wrong way. The only thing the frontend
maps is band name → colour.

Indicative costs are planning envelopes derived from severity, coverage gap and
delivery failure, not cost estimates.

## Configuration

| Variable | Purpose | Default |
| -------- | ------- | ------- |
| `VITE_API_BASE_URL` | Base URL of Module 6's FastAPI service | `http://localhost:8006` |

Vite only exposes env vars prefixed with `VITE_`. See `.env.example`.

## Endpoints used

| Endpoint | Used for |
| -------- | -------- |
| `GET /health` | Service status and whether anything is scored |
| `GET /api/v1/priorities` | The full ranking |
| `GET /api/v1/priorities/summary` | Portfolio totals for the tiles |
| `GET /api/v1/plan` | Funding plan with cumulative envelope |

`GET /api/v1/priorities/{code}/history` and `POST /api/v1/operations/recompute`
are exposed by Module 6 and wrapped in `api.js`, but not yet surfaced in the UI.

## Folder Structure

```
module-7-policymaker-dashboard/
├── index.html                # SPA shell
├── package.json
├── vite.config.js
├── .env.example              # env template (VITE_API_BASE_URL)
├── src/
│   ├── main.jsx              # React entrypoint
│   ├── App.jsx               # root: data fetch + layout + shared state
│   ├── api.js                # fetch wrapper; separates 409 / 503 / unreachable
│   ├── styles.css            # plain CSS (single file)
│   ├── utils/
│   │   └── priority.js       # formatting, band colours, map bounds
│   └── components/
│       ├── HotspotMap.jsx          # Leaflet map (OSM tiles, circle markers)
│       ├── HotspotPanel.jsx        # factor breakdown + evidence
│       ├── RecommendationsList.jsx # ranked funding plan + band filters
│       └── PriorityChart.jsx       # band mix + district means
└── README.md
```

## Troubleshooting

- **"Module 6 is not responding"** — start Module 6 (`uvicorn app.main:app
  --port 8006`) and confirm `VITE_API_BASE_URL` matches its host and port, then
  hard-refresh.
- **"Nothing has been scored yet"** — seed Module 5 and start Module 6; the
  message names the command.
- **Blank map** — OSM tiles need a network connection. Everything else works
  offline; only tile imagery is missing.
- **Stale scores after a recompute** — press Refresh. Module 6 replaces the
  ranking in place, so a refresh is the only way to pick up a new run.

## License

Academic project.