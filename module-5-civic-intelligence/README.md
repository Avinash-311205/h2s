# Module 5 - Civic Intelligence

Reads Module 4's data mesh and answers three questions a district officer asks
before deciding where to send a team:

1. **Where is demand concentrated right now?** (hotspots)
2. **What is getting better or worse, and how fast?** (trends)
3. **What deserves attention before anyone asks?** (emerging risks)

Every output carries the evidence that produced it. A hotspot says which wards
made it and at what intensity; a trend says the series it was computed from; a
risk says which rule fired and the numbers behind it. Nothing here is a score
whose derivation has to be reverse-engineered.

## Scope

Module 5 **detects**, it does not **prioritise**. Ranking gaps into a
budget-ordered intervention list belongs to Module 6 (Priority & Recommendation),
so the interfaces meet at `risk_code` and `ward_code` rather than overlapping.
This module will happily tell you a gap is CRITICAL and worsening; what to do
about it is the next module's question.

## Snapshots, then intelligence

The design decision the rest follows from: **the module keeps its own copy of
the mesh over time.**

```
Module 4 database  --sync-->  demand_windows    (history, read-only copy)
                          ->  gap_snapshots
                          ->  project_snapshots
                                     |
                                     v
                                 analytics
                                     |
                    +----------------+----------------+
                    v                v                v
                 hotspots          trends       emerging_risks
```

Snapshots and outputs are separate tables, deliberately. A re-run of the
analytics must never mutate the recorded history it is analysing, and keeping
them apart makes that structural rather than a matter of discipline.

Three consequences worth knowing:

- **Sync is idempotent.** Re-syncing unchanged mesh data inserts nothing, so it
  is safe to run on a schedule. `sync_from_mesh` returns per-domain insert
  counts, and all-zero is a success.
- **Project snapshots record changes, not polls.** A project is re-snapshotted
  only when status, spend, budget or a date actually moved - otherwise a nightly
  sync would append identical rows forever and call the result a history.
- **Analytics never write to a snapshot.** Re-running `analyse` overwrites the
  intelligence tables and nothing else.

### Reading the mesh

`app/services/mesh_client.py` reads Module 4's SQLite file directly instead of
calling its API. A sync then works with Module 4 stopped, and the two modules
stay independently runnable. Consequences:

- Non-SQLite `MESH_DATABASE_URL` values are **declined, not guessed** - pointing
  at PostgreSQL would need a different driver, and failing later is worse than
  saying so now.
- Missing tables are treated as "that domain has not been synced yet" and
  skipped, so a mesh with only GIS loaded yields an honest partial snapshot.
- An unreachable mesh is reported as `mesh_available: false` (HTTP 503 on
  `POST /operations/sync`), never as an empty success.

`POST /operations/sync?district=Madurai` scopes **every** domain, not just wards.
Demand, gaps and projects carry no district of their own — they are keyed on
`ward_code` — so they are scoped through the ward list the district resolved to.
Filtering wards alone would copy the whole country's demand while reporting
success, and those rows would be stored against ward locations that were never
copied, landing with a district of `UNKNOWN`.

## Hotspots

**Normalise before ranking.** Complaint counts alone just rank wards by size.
Intensity is complaints per 1,000 residents, so a dense ward of 4,000 with 200
complaints correctly outranks a sprawling ward of 30,000 with 600.

**Cluster by geography, not by rank.** Wards within `cluster_radius_m` (12 km
default) of each other join one cluster via union-find over haversine distance.
The result is a place you can send someone to, not an arbitrary top-N list.
Transitive closure is intended: a chain of wards each within the radius is one
contiguous cluster.

**Score against the city, not a constant.** A cluster's intensity is expressed
as standard deviations above the city-wide ward mean, and tiers are cut at
percentiles of the cluster distribution (`CRITICAL` top 10%, `HIGH` top 25%,
`MODERATE` top 50%). Both keep their meaning as the number of wards changes,
which a hard-coded "more than 500 complaints" threshold does not.

Cluster intensity uses the cluster-wide rate rather than a mean of ward rates,
so one large quiet ward cannot mask several small loud ones. Clusters with no
complaints at all are dropped: silence is not a finding, and including it would
drag the percentile cut-offs down for everyone else.

`/hotspots/summary` reports **coverage** - what share of the ward population the
hotspots account for. Hotspots covering 90% of the city is not a hotspot list,
it is a city-wide problem, and that is what makes the distinction visible.

## Trends

A trend needs at least two observations, so a single window yields `UNKNOWN`
rather than a guess. Direction requires the percentage move and the least-squares
slope to **agree**: a rising percentage with a flat slope is one bad window, not
a trend, and a falling percentage with a rising slope is a rebound. A move below
`trend_change_threshold_pct` (10%) is `STABLE`, because "complaints went from 4
to 5" is not worth an officer's time.

`momentum` (the most recent step) is reported separately from `pct_change`,
because a ward can be improving on average while its latest window got sharply
worse - exactly the case a single average would hide.

**Only the latest window feeds a hotspot.** Consecutive 30-day windows share a
`window_days` value, so summing every matching row would count the whole history
and make a hotspot look worse every time another window is synced.

## Emerging risks

Five rules, deliberately not a learned model. With this little data, a
transparent threshold a reviewer can dispute beats a score nobody can explain.
Each risk records the rule that fired and the numbers that triggered it.

