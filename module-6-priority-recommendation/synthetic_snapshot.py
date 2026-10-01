"""Builds a Module 5-shaped intelligence database for tests.

Tests need an upstream that has the *same schema* as the real Module 5, or they
pass against a fixture the production code would never see. So this creates the
real tables with the real column names and nothing else.

Every builder can omit data on purpose - that is how the degraded-factor and
unmeasured-factor tests work, by leaving out the rows a healthy snapshot would
have.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEMAND_WINDOWS = """
CREATE TABLE IF NOT EXISTS demand_windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ward_code TEXT NOT NULL,
    district TEXT,
    sector TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    window_days INTEGER NOT NULL DEFAULT 30,
    complaint_count INTEGER NOT NULL DEFAULT 0,
    critical_count INTEGER NOT NULL DEFAULT 0,
    avg_severity REAL NOT NULL DEFAULT 0.0,
    population INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'mesh',
    captured_at TEXT
)
"""

GAP_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS gap_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ward_code TEXT NOT NULL,
    district TEXT,
    sector TEXT NOT NULL,
    snapshot_at TEXT,
    gap_score REAL NOT NULL DEFAULT 0.0,
    severity TEXT NOT NULL DEFAULT 'LOW',
    demand_score REAL NOT NULL DEFAULT 0.0,
    absence_score REAL NOT NULL DEFAULT 0.0,
    quality_score REAL NOT NULL DEFAULT 0.0,
    recommended_action TEXT,
    active_project_count INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'mesh'
)
"""

PROJECT_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS project_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code TEXT NOT NULL,
    ward_code TEXT NOT NULL,
    district TEXT,
    sector TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PLANNED',
    title TEXT,
    budget_lakhs REAL NOT NULL DEFAULT 0.0,
    spent_lakhs REAL NOT NULL DEFAULT 0.0,
    sanctioned_on TEXT,
    expected_completion_on TEXT,
    delay_days INTEGER NOT NULL DEFAULT 0,
    snapshot_at TEXT,
    source TEXT NOT NULL DEFAULT 'mesh'
)
"""

TRENDS = """
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ward_code TEXT NOT NULL,
    district TEXT,
    sector TEXT NOT NULL,
    window_days INTEGER NOT NULL DEFAULT 90,
    direction TEXT NOT NULL DEFAULT 'UNKNOWN',
    first_count INTEGER NOT NULL DEFAULT 0,
    last_count INTEGER NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 0,
    pct_change REAL NOT NULL DEFAULT 0.0,
    slope_per_window REAL NOT NULL DEFAULT 0.0,
    momentum REAL NOT NULL DEFAULT 0.0,
    volatility REAL NOT NULL DEFAULT 0.0,
    series TEXT,
    computed_at TEXT
)
"""

HOTSPOTS = """
CREATE TABLE IF NOT EXISTS hotspots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hotspot_code TEXT NOT NULL UNIQUE,
    district TEXT NOT NULL,
    centroid_latitude REAL NOT NULL DEFAULT 0.0,
    centroid_longitude REAL NOT NULL DEFAULT 0.0,
    ward_codes TEXT NOT NULL DEFAULT '[]',
    sectors TEXT NOT NULL DEFAULT '[]',
    window_days INTEGER NOT NULL DEFAULT 30,
    total_complaints INTEGER NOT NULL DEFAULT 0,
    critical_complaints INTEGER NOT NULL DEFAULT 0,
    population INTEGER NOT NULL DEFAULT 0,
    mean_intensity REAL NOT NULL DEFAULT 0.0,
    peak_intensity REAL NOT NULL DEFAULT 0.0,
    peak_severity REAL NOT NULL DEFAULT 0.0,
    intensity_z_score REAL NOT NULL DEFAULT 0.0,
    tier TEXT NOT NULL DEFAULT 'NORMAL',
    computed_at TEXT,
    window_start TEXT,
    window_end TEXT
)
"""


def create_tables(path: Path, *, tables: Iterable[str] | None = None) -> sqlite3.Connection:
    """Create the Module 5 tables this module reads.

    ``tables`` selects a subset; omitting one is how a test simulates an upstream
    database from an older Module 5 that never had that table.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    selected = set(tables) if tables is not None else {
        "hotspots",
        "demand_windows",
        "gap_snapshots",
        "project_snapshots",
        "trends",
    }
    for name, ddl in (
        ("demand_windows", DEMAND_WINDOWS),
        ("gap_snapshots", GAP_SNAPSHOTS),
        ("project_snapshots", PROJECT_SNAPSHOTS),
        ("trends", TRENDS),
        ("hotspots", HOTSPOTS),
    ):
        if name in selected:
            conn.execute(ddl)
    conn.commit()
    return conn


