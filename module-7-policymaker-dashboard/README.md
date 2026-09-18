# Module 7: Policymaker Dashboard

React + Vite visualisation layer for Niti-Setu. It consumes Module 6's
Priority & Recommendation API and shows policymakers **where** to act (map),
**what** to fund (ranked recommendations), **why** (transparent score
breakdown), and a quick cross-category summary (chart).

- **Leaflet + OpenStreetMap** free tiles (no API key), hotspots as circle
  markers sized/coloured by priority score.
- **Recharts** bar chart: average & max priority score by category.
- Filterable, ranked **recommended-projects list** with plain-language copy
  straight from Module 6.
- Zero paid services; plain CSS (no UI kit).

## Prereqs

- Node.js 18+ (tested on 22)
- Module 6 running and serving `/hotspots` + `/recommendations`

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

## What you'll see

| Area | What it shows |
| ---- | ------------- |
| Map | Every hotspot as a coloured, sized circle. Red = high, amber = medium, green = low priority. Click a marker for details. |
| Side panel | Category, request count, the **full factor-by-factor score breakdown** (raw → normalised → weighted contribution for volume, severity, density, days open) and sample citizen complaints as evidence. |
| Recommendations | Top projects ranked by priority score, with a plain-language recommendation per project. Filter by `category` and `region`. |
| Chart | Average & max priority score per category for a one-glance summary. |

## Configuration

| Variable | Purpose | Default |
| -------- | ------- | ------- |
| `VITE_API_BASE_URL` | Base URL of Module 6's FastAPI service | `http://localhost:8006` |

Vite only exposes env vars prefixed with `VITE_`. See `.env.example`.

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
│   ├── api.js                # thin fetch wrapper over Module 6 endpoints
│   ├── styles.css            # plain CSS (single file)
│   ├── utils/
│   │   └── priority.js       # score -> colour / size / band / labels
│   └── components/
│       ├── HotspotMap.jsx          # Leaflet map (OSM tiles, circle markers)
│       ├── HotspotPanel.jsx        # side panel with transparent breakdown
│       ├── RecommendationsList.jsx # ranked list + category/region filters
│       └── PriorityChart.jsx       # Recharts bar chart by category
└── README.md
```

## Troubleshooting

- **"API error … Is Module 6 running?"** — Start Module 6 (`python main.py` in
  `module-6-priority-recommendation`) and confirm `VITE_API_BASE_URL` matches
  its host/port, then hard-refresh `http://localhost:5173`.
- **Blank map / missing tiles** — OSM tiles require a network connection. The
  rest of the dashboard still works offline; only tile imagery is missing.

## License

Academic project.