| Rule | Fires when | Severity |
|---|---|---|
| `SPIKE` | recent window is >= 2x the previous, and >= 10 complaints in absolute terms | scales with the ratio |
| `NEW_CATEGORY` | previously silent ward-sector, now >= 3 complaints | MEDIUM |
| `DETERIORATING_WITH_SPEND` | complaints rising while active projects hold committed, unspent money | CRITICAL if absorption is low |
| `STALLED_ABSORPTION` | project past 50% of its window with under 35% of budget spent | HIGH if the shortfall is large |
| `CRITICAL_CONCENTRATION` | a CRITICAL-band gap whose complaints are still rising | CRITICAL |

Two guards keep these from crying wolf:

- **The absolute floor on `SPIKE`.** A jump from 1 to 3 is a doubling and
  meaningless; the floor is what stops it reading as an emergency.
- **Two windows minimum, everywhere.** A ward-sector with a single window has no
  direction, so it is skipped rather than judged on one observation. A
  zero-to-non-zero jump is `NEW_CATEGORY`, never `SPIKE` - the ratio is unbounded.

`DETERIORATING_WITH_SPEND` exists because it is the expensive failure: the ward
is not neglected, it is being served and still deteriorating, which means the
intervention is wrong rather than insufficient.

Risks are keyed by a stable `risk_code` (`{TYPE}:{WARD}:{SECTOR}`), so
re-detection **updates** rather than duplicates, and an officer's decision to
acknowledge or dismiss a risk survives the next run. Only the evidence is
refreshed.

## Running it

```bash
pip install -r requirements.txt

# 1. The mesh must exist first
python ../module-4-national-data-mesh/seed_mesh.py --reset

# 2. Give Module 5 something to analyse
python seed_intelligence.py --reset

# 3. Serve
python -m uvicorn app.main:app --reload --port 8005
```

Then `http://localhost:8005/docs`.

`seed_intelligence.py` adopts the mesh's own wards, gaps and projects when the
mesh is reachable, and backfills the earlier demand windows around it - because a
trend needs two windows and the mesh only holds the present. A bare sync leaves
every trend `UNKNOWN`; that is correct behaviour for one observation, and the
seed exists so the analytics are demonstrable out of the box. Backfilled rows are
marked `source="seed_history"` and can never be mistaken for synced data. Use
`--no-mesh` to generate a fully synthetic district instead.

The schema is created on startup, so a fresh clone runs with no migration step.

## API

| Method | Path | Answers |
|---|---|---|
| GET | `/health` | is it alive, does it have data, is the mesh reachable? |
| GET | `/health/mesh` | which upstream is it configured against? |
| GET | `/api/v1/hotspots` | clusters, worst first (`tier`, `district`, `window_days`) |
| GET | `/api/v1/hotspots/summary` | tier counts, district rollup, population coverage |
| GET | `/api/v1/hotspots/wards/{ward_code}/brief` | one ward's demand profile |
| GET | `/api/v1/trends` | per ward-sector (`direction`, `sector`, `ward_code`) |
| GET | `/api/v1/trends/summary` | direction mix, per-sector matrix, fastest-worsening |
| GET | `/api/v1/trends/wards/{ward_code}` | every sector trend for one ward |
| GET | `/api/v1/risks` | detected risks (`risk_type`, `severity`, `status`) |
| GET | `/api/v1/risks/summary` | counts by type and severity, open vs total |
| GET | `/api/v1/risks/wards/{ward_code}` | open risks for one ward |
| PATCH | `/api/v1/risks/{risk_code}/status` | acknowledge, dismiss or resolve |
| POST | `/api/v1/operations/sync` | copy the mesh in (idempotent) |
| POST | `/api/v1/operations/analyse` | recompute hotspots, trends, risks |
| POST | `/api/v1/operations/refresh` | sync then analyse |
| GET | `/api/v1/operations/runs` | audit trail of recent analytics runs |

Unknown tier/direction values are rejected with 422 rather than silently
returning everything; `sync` without a mesh returns 503 with the remedy in the
detail message.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./civic_intelligence.db` | local store |
| `MESH_DATABASE_URL` | `sqlite:///../module-4-national-data-mesh/national_data_mesh.db` | upstream mesh |
| `CLUSTER_RADIUS_M` | `12000` | neighbour distance for clustering |
| `POPULATION_PER_UNIT` | `1000` | complaints per this many residents |
| `TREND_WINDOWS_DAYS` | `[30, 90]` | window lengths analysed |
| `TREND_CHANGE_THRESHOLD_PCT` | `10.0` | below this a change is `STABLE` |
| `SPIKE_RATIO` | `2.0` | spike multiple |
| `SPIKE_MIN_COMPLAINTS` | `10` | spike absolute floor |
| `STALL_ELAPSED_FRACTION` | `0.5` | project age before judging absorption |
| `STALL_SPEND_RATIO` | `0.35` | spend below this while stalled |

## Tests

```bash
python -m pytest
```

267 tests covering the statistics, the clustering, every risk rule's firing *and
non-firing* conditions, sync idempotency, review-status preservation, and the
full HTTP contract. `DATABASE_URL` is forced to `sqlite://` in `conftest.py`
before any import, so the suite cannot touch a real database.

## Known limits

- Union-find clustering is O(n²) in ward count. Fine into the low thousands of
  wards; beyond that a spatial index (rtree, PostGIS) is the fix and the
  interface does not change.
- Rules are rule-based, so an unusual-but-real failure mode that no rule covers
  will be missed. That is the trade for explainability, and the rule set is the
  place to extend.
- The mesh is read as a file. Pointing `MESH_DATABASE_URL` at a network database
  would need a driver added here; the client declines non-SQLite URLs rather
  than failing obscurely later.