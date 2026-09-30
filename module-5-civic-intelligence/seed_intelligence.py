"""Seed Module 5 with a realistic multi-window history.

    python seed_intelligence.py --reset
    python seed_intelligence.py --reset --windows 4 --no-mesh

Why this exists rather than a bare ``sync``: the mesh holds only the *current*
window of demand. A trend needs at least two windows, and a spike needs two
consecutive ones, so seeding from the mesh alone would leave every trend
``UNKNOWN`` and every risk undetected - a module that looks broken because it has
no history, not because its logic is wrong.

So this script takes the mesh's current window as ground truth and backfills the
earlier windows around it, with the shape of the history chosen deliberately:
some ward-sectors worsen, some improve, a few spike hard, and some go from
silent to loud. That is what makes the analytics meaningful out of the box, and
it is why the seeded output is stable enough to assert against in tests.

The backfilled windows are marked ``source="seed_history"`` so they can never be
mistaken for real synced data.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import ProjectStatus, Sector
from app.core.logging import configure_logging, get_logger
from app.core.utils import utcnow
from app.database.connection import SessionLocal, init_db
from app.database.models_registry import DemandWindow, GapSnapshot, IntelligenceRun, ProjectSnapshot, WardLocation
from app.repositories.intelligence_repository import IntelligenceRepository
from app.services import analytics_service, hotspot_service, mesh_client, risk_service
from app.services.mesh_client import _parse_dt as parse_datetime
from app.services.mesh_client import parse_observed_bounds

logger = get_logger(__name__)

# Wards are placed in a loose grid so geographic clustering has something real to
# work with: nearby wards share a base level of demand, which is what produces
# clusters instead of a scatter.
_DISTRICTS = ["Chennai", "Coimbatore", "Madurai", "Trichy", "Salem"]
_SECTORS = [
    Sector.WATER.value,
    Sector.ELECTRICITY.value,
    Sector.ROAD.value,
    Sector.SANITATION.value,
    Sector.HEALTHCARE.value,
    Sector.TRANSPORT.value,
]
_BASE_COORDS = {
    "Chennai": (13.0827, 80.2707),
    "Coimbatore": (11.0168, 76.9558),
    "Madurai": (9.9252, 78.1198),
    "Trichy": (10.7905, 78.7047),
    "Salem": (11.6643, 78.1460),
}
_WARDS_PER_DISTRICT = 6


def _ward_grid(rng: random.Random) -> list[WardLocation]:
    """Build the ward dimension: real-ish Tamil Nadu coordinates and populations."""
    wards: list[WardLocation] = []
    for district in _DISTRICTS:
        base_lat, base_lon = _BASE_COORDS[district]
        for index in range(_WARDS_PER_DISTRICT):
            # ~0.035 degrees is roughly 3.8km of latitude, so wards in a district
            # sit inside the 12km cluster radius and chain into one cluster.
            wards.append(
                WardLocation(
                    ward_code=f"{district[:3].upper()}-{index + 1:02d}",
                    name=f"{district} Ward {index + 1}",
                    district=district,
                    state="Tamil Nadu",
                    latitude=round(base_lat + (index // 3) * 0.035 + rng.uniform(-0.006, 0.006), 6),
                    longitude=round(base_lon + (index % 3) * 0.038 + rng.uniform(-0.006, 0.006), 6),
                    population=rng.randrange(6_000, 34_000),
                )
            )
    return wards


def _wards_from_mesh(snapshot) -> list[WardLocation]:
    """Use the mesh's own wards rather than a synthetic grid.

    Preferred whenever the mesh is reachable: seeding with the mesh's real
    geography and names means a seeded module and a synced module describe the
    same city, instead of two different ones.
    """
    return [
        WardLocation(
            ward_code=row.get("ward_code"),
            name=row.get("name") or row.get("ward_code"),
            district=row.get("district") or "UNKNOWN",
            state=row.get("state"),
            latitude=float(row.get("centroid_latitude") or 0.0),
            longitude=float(row.get("centroid_longitude") or 0.0),
            population=int(row.get("population") or 0),
        )
        for row in snapshot.wards
        if row.get("ward_code")
    ]


def _mesh_anchor(snapshot) -> dict:
    """``(ward, sector) -> (count, window_start, window_end)`` from the mesh.

    Carrying the window bounds matters as much as the counts: a seeded anchored
    row then shares its natural key with what a later real sync would write, so
    syncing the mesh afterwards is a no-op instead of a duplicate insert.
    """
    anchor: dict = {}
    for row in snapshot.demand:
        ward_code = row.get("ward_code")
        sector = row.get("sector")
        if not ward_code or not sector:
            continue
        start, end = parse_observed_bounds(row)
        anchor[(ward_code, sector)] = (int(row.get("complaint_count") or 0), start, end)
    return anchor


def _backfill_demand(
    wards: list[WardLocation],
    *,
    windows: int,
    window_days: int,
    rng: random.Random,
    anchor: dict | None,
) -> list[DemandWindow]:
    """Generate ``windows`` consecutive windows of demand ending today.

    ``anchor`` is the mesh's current window, used as the most recent one so the
    seeded history ends where real data begins. Without a mesh, the whole series
    is generated. Anchor values may be a bare count or a
    ``(count, window_start, window_end)`` tuple.
    """
    rows: list[DemandWindow] = []
    today = utcnow()
    by_code = {ward.ward_code: ward for ward in wards}

    # When a mesh is available, series are built on the mesh's own
    # (ward, sector) grain. Picking a sector at random and hoping it matches one
    # of the mesh's would make anchoring work only by accident; using the real
    # grain means the newest window lines up exactly with the synced data.
    grains = (
        [(code, sector) for (code, sector) in anchor if code in by_code]
        if anchor
        else []
    )
    if not grains:
        grains = [(ward.ward_code, rng.choice(_SECTORS)) for ward in wards]

    for ward_code, sector in grains:
        ward = by_code[ward_code]
        base = rng.randrange(12, 70)
        # Each ward-sector gets a trajectory, so a few worsen, a few improve and
        # the rest stay flat - the mix a real district looks like.
        trajectory = rng.choice(["worsening", "improving", "stable", "worsening"])
        silent = rng.random() < 0.18
        spiking = rng.random() < 0.12

        for step in range(windows):
            # Oldest window first.
            period = windows - step
            if silent and period > 1:
                count = 0
            elif silent:
                count = rng.randrange(4, 15)
            else:
                count = max(1, int(base * (0.85 ** period)))
                if trajectory == "worsening":
                    count = max(1, int(base * (0.78 ** period)))
                elif trajectory == "improving":
                    count = max(1, int(base * (1.28 ** period)))
                if spiking and period == 1:
                    count = max(1, int(count * rng.uniform(2.4, 4.2)))

            end = today - timedelta(days=window_days * (period - 1))
            start = end - timedelta(days=window_days)
            source = "seed_history"

            if anchor is not None and period == 1:
                # The most recent window comes from the mesh, not the generator,
                # so the seeded history ends exactly where real data begins.
                anchored = anchor.get((ward_code, sector), count)
                if isinstance(anchored, tuple):
                    count, start, end = anchored[0], anchored[1], anchored[2]
                else:
                    count = int(anchored)
                source = "mesh"

            rows.append(
                DemandWindow(
                    ward_code=ward_code,
                    district=ward.district,
                    sector=sector,
                    window_start=start,
                    window_end=end,
                    window_days=window_days,
                    complaint_count=count,
                    critical_count=max(0, round(count * rng.uniform(0.08, 0.25))),
                    avg_severity=round(rng.uniform(1.8, 4.6), 2),
                    population=ward.population,
                    source=source,
                    captured_at=end,
                )
            )
    return rows


def _gaps_from_mesh(snapshot, wards: list[WardLocation]) -> list[GapSnapshot]:
    """The mesh's current gap records, carrying the ward's district."""
    district_of = {ward.ward_code: ward.district for ward in wards}
    rows = []
    for row in snapshot.gaps:
        snapshot_at = parse_datetime(row.get("computed_at")) or utcnow()
        rows.append(
            GapSnapshot(
                ward_code=row.get("ward_code"),
                district=district_of.get(row.get("ward_code")),
                sector=row.get("sector") or "UNKNOWN",
                snapshot_at=snapshot_at,
                gap_score=float(row.get("gap_score") or 0.0),
                severity=row.get("severity") or "LOW",
                demand_score=float(row.get("demand_score") or 0.0),
                absence_score=float(row.get("absence_score") or 0.0),
                quality_score=float(row.get("quality_score") or 0.0),
                recommended_action=row.get("recommended_action"),
                active_project_count=int(row.get("active_project_count") or 0),
                source="mesh",
            )
        )
    return rows


def _projects_from_mesh(snapshot, wards: list[WardLocation]) -> list[ProjectSnapshot]:
    """The mesh's current project states, carrying the ward's district."""
    district_of = {ward.ward_code: ward.district for ward in wards}
    rows = []
    for row in snapshot.projects:
        rows.append(
            ProjectSnapshot(
                project_code=row.get("project_code"),
                ward_code=row.get("ward_code"),
                district=district_of.get(row.get("ward_code")),
                sector=row.get("sector") or "UNKNOWN",
                status=row.get("status") or "PLANNED",
                title=row.get("title"),
                budget_lakhs=float(row.get("budget_lakhs") or 0.0),
                spent_lakhs=float(row.get("spent_lakhs") or 0.0),
                sanctioned_on=parse_datetime(row.get("sanctioned_on")),
                expected_completion_on=parse_datetime(row.get("expected_completion_on")),
                delay_days=int(row.get("delay_days") or 0),
                snapshot_at=utcnow(),
                source="mesh",
            )
        )
    return rows


def _gap_band(score: float) -> str:
    """Module 4's gap bands.

    Spelled out as literals rather than reusing ``RiskSeverity``: gap bands are
    MODERATE where risk severities are MEDIUM, and borrowing one enum for the
    other would quietly blur two different vocabularies.
    """
    if score >= 75:
        return "CRITICAL"
    if score >= 55:
        return "HIGH"
    if score >= 35:
        return "MODERATE"
    return "LOW"


def _backfill_gaps(wards: list[WardLocation], *, rng: random.Random) -> list[GapSnapshot]:
    """One gap snapshot per ward, banded to match plausible scores."""
    today = utcnow()
    rows = []
    for ward in wards:
        score = round(rng.uniform(12.0, 96.0), 2)
        rows.append(
            GapSnapshot(
                ward_code=ward.ward_code,
                district=ward.district,
                sector=_SECTORS[0],
                snapshot_at=today,
                gap_score=score,
                severity=_gap_band(score),
                demand_score=round(score * 0.5, 2),
                absence_score=round(score * 0.3, 2),
                quality_score=round(score * 0.2, 2),
                recommended_action="EXPAND_CAPACITY" if score >= 55 else "MONITOR",
                active_project_count=rng.randrange(0, 3),
                source="seed",
            )
        )
    return rows


def _backfill_projects(wards: list[WardLocation], *, rng: random.Random) -> list[ProjectSnapshot]:
    """Projects in a mix of states, including stalled ones.

    At least two are deliberately stalled past half their window with almost no
    spend, so ``STALLED_ABSORPTION`` has something true to find.
    """
    today = utcnow()
    rows: list[ProjectSnapshot] = []
    statuses = [
        ProjectStatus.IN_PROGRESS.value,
        ProjectStatus.PLANNED.value,
        ProjectStatus.DELAYED.value,
        ProjectStatus.COMPLETED.value,
    ]

    for index, ward in enumerate(wards):
        if rng.random() < 0.45:
            continue
        status = statuses[index % len(statuses)]
        sanctioned = today - timedelta(days=rng.randrange(180, 900))
        duration = rng.randrange(365, 1_100)
        budget = round(rng.uniform(120.0, 2_400.0), 2)
        if index % 7 == 0 and status != ProjectStatus.COMPLETED.value:
            spent = round(budget * rng.uniform(0.03, 0.2), 2)
        elif status == ProjectStatus.COMPLETED.value:
            spent = budget
        else:
            spent = round(budget * rng.uniform(0.4, 0.92), 2)
        rows.append(
            ProjectSnapshot(
                project_code=f"TN-PROJ-{index + 1:03d}",
                ward_code=ward.ward_code,
                sector=_SECTORS[index % len(_SECTORS)],
                status=status,
                title=f"{_SECTORS[index % len(_SECTORS)].title()} works - {ward.ward_code}",
                budget_lakhs=budget,
                spent_lakhs=spent,
                sanctioned_on=sanctioned,
                expected_completion_on=sanctioned + timedelta(days=duration),
                delay_days=rng.randrange(0, 180) if status == ProjectStatus.DELAYED.value else 0,
                snapshot_at=today,
                source="seed",
            )
        )
    return rows


def seed(db: Session, *, windows: int = 3, window_days: int = 30, seed_value: int = 20260930) -> dict:
    """Populate every snapshot table, then run the analytics once.

    With a mesh reachable the seed adopts the mesh's own wards, gaps and projects
    and backfills only the earlier demand windows. Without one it generates a
    self-contained synthetic district. Either way the result is a dataset with
    enough history for trends, spikes and emerging risks to be detectable.
    """
    rng = random.Random(seed_value)

    snapshot = None
    if mesh_client.mesh_available():
        snapshot = mesh_client.fetch_snapshot()

    wards = _wards_from_mesh(snapshot) if snapshot and snapshot.wards else _ward_grid(rng)
    db.add_all(wards)
    db.flush()

    anchor = _mesh_anchor(snapshot) if snapshot else None
    if snapshot is not None:
        logger.info(
            "mesh_found",
            extra={"wards": len(snapshot.wards), "demand_rows": len(snapshot.demand)},
        )

    db.add_all(
        _backfill_demand(wards, windows=windows, window_days=window_days, rng=rng, anchor=anchor)
    )

    # Gaps and projects are taken from the mesh when available so the seeded
    # module agrees with a synced one; otherwise they are generated.
    if snapshot and snapshot.gaps:
        db.add_all(_gaps_from_mesh(snapshot, wards))
    else:
        db.add_all(_backfill_gaps(wards, rng=rng))

    if snapshot and snapshot.projects:
        db.add_all(_projects_from_mesh(snapshot, wards))
    else:
        db.add_all(_backfill_projects(wards, rng=rng))

    run = IntelligenceRun(kind="seed", window_days=window_days, run_at=utcnow())
    db.add(run)
    db.commit()

    counts = IntelligenceRepository(db).counts()
    logger.info("seeded_snapshots", extra=counts)
    return counts


def report(db: Session) -> dict:
    """Print what the analytics made of the seeded data, for a human reader."""
    repo = IntelligenceRepository(db)
    window = settings.trend_windows_days[0]
    hotspots = repo.list_hotspots(window_days=window, limit=5)
    trends = repo.list_trends(limit=5)
    risks = repo.list_risks(limit=5)

    print("\n=== Module 5 seed report ===")
    print(f"ward locations      : {repo.counts()['ward_locations']}")
    print(f"demand windows      : {repo.counts()['demand_windows']}")
    print(f"gap snapshots       : {repo.counts()['gap_snapshots']}")
    print(f"project snapshots   : {repo.counts()['project_snapshots']}")
    print(f"\ntop hotspots ({window}-day window):")
    for row in hotspots:
        print(
            f"  {row.hotspot_code}  {row.district:<12} tier={row.tier:<8} "
            f"intensity={row.mean_intensity:>7.2f}  wards={len(row.ward_codes)}"
        )
    print(f"\nwidest trend moves:")
    for row in trends:
        print(
            f"  {row.ward_code:<8} {row.sector:<12} {row.direction:<10} "
            f"change={row.pct_change:+7.1f}%  series={row.series}"
        )
    print(f"\ntop emerging risks:")
    for row in risks:
        print(f"  {row.risk_type:<26} {row.severity:<9} {row.title}")
    print()
    return {"hotspots": len(repo.list_hotspots()), "trends": len(repo.list_trends()), "risks": len(risks)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Module 5 civic intelligence.")
    parser.add_argument("--reset", action="store_true", help="drop the database before seeding")
    parser.add_argument("--windows", type=int, default=3, help="number of demand windows to generate")
    parser.add_argument("--window-days", type=int, default=30, help="length of each window")
    parser.add_argument("--no-mesh", action="store_true", help="ignore the mesh and generate everything")
    parser.add_argument("--no-analyse", action="store_true", help="seed snapshots without running analytics")
    args = parser.parse_args()

    configure_logging()
    if args.no_mesh:
        settings.mesh_database_url = "sqlite:///./__no_mesh__.db"

    if args.reset:
        target = Path(settings.database_url.split("sqlite:///")[-1])
        if target.exists():
            target.unlink()
            logger.info("database_reset", extra={"path": str(target)})

    init_db()
    with SessionLocal() as db:
        seed(db, windows=args.windows, window_days=args.window_days)
        if not args.no_analyse:
            analytics_service.analyse(db)
        report(db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())