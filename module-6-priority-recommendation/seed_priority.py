#!/usr/bin/env python3
"""Produce a priority ranking from a Module 5 intelligence snapshot.

Run this before starting the API, or whenever Module 5's output changes:

    python seed_priority.py                       # real Module 5 snapshot
    python seed_priority.py --synthetic           # self-contained demo data
    python seed_priority.py --intelligence-url sqlite:///other/snapshot.db

A synthetic mode exists so the dashboard can be demonstrated without running the
whole pipeline from Module 1. It writes a Module 5-*shaped* database, not a
Module 6 one, so the code path exercised is identical to the real run - which
means a green synthetic run is genuine evidence, not a mock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.database.connection import (  # noqa: E402
    build_engine,
    create_tables,
)
from app.services import analytics_service  # noqa: E402
from app.services.intelligence_client import IntelligenceClient  # noqa: E402

DEFAULT_INTELLIGENCE = (
    MODULE_DIR.parent / "module-5-civic-intelligence" / "civic_intelligence.db"
)

#: Six districts with real coordinates and plausible sector mixes, so the map
#: shows a recognisable Tamil Nadu rather than a cluster of default points.
SYNTHETIC_DISTRICTS = [
    ("Chennai", 13.0827, 80.2707, ["WATER", "ROAD", "ELECTRICITY"]),
    ("Coimbatore", 11.0168, 76.9558, ["WATER", "ROAD"]),
    ("Madurai", 9.9252, 78.1198, ["WATER", "ELECTRICITY", "WASTE"]),
    ("Trichy", 10.7905, 78.7047, ["ROAD", "WATER"]),
    ("Salem", 11.6643, 78.1460, ["WASTE", "WATER", "ROAD"]),
    ("Tirunelveli", 8.7139, 77.7567, ["WATER", "ELECTRICITY"]),
]


def build_synthetic_intelligence(path: Path) -> None:
    """Write a Module 5-shaped snapshot with deterministic demo data.

    Deterministic on purpose - the same input must produce the same ranking, or
    a demo cannot be used to check whether a change altered the output.
    """
    sys.path.insert(0, str(MODULE_DIR))
    from synthetic_snapshot import create_tables

    if path.exists():
        path.unlink()
    conn = create_tables(path)
    for index, (district, lat, lon, sectors) in enumerate(SYNTHETIC_DISTRICTS, start=1):
        wards = [f"W{index}1", f"W{index}2"]
        code = f"HS-SYN-{index:03d}"
        population = 40_000 + index * 5_000
        complaints = 180 + index * 35
        critical = 8 + index * 3

        conn.execute(
            """
            INSERT INTO hotspots (hotspot_code, district, centroid_latitude,
                                  centroid_longitude, ward_codes, sectors,
                                  window_days, total_complaints,
                                  critical_complaints, population)
            VALUES (?,?,?,?,?,?,30,?,?,?)
            """,
            (code, district, lat, lon, json.dumps(wards), json.dumps(sectors),
             complaints, critical, population),
        )
        for ward_index, ward in enumerate(wards):
            sector = sectors[ward_index % len(sectors)]
            ward_population = population // len(wards)
            ward_complaints = complaints // len(wards) + ward_index * 11
            conn.execute(
                """
                INSERT INTO demand_windows (ward_code, district, sector, window_days,
                                            complaint_count, critical_count,
                                            avg_severity, population)
                VALUES (?,?,?,30,?,?,?,?)
                """,
                (ward, district, sector, ward_complaints, critical // len(wards),
                 2.8 + index * 0.2, ward_population),
            )
            # An older, worse snapshot plus a recent better one, so "latest
            # snapshot wins" is genuinely exercised rather than assumed.
            conn.execute(
                """
                INSERT INTO gap_snapshots (ward_code, district, sector, snapshot_at, gap_score)
                VALUES (?,?,?,'2025-06-01',?)
                """,
                (ward, district, sector, 70.0 - index * 4),
            )
            conn.execute(
                """
                INSERT INTO gap_snapshots (ward_code, district, sector, snapshot_at, gap_score)
                VALUES (?,?,?,'2026-09-01',?)
                """,
                (ward, district, sector, 30.0 + index * 5),
            )
            trend_direction = "WORSENING" if index % 3 else "STABLE"
            conn.execute(
                """
                INSERT INTO trends (ward_code, district, sector, window_days, direction,
                                    first_count, last_count, sample_count, pct_change, series)
                VALUES (?,?,?,90,?,10,?,3,?,?)
                """,
                (ward, district, sector, trend_direction, 10 + index,
                 (index * 12) if trend_direction == "WORSENING" else 0,
                 json.dumps([10, 11 + index])),
            )
            status = "STALLED" if index % 2 else "IN_PROGRESS"
            conn.execute(
                """
                INSERT INTO project_snapshots (project_code, ward_code, district, sector,
                                               status, budget_lakhs, spent_lakhs,
                                               delay_days, snapshot_at)
                VALUES (?,?,?,?,?,?,?,?,'2026-09-01')
                """,
                (f"P-SYN-{index}{ward_index}", ward, district, sector, status,
                 120.0 + index * 10, 20.0 + index * 8, index * 14),
            )
    conn.commit()
    conn.close()


def report(session, result: dict) -> None:
    """Print the ranking as a table, and say so plainly if nothing was scored."""
    if not result["scored"]:
        print("\nNo hotspots in the snapshot, so there is nothing to rank.")
        print("Run module-5-civic-intelligence/seed_intelligence.py first.")
        return

    payload = analytics_service.priorities_payload(session)
    summary = analytics_service.summary_payload(session)

    print(f"\nPriority ranking  (source: {result['source']}, run #{result['run_id']})")
    print("=" * 108)
    print(
        f"{'#':<4}{'Hotspot':<16}{'District':<13}{'Score':>7}{'Band':>9}  "
        f"{'Sector':<13}{'Cost (L)':>10}  Unmeasured"
    )
    print("-" * 108)
    for item in payload["priorities"]:
        unmeasured = ",".join(item["unmeasured_factors"]) or "-"
        print(
            f"{item['rank']:<4}{item['hotspot_code']:<16}{item['district']:<13}"
            f"{item['score']:>7.1f}{item['band']:>9}  "
            f"{str(item['dominant_sector'] or '-'):<13}"
            f"{item['recommended_cost_lakhs']:>10.1f}  {unmeasured}"
        )
    print("-" * 108)
    print(
        f"{summary['scored']} hotspots across {summary['districts']} districts  ·  "
        f"bands {summary['bands']}  ·  envelope Rs {summary['total_cost_lakhs']:.1f} lakh"
    )

    if result["degraded_factors"]:
        print(f"\nDegraded factors (upstream data missing): {result['degraded_factors']}")
        print("Scores are scaled down by the share of weight that was measurable.")

    for item in payload["priorities"][:3]:
        if item["recommendation"]:
            print(f"  {item['rank']}. {item['district']}: {item['recommendation']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a Module 5 snapshot.")
    parser.add_argument(
        "--intelligence-url",
        default=f"sqlite:///{DEFAULT_INTELLIGENCE}",
        help="Module 5 snapshot to score (default: the sibling module's database)",
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="where to write this module's database",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="build a self-contained Module 5-shaped snapshot and score that",
    )
    parser.add_argument(
        "--synthetic-path",
        default=str(MODULE_DIR / "synthetic_intelligence.db"),
        help="where to write the synthetic snapshot",
    )
    args = parser.parse_args()

    intelligence_url = args.intelligence_url
    if args.synthetic:
        synthetic = Path(args.synthetic_path)
        build_synthetic_intelligence(synthetic)
        intelligence_url = f"sqlite:///{synthetic}"
        print(f"Built synthetic Module 5 snapshot at {synthetic}")

    if not Path(intelligence_url.replace("sqlite:///", "")).exists():
        print(f"error: intelligence snapshot not found: {intelligence_url}", file=sys.stderr)
        print(
            "Run module-5-civic-intelligence/seed_intelligence.py, or pass --synthetic.",
            file=sys.stderr,
        )
        return 1

    target_engine = build_engine(args.database_url)
    create_tables(target_engine)
    session = sessionmaker(
        bind=target_engine, autoflush=False, expire_on_commit=False
    )()
    try:
        client = IntelligenceClient(intelligence_url)
        result = analytics_service.recompute(session, trigger="seed", client=client)
        report(session, result)
    finally:
        session.close()
        target_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())