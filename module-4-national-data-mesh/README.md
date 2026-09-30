# Module 4 - National Data Mesh

Joins citizen complaints, GIS wards, infrastructure, investment and demographic
data on one key (`ward_code`) and turns that join into a **ranked, explainable
list of infrastructure gaps**.

Every number this module produces can be traced back to the rows behind it: each
gap stores the components that produced its score, the domain datasets carry
their own lineage and quality verdicts, and the coverage assumptions are served
from an endpoint rather than buried in code.

## Why this is a mesh and not a wide table

Each domain lives in its own table with its own refresh cadence:

| Domain | Table | Joins on | Upstream sources |
|---|---|---|---|
| Citizen | `citizen_demand` | `ward_code` | Module 2 understanding, Module 3 processing |
| GIS | `wards` | `ward_code` (the key) | OpenStreetMap, ward shapefiles, delimitation register |
| Infrastructure | `infrastructure_assets` | `ward_code` | Asset register, field inspection, work orders |
| Investment | `investment_projects` | `ward_code` | Budget register, project portfolio, GeVi |
| Demographics | `census_indicators` | `ward_code` | Census, NFSA households, electoral roll |
| Catalogue | `data_products` | domain | Lineage + quality for each of the above |

Raw complaint text never enters the mesh - only counts and mean severity. The
mesh is a join of aggregates, so it can be refreshed from one source without
invalidating the others.

## The gap score

For each `(ward, sector)` pair:

```
gap_score = 100 * (0.40 * demand + 0.35 * absence + 0.25 * quality)
```

**demand** (0-1) - complaint volume relative to the busiest ward *in that same
sector*, lifted by the severity citizens reported:
`0.6 * volume_share + 0.4 * (severity - 1) / 4`. Normalising per sector is what
makes scores comparable across sectors; a single global maximum would let a very
noisy water ward shrink every wifi complaint in the country to near zero.

**absence** (0-1) - how far short of full coverage the ward's assets fall,
where each asset type is converted to the population it serves (a borewell
covers ~800 people, a health clinic ~5,000). Only *functional* assets count: a
broken transformer serves nobody, and that distinction is the point of the
module.

**quality** (0-1) - how badly the existing assets are performing, from condition
grade, age and broken share.

Bands: `LOW < 35`, `MODERATE >= 35`, `HIGH >= 55`, `CRITICAL >= 75`.

Two deliberate refinements:

- **Unmeasurable sectors contribute no absence.** A road has no people-per-unit
  figure, so computing coverage for one would always yield 0% and make every
  ward in the country look like it had no roads. Roads are scored on demand and
  quality alone. `evidence.absence_measurable` records which case applies.
- **Sectors with no asset register are not scored at all.** Housing and public
  safety have no mapping, so they would emit a phantom gap per ward.
- **Complaints are filtered to the sector being scored**, otherwise every sector
  in a ward inherits the ward's total complaint count.

Because the components are stored next to the score, a policymaker can see which
signal drove a ranking, and a reviewer can recompute it by hand.

## Recommended actions

Chosen from *what is actually wrong*, not from the score alone:

| Action | Trigger |
|---|---|
| `BUILD_INFRASTRUCTURE` | no assets of the sector's types exist |
| `URGENT_REPAIR` | critical complaints and assets that are not functional |
| `EXPAND_CAPACITY` | assets serve well under the ward's population |
| `ACCELERATE_PROJECTS` | coverage adequate but complaints say quality is failing |
| `IMPROVE_QUALITY` | moderate score, maintenance is the lever |
| `MONITOR` | no significant gap, or work already under way |

## Quality and lineage

Each domain is scored on completeness, validity and timeliness
(weighted `0.40 / 0.40 / 0.20`), with declared refresh cadences:

| Domain | Refresh interval |
|---|---|
| Citizen | 7 days |
| Infrastructure | 30 days |
| Investment | 60 days |
| GIS | 90 days |
| Demographics | 365 days |

Timeliness decays to 0 at twice the interval, so a product two intervals stale
is `STALE` rather than quietly `HEALTHY`. `GET /api/v1/quality` recomputes these
live instead of returning the cached catalogue, so a decayed dataset cannot
masquerade as a healthy one.

## Investment analytics

`GET /api/v1/investment/summary` reports portfolio delivery: absorption rate
(spent ÷ sanctioned), delays, and **stalled projects** - sanctioned beyond half
their window with under 35% of the money spent. That is the common municipal
failure mode where a budget line exists, so the ward looks funded, while nothing
has been built. Cancelled projects are excluded from capex totals: money spent
on a cancelled project bought no capacity.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness and row counts; `degraded` until wards exist |
| GET | `/api/v1/catalog` | Data products with lineage, quality and cadence |
| GET | `/api/v1/quality` | Live quality score per domain |
| GET | `/api/v1/wards` | List wards (filter by `district`, `state`) |
| GET | `/api/v1/districts` | District rollup |
| POST | `/api/v1/locate` | Assign a coordinate to a ward |
| GET | `/api/v1/assets` | Asset inventory with `degraded` flag |
| GET | `/api/v1/assets/breakdown` | Counts by asset type and status |
| GET | `/api/v1/demand/totals` | Complaint totals per sector |
| GET | `/api/v1/capacity-targets` | The people-per-asset assumptions |
| GET | `/api/v1/gaps` | Ranked gaps (filter by ward, sector, severity, `min_score`) |
| POST | `/api/v1/gaps/analyse` | Recompute the gap table |
| GET | `/api/v1/investment/projects` | Project list with absorption rate |
| GET | `/api/v1/investment/summary` | Delivery health and stalled projects |

`POST /api/v1/gaps/analyse` accepts `{"sectors": ["WATER"], "district": "...", "persist": true}`.
With `persist: true` the run is stored (so Module 5 can compare consecutive runs
and detect which gaps are actually improving) and the catalogue is refreshed.
With no wards loaded it returns `409` rather than an empty analysis.

Interactive docs at `/docs`.

## Geometry

Wards are stored as a centroid plus a bounding box, not a polygon. At the
resolution Module 3 already produces, that is enough to answer "which ward does
this complaint belong to", keeps the module dependency-free, and lets a point
outside every box be reported as **unassigned** instead of being snapped to a
distant ward and silently corrupting that ward's statistics. Points within 25 km
fall back to the nearest centroid, flagged `confident: false`.

## Running it

```bash
pip install -r requirements.txt
python seed_mesh.py --reset        # coherent synthetic Tamil Nadu mesh
uvicorn app.main:app --reload --port 8004
```

Configuration is environment-driven (`app/core/config.py`): `DATABASE_URL`
(default `sqlite:///./national_data_mesh.db`), `PORT`, `LOG_LEVEL`, `LOG_JSON`,
and the gap weights `WEIGHT_DEMAND` / `WEIGHT_ABSENCE` / `WEIGHT_QUALITY`,
which must sum to 1.0.

## Tests

```bash
python -m pytest        # 139 tests
```

Coverage includes geometry edge cases (poles, unbounded wards, out-of-range
coordinates), the fact that broken assets serve nobody, demand normalisation,
sector leakage, the band thresholds, persistence as upsert, and the full HTTP
contract against an in-memory mesh.

## Handing off to Module 5

Module 5 reads `gap_records` (ranked gaps with components) and
`investment_projects` (delivery trends) to produce hotspots, trends and emerging
risks. Because a gap stores the components behind its score, Module 5 can tell
an *absence* gap that is closing from a *demand* gap that is merely getting
louder.