def add_hotspot(
    conn: sqlite3.Connection,
    code: str,
    district: str = "Chennai",
    wards: list[str] | None = None,
    sectors: list[str] | None = None,
    population: int = 10_000,
    complaints: int = 100,
    critical: int = 0,
    latitude: float = 13.08,
    longitude: float = 80.27,
) -> None:
    """Insert a hotspot. Wards and sectors default to a single-element list."""
    conn.execute(
        """
        INSERT INTO hotspots (hotspot_code, district, centroid_latitude, centroid_longitude,
                              ward_codes, sectors, window_days, total_complaints,
                              critical_complaints, population)
        VALUES (?,?,?,?,?,?,30,?,?,?)
        """,
        (
            code,
            district,
            latitude,
            longitude,
            json.dumps(wards or [f"{code}-W1"]),
            json.dumps(sectors or ["WATER"]),
            complaints,
            critical,
            population,
        ),
    )
    conn.commit()


def add_demand(
    conn: sqlite3.Connection,
    ward: str,
    sector: str = "WATER",
    complaints: int = 100,
    critical: int = 0,
    severity: float = 3.0,
    population: int = 10_000,
) -> None:
    conn.execute(
        """
        INSERT INTO demand_windows (ward_code, district, sector, window_days,
                                    complaint_count, critical_count, avg_severity, population)
        VALUES (?,?,?,30,?,?,?,?)
        """,
        (ward, "Chennai", sector, complaints, critical, severity, population),
    )
    conn.commit()


def add_gap(
    conn: sqlite3.Connection,
    ward: str,
    sector: str = "WATER",
    gap_score: float = 40.0,
    snapshot_at: str = "2026-09-01",
) -> None:
    conn.execute(
        """
        INSERT INTO gap_snapshots (ward_code, district, sector, snapshot_at, gap_score, severity)
        VALUES (?,?,?,?,?,?)
        """,
        (ward, "Chennai", sector, snapshot_at, gap_score, "MEDIUM" if gap_score >= 40 else "LOW"),
    )
    conn.commit()


def add_project(
    conn: sqlite3.Connection,
    project_code: str,
    ward: str,
    sector: str = "WATER",
    status: str = "IN_PROGRESS",
    budget: float = 100.0,
    spent: float = 40.0,
    delay_days: int = 30,
) -> None:
    conn.execute(
        """
        INSERT INTO project_snapshots (project_code, ward_code, district, sector, status,
                                       budget_lakhs, spent_lakhs, delay_days, snapshot_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (project_code, ward, "Chennai", sector, status, budget, spent, delay_days, "2026-09-01"),
    )
    conn.commit()


def add_trend(
    conn: sqlite3.Connection,
    ward: str,
    sector: str = "WATER",
    direction: str = "WORSENING",
    pct_change: float = 40.0,
) -> None:
    conn.execute(
        """
        INSERT INTO trends (ward_code, district, sector, window_days, direction,
                            first_count, last_count, sample_count, pct_change, series)
        VALUES (?,?,?,90,?,10,14,3,?,?)
        """,
        (ward, "Chennai", sector, direction, pct_change, json.dumps([10, 12, 14])),
    )
    conn.commit()


def healthy_snapshot(path: Path) -> sqlite3.Connection:
    """Two well-populated hotspots with every signal present."""
    conn = create_tables(path)
    add_hotspot(conn, "HS-1", "Chennai", ["W1", "W2"], ["WATER", "ROAD"], 20_000, 200, 20)
    add_demand(conn, "W1", "WATER", 150, 12, 3.5, 12_000)
    add_demand(conn, "W2", "ROAD", 90, 8, 4.0, 8_000)
    add_gap(conn, "W1", "WATER", 55.0)
    add_gap(conn, "W2", "ROAD", 40.0)
    add_project(conn, "P1", "W1", "WATER", "STALLED", 200.0, 30.0, 120)
    add_project(conn, "P2", "W2", "ROAD", "IN_PROGRESS", 150.0, 90.0, 20)
    add_trend(conn, "W1", "WATER", "WORSENING", 55.0)
    add_trend(conn, "W2", "ROAD", "IMPROVING", -20.0)

    add_hotspot(conn, "HS-2", "Coimbatore", ["W3"], ["ELECTRICITY"], 15_000, 90, 4)
    add_demand(conn, "W3", "ELECTRICITY", 90, 4, 2.5, 15_000)
    add_gap(conn, "W3", "ELECTRICITY", 25.0)
    add_project(conn, "P3", "W3", "ELECTRICITY", "IN_PROGRESS", 80.0, 70.0, 0)
    add_trend(conn, "W3", "ELECTRICITY", "STABLE", 2.0)
    return conn


def build(path: Path, **kwargs: Any) -> sqlite3.Connection:
    """Create a healthy snapshot at ``path``."""
    return healthy_snapshot(path)