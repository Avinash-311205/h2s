# Module 6 · Priority & Recommendation Engine

Ranks civic hotspots by need and turns that ranking into a funding plan.

This module does **not** re-cluster anything. It reads the hotspots, demand
windows, coverage gaps, trends and project snapshots that Module 5 produced, and
scores them. That is a deliberate boundary: the expensive analysis already
happened upstream, and repeating it here would mean two different answers to the
same question.

## Quick start

```bash
pip install -r requirements.txt

# Score the real Module 5 snapshot (seed it first if you have not)
python ../module-5-civic-intelligence/seed_intelligence.py
python seed_priority.py

# Or score self-contained demo data, no upstream needed
python seed_priority.py --synthetic

# Serve the API
uvicorn app.main:app --port 8006
```

## Two ideas that shape everything else

**Unmeasured is not zero.** If Module 5 has no trend for a ward-sector, the
trend factor contributes nothing *and is recorded as unmeasured*. A district
where nobody reports anything must not score the same as a district where
nothing is wrong.

To make that distinction visible in the number itself, the composite is scaled
by `evidence_coverage` — the share of total weight that was actually measured:

```
score = Σ(component × weight) × evidence_coverage × 100
```

Partial evidence therefore always scores *lower* than the same reading with full
evidence, never higher, and `evidence_coverage` is returned with every score so a
reader can see how much of it rests on real measurement. A hotspot measured on
three of five factors gets roughly 0.7× the score of the same reading fully
measured.

**References are fixed policy, not statistics.** The values in
`app/core/config.py` are the raw measurements that score a full 1.0. They are
constants on purpose: a percentile taken from today's data would make the same
complaint count score differently next month purely because the data moved,
destroying the comparability that the history table exists to provide.

> **Review point.** `ref_max_complaints_per_1000` is set to 5.0, calibrated
> against the seeded mesh where 30-day rates run 0.8–2.5 per 1,000. An earlier
> value of 25.0 pushed every real hotspot into the bottom 10% of the factor's
> range, which silently reduced the heaviest-weighted factor (0.30) to an
> effective weight near 0.03. Five complaints per 1,000 residents per month is a
> judgement call about what counts as elevated demand, not a measurement, and it
> is the number most worth a second opinion from someone who knows the domain.

## Scoring

| Factor | Weight | Measured from | Reference = full marks |
|---|---|---|---|
| `demand` | 0.30 | complaints per 1,000 residents | 5.0 / 1,000 |
| `severity` | 0.20 | average severity | 5.0 |
| `trend` | 0.20 | direction + % growth | 100% growth |
| `coverage` | 0.15 | service coverage gap | 100% |
| `service_failure` | 0.15 | stalled, unspent, delayed | 40% / 50% / 180d |

Each component saturates independently, so one extreme input cannot mask the
others. Bands: `HIGH ≥ 70`, `MEDIUM ≥ 45`, else `LOW`.

Trend is asymmetric on purpose — worsening and improving by the same percentage
are different situations, so improving maps to 0 and stable maps to a neutral
0.25 rather than being treated as healthy or alarming.

## What counts as degraded

If a Module 5 table is missing entirely, the affected factors are marked
degraded rather than the run failing. Three of five factors still produces a
usable ranking with visible caveats; no factors produces an error. A ranking
built from partial data is far more useful to a policymaker than a blank page.

Directional vocabulary is shared verbatim with Module 5 (`WORSENING`,
`IMPROVING`, `STABLE`) rather than translated. An earlier version used
`RISING`/`FALLING`, which matched nothing upstream: every hotspot fell through
to a zero trend contribution while still reporting `measured=True`, so the trend
factor silently did nothing on real data. `tests/test_intelligence_client.py`
guards against that recurring.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Works before any recompute — 0 scored is a valid answer |
| GET | `/health/intelligence` | Table presence and degradation detail |
| GET | `/api/v1/priorities` | Full ranking; `?district=`, `?band=` |
| GET | `/api/v1/priorities/summary` | Portfolio totals |
| GET | `/api/v1/plan` | Funding plan, `?size=` |
| GET | `/api/v1/priorities/{code}/history` | Previous rankings for one hotspot |
| GET | `/api/v1/operations/runs` | Run audit log |
| POST | `/api/v1/operations/recompute` | Rescore from the current snapshot |

Status codes carry meaning:

- **409** — the service is healthy but nothing has been scored yet. A workflow
  state the user can act on, distinct from a failure.
- **503** — Module 5's database is missing or unreadable.
- **422** — an invalid filter.

A failed recompute returns an error *and leaves the previous ranking intact*,
with the failure recorded on the run log. It never leaves stale scores looking
current.

## Data model

Three tables. `priority_scores` holds only the current ranking and is replaced
wholesale per run, so reads never see a half-finished recompute.
`priority_history` is append-only and holds previous rankings.
`priority_runs` records what each run attempted, whether it succeeded, and its
error if not.

## Tests

```bash
python -m pytest          # 112 passing
```

The suite concentrates on the behaviours that are easy to get subtly wrong:
that unmeasured never scores like measured-zero, that partial evidence cannot
inflate a score, that a failed run leaves the previous ranking readable, and that
the trend vocabulary actually matches the upstream database.