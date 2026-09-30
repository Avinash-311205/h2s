"""Data access for Module 5.

Snapshot writes are idempotent on their natural keys: re-syncing unchanged mesh
data must not create duplicate demand windows, or every trend would be computed
over an artificially long series.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import RiskStatus
from app.core.utils import naive_utc, utcnow
from app.models.intelligence_tables import (
    DemandWindow,
    EmergingRisk,
    GapSnapshot,
    Hotspot,
    IntelligenceRun,
    ProjectSnapshot,
    Trend,
    WardLocation,
)


class IntelligenceRepository:
    """CRUD and filtered reads over the snapshot and intelligence tables."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- snapshot ingestion -------------------------------------------------
    def store_snapshot(self, snapshot, *, snapshot_at: Optional[datetime] = None) -> dict[str, int]:
        """Persist a mesh snapshot, skipping rows already stored.

        Returns the number of rows actually inserted per domain, which is what a
        sync reports - "0 new demand windows" is a meaningful success.
        """
        moment = snapshot_at or utcnow()
        inserted = {
            "wards": self._store_wards(snapshot.wards),
            "demand": self._store_demand(snapshot.demand, moment),
            "gaps": self._store_gaps(snapshot.gaps, moment),
            "projects": self._store_projects(snapshot.projects, moment),
        }
        return inserted

    def _store_wards(self, rows: Sequence[dict]) -> int:
        existing = {ward.ward_code: ward for ward in self.session.scalars(select(WardLocation)).all()}
        inserted = 0
        for row in rows:
            code = row.get("ward_code")
            if not code:
                continue
            ward = existing.get(code)
            if ward is None:
                ward = WardLocation(ward_code=code)
                self.session.add(ward)
                existing[code] = ward
                inserted += 1
            ward.name = row.get("name") or code
            ward.district = row.get("district") or "UNKNOWN"
            ward.state = row.get("state")
            ward.latitude = float(row.get("centroid_latitude") or 0.0)
            ward.longitude = float(row.get("centroid_longitude") or 0.0)
            ward.population = int(row.get("population") or 0)
        self.session.flush()
        return inserted

    def _store_demand(self, rows: Sequence[dict], moment: datetime) -> int:
        existing = {
            (row.ward_code, row.sector, naive_utc(row.window_start), naive_utc(row.window_end))
            for row in self.session.scalars(select(DemandWindow)).all()
        }
        inserted = 0
        for row in rows:
            ward_code = row.get("ward_code")
            if not ward_code:
                continue
            start, end = _observed_bounds(row)
            key = (ward_code, row.get("sector") or "UNKNOWN", naive_utc(start), naive_utc(end))
            if key in existing:
                continue
            self.session.add(
                DemandWindow(
                    ward_code=ward_code,
                    district=_district_for(self.session, ward_code),
                    sector=row.get("sector") or "UNKNOWN",
                    window_start=start,
                    window_end=end,
                    window_days=int(row.get("window_days") or 30),
                    complaint_count=int(row.get("complaint_count") or 0),
                    critical_count=int(row.get("critical_count") or 0),
                    avg_severity=float(row.get("avg_severity") or 0.0),
                    population=0,
                    source="mesh",
                    captured_at=moment,
                )
            )
            existing.add(key)
            inserted += 1
        self.session.flush()
        return inserted

    def _store_gaps(self, rows: Sequence[dict], moment: datetime) -> int:
        existing = {
            (row.ward_code, row.sector, naive_utc(row.snapshot_at))
            for row in self.session.scalars(select(GapSnapshot)).all()
        }
        inserted = 0
        for row in rows:
            ward_code = row.get("ward_code")
            if not ward_code:
                continue
            sector = row.get("sector") or "UNKNOWN"
            # A second sync of unchanged data carries a new timestamp, so match
            # on the computed-at stamp the mesh itself provides when present.
            snapshot_at = _as_datetime(row.get("computed_at")) or naive_utc(moment)
            key = (ward_code, sector, naive_utc(snapshot_at))
            if key in existing:
                continue
            self.session.add(
                GapSnapshot(
                    ward_code=ward_code,
                    district=_district_for(self.session, ward_code),
                    sector=sector,
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
            existing.add(key)
            inserted += 1
        self.session.flush()
        return inserted

    def _store_projects(self, rows: Sequence[dict], moment: datetime) -> int:
        # A project is re-snapshotted only when its state actually changes. The
        # timestamp is the sync time, so keying on it alone would append an
        # identical row on every sync - unbounded growth that records nothing.
        # Comparing the stateful fields instead means the table is a genuine
        # history of how spend and status moved.
        latest: dict[str, ProjectSnapshot] = {}
        for existing_row in self.session.scalars(select(ProjectSnapshot)).all():
            current = latest.get(existing_row.project_code)
            if current is None or naive_utc(existing_row.snapshot_at) > naive_utc(current.snapshot_at):
                latest[existing_row.project_code] = existing_row

        inserted = 0
        for row in rows:
            code = row.get("project_code")
            ward_code = row.get("ward_code")
            if not code or not ward_code:
                continue
            snapshot_at = naive_utc(moment)
            previous = latest.get(code)
            if previous is not None and not _project_state_changed(previous, row):
                continue
            self.session.add(
                ProjectSnapshot(
                    project_code=code,
                    ward_code=ward_code,
                    district=_district_for(self.session, ward_code),
                    sector=row.get("sector") or "UNKNOWN",
                    status=row.get("status") or "PLANNED",
                    title=row.get("title"),
                    budget_lakhs=float(row.get("budget_lakhs") or 0.0),
                    spent_lakhs=float(row.get("spent_lakhs") or 0.0),
                    sanctioned_on=_as_datetime(row.get("sanctioned_on")),
                    expected_completion_on=_as_datetime(row.get("expected_completion_on")),
                    delay_days=int(row.get("delay_days") or 0),
                    snapshot_at=snapshot_at,
                    source="mesh",
                )
            )
            inserted += 1
        self.session.flush()
        return inserted

    # --- snapshot reads -----------------------------------------------------
    def list_ward_locations(self, *, district: Optional[str] = None) -> list[WardLocation]:
        statement = select(WardLocation).order_by(WardLocation.ward_code)
        if district:
            statement = statement.where(WardLocation.district == district)
        return list(self.session.scalars(statement).all())

    def list_demand(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        window_days: Optional[int] = None,
        district: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[DemandWindow]:
        statement = select(DemandWindow).order_by(DemandWindow.window_end)
        if ward_code:
            statement = statement.where(DemandWindow.ward_code == ward_code)
        if sector:
            statement = statement.where(DemandWindow.sector == sector)
        if window_days is not None:
            statement = statement.where(DemandWindow.window_days == window_days)
        if district:
            statement = statement.where(DemandWindow.district == district)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    def list_latest_demand(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        window_days: Optional[int] = None,
        district: Optional[str] = None,
    ) -> list[DemandWindow]:
        """Only the most recent window per (ward, sector, window length).

        Consecutive windows share a ``window_days`` value, so filtering on length
        alone returns the whole history. Anything describing "now" - hotspot
        intensity above all - has to read the latest window only, or its totals
        grow every time another window is synced.
        """
        statement = select(DemandWindow)
        if ward_code:
            statement = statement.where(DemandWindow.ward_code == ward_code)
        if sector:
            statement = statement.where(DemandWindow.sector == sector)
        if window_days is not None:
            statement = statement.where(DemandWindow.window_days == window_days)
        if district:
            statement = statement.where(DemandWindow.district == district)

        rows = list(self.session.scalars(statement).all())
        newest: dict[tuple[str, str, int], datetime] = {}
        for row in rows:
            key = (row.ward_code, row.sector, row.window_days)
            current = newest.get(key)
            if current is None or naive_utc(row.window_end) > naive_utc(current):
                newest[key] = row.window_end
        return [
            row
            for row in rows
            if naive_utc(row.window_end) == naive_utc(newest[(row.ward_code, row.sector, row.window_days)])
        ]

    def list_gaps(self, *, ward_code: Optional[str] = None) -> list[GapSnapshot]:
        statement = select(GapSnapshot).order_by(GapSnapshot.snapshot_at)
        if ward_code:
            statement = statement.where(GapSnapshot.ward_code == ward_code)
        return list(self.session.scalars(statement).all())

    def latest_gaps(self) -> list[GapSnapshot]:
        """Only the newest snapshot per ward-sector."""
        rows = self.session.scalars(select(GapSnapshot)).all()
        latest: dict[tuple[str, str], GapSnapshot] = {}
        for row in rows:
            key = (row.ward_code, row.sector)
            current = latest.get(key)
            if current is None or row.snapshot_at > current.snapshot_at:
                latest[key] = row
        return list(latest.values())

    def list_projects(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        district: Optional[str] = None,
    ) -> list[ProjectSnapshot]:
        """Latest state per project, since a project is re-snapshotted on change."""
        rows = self.session.scalars(select(ProjectSnapshot)).all()
        latest: dict[str, ProjectSnapshot] = {}
        for row in rows:
            current = latest.get(row.project_code)
            if current is None or naive_utc(row.snapshot_at) > naive_utc(current.snapshot_at):
                latest[row.project_code] = row
        projects = list(latest.values())
        if ward_code:
            projects = [p for p in projects if p.ward_code == ward_code]
        if sector:
            projects = [p for p in projects if p.sector == sector]
        if district:
            projects = [p for p in projects if p.district == district]
        return projects

    # --- hotspots -----------------------------------------------------------
    def replace_hotspots(self, hotspots: Sequence, *, window_days: int) -> int:
        """Upsert hotspots per window, so re-analysis updates in place."""
        from app.services.hotspot_service import window_bounds

        self.session.query(Hotspot).filter(Hotspot.window_days == window_days).delete()
        window_start, window_end = window_bounds(window_days)
        for index, hotspot in enumerate(hotspots, start=1):
            self.session.add(
                Hotspot(
                    hotspot_code=f"HS-{window_days}-{index:04d}",
                    district=hotspot.district,
                    centroid_latitude=hotspot.latitude,
                    centroid_longitude=hotspot.longitude,
                    ward_codes=sorted(hotspot.ward_codes),
                    sectors=sorted(hotspot.sectors),
                    window_days=window_days,
                    total_complaints=hotspot.total_complaints,
                    critical_complaints=hotspot.critical_complaints,
                    population=hotspot.population,
                    mean_intensity=hotspot.mean_intensity,
                    peak_intensity=hotspot.peak_intensity,
                    peak_severity=hotspot.peak_severity,
                    intensity_z_score=hotspot.intensity_z_score,
                    tier=hotspot.tier,
                    window_start=window_start,
                    window_end=window_end,
                )
            )
        self.session.flush()
        return len(hotspots)

    def list_hotspots(
        self,
        *,
        tier: Optional[str] = None,
        district: Optional[str] = None,
        window_days: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Hotspot]:
        statement = select(Hotspot).order_by(Hotspot.mean_intensity.desc())
        if tier:
            statement = statement.where(Hotspot.tier == tier)
        if district:
            statement = statement.where(Hotspot.district == district)
        if window_days is not None:
            statement = statement.where(Hotspot.window_days == window_days)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    # --- trends -------------------------------------------------------------
    def replace_trends(self, trends: Sequence) -> int:
        """Upsert trends per (ward, sector, window)."""
        existing = {
            (row.ward_code, row.sector, row.window_days): row
            for row in self.session.scalars(select(Trend)).all()
        }
        for result in trends:
            key = (result.ward_code, result.sector, result.window_days)
            row = existing.get(key)
            if row is None:
                row = Trend(ward_code=result.ward_code, sector=result.sector,
                            window_days=result.window_days)
                self.session.add(row)
                existing[key] = row
            row.direction = result.direction
            row.district = _district_for(self.session, result.ward_code)
            row.first_count = result.first_count
            row.last_count = result.last_count
            row.sample_count = result.sample_count
            row.pct_change = result.pct_change
            row.slope_per_window = result.slope_per_window
            row.momentum = result.momentum
            row.volatility = result.volatility
            row.series = list(result.series)
        self.session.flush()
        return len(trends)

    def list_trends(
        self,
        *,
        direction: Optional[str] = None,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        window_days: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[Trend]:
        statement = select(Trend).order_by(Trend.pct_change.desc())
        if direction:
            statement = statement.where(Trend.direction == direction)
        if ward_code:
            statement = statement.where(Trend.ward_code == ward_code)
        if sector:
            statement = statement.where(Trend.sector == sector)
        if window_days is not None:
            statement = statement.where(Trend.window_days == window_days)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    # --- risks --------------------------------------------------------------
    def replace_risks(self, signals: Sequence, *, run_id: Optional[int] = None) -> int:
        """Upsert risks by their stable code, preserving any review status.

        Re-running detection must not reset a risk an officer has already
        acknowledged, so ``status`` is only written when the row is new.
        """
        existing = {
            row.risk_code: row
            for row in self.session.scalars(select(EmergingRisk)).all()
        }
        for signal in signals:
            from app.services.risk_service import risk_code_for

            code = risk_code_for(signal.risk_type, signal.ward_code, signal.sector)
            row = existing.get(code)
            is_new = row is None
            if is_new:
                row = EmergingRisk(risk_code=code, status=RiskStatus.OPEN.value)
                self.session.add(row)
                existing[code] = row
            row.risk_type = signal.risk_type
            row.rule = signal.rule
            row.ward_code = signal.ward_code
            row.district = signal.district
            row.sector = signal.sector
            row.severity = signal.severity
            row.confidence = signal.confidence
            row.title = signal.title
            row.description = signal.description
            row.evidence = signal.evidence
            row.run_id = run_id or row.run_id
            if is_new:
                row.detected_at = utcnow()
        self.session.flush()
        return len(signals)

    def list_risks(
        self,
        *,
        risk_type: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        ward_code: Optional[str] = None,
        district: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[EmergingRisk]:
        order = {
            "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3,
        }
        statement = select(EmergingRisk)
        if risk_type:
            statement = statement.where(EmergingRisk.risk_type == risk_type)
        if severity:
            statement = statement.where(EmergingRisk.severity == severity)
        if status:
            statement = statement.where(EmergingRisk.status == status)
        if ward_code:
            statement = statement.where(EmergingRisk.ward_code == ward_code)
        if district:
            statement = statement.where(EmergingRisk.district == district)
        rows = list(self.session.scalars(statement).all())
        rows.sort(key=lambda row: (order.get(row.severity, 9), -row.confidence))
        return rows[:limit] if limit else rows

    def set_risk_status(self, risk_code: str, status: str) -> Optional[EmergingRisk]:
        """Update a risk's review status, or return ``None`` if it is unknown."""
        row = self.session.scalar(
            select(EmergingRisk).where(EmergingRisk.risk_code == risk_code)
        )
        if row is None:
            return None
        row.status = status
        self.session.flush()
        return row

    # --- runs ---------------------------------------------------------------
    def start_run(self, *, kind: str = "analysis", window_days: int = 30) -> IntelligenceRun:
        run = IntelligenceRun(kind=kind, window_days=window_days, run_at=utcnow())
        self.session.add(run)
        self.session.flush()
        return run

    def finish_run(self, run: IntelligenceRun, **counts: Any) -> IntelligenceRun:
        for field, value in counts.items():
            setattr(run, field, value)
        self.session.flush()
        return run

    def list_runs(self, *, limit: int = 20) -> list[IntelligenceRun]:
        return list(
            self.session.scalars(
                select(IntelligenceRun).order_by(IntelligenceRun.run_at.desc()).limit(limit)
            ).all()
        )

    def counts(self) -> dict[str, int]:
        return {
            "ward_locations": self.session.scalar(select(func.count(WardLocation.id))) or 0,
            "demand_windows": self.session.scalar(select(func.count(DemandWindow.id))) or 0,
            "gap_snapshots": self.session.scalar(select(func.count(GapSnapshot.id))) or 0,
            "project_snapshots": self.session.scalar(select(func.count(ProjectSnapshot.id))) or 0,
            "hotspots": self.session.scalar(select(func.count(Hotspot.id))) or 0,
            "trends": self.session.scalar(select(func.count(Trend.id))) or 0,
            "emerging_risks": self.session.scalar(select(func.count(EmergingRisk.id))) or 0,
            "runs": self.session.scalar(select(func.count(IntelligenceRun.id))) or 0,
        }

    def open_risk_count(self) -> int:
        return self.session.scalar(
            select(func.count(EmergingRisk.id)).where(EmergingRisk.status == RiskStatus.OPEN.value)
        ) or 0


def _observed_bounds(row: dict) -> tuple[datetime, datetime]:
    """Window bounds for a mesh demand row, defaulting to a trailing window."""
    from app.services.mesh_client import parse_observed_bounds

    return parse_observed_bounds(row)


def _project_state_changed(previous: ProjectSnapshot, row: dict) -> bool:
    """Whether a mesh project row differs from its latest stored snapshot.

    Spend and status are the fields that drive the delivery-side risk rules, so
    those lead the comparison; identity and dates are included because a revised
    completion date is itself a change worth recording.
    """
    for field, incoming in (
        ("status", row.get("status") or "PLANNED"),
        ("sector", row.get("sector") or "UNKNOWN"),
        ("title", row.get("title")),
        ("budget_lakhs", float(row.get("budget_lakhs") or 0.0)),
        ("spent_lakhs", float(row.get("spent_lakhs") or 0.0)),
        ("delay_days", int(row.get("delay_days") or 0)),
        ("sanctioned_on", _as_datetime(row.get("sanctioned_on"))),
        ("expected_completion_on", _as_datetime(row.get("expected_completion_on"))),
    ):
        stored = getattr(previous, field)
        if isinstance(stored, datetime):
            stored = naive_utc(stored)
        if stored != incoming:
            return True
    return False


def _as_datetime(value: Any) -> Optional[datetime]:
    from app.services.mesh_client import _parse_dt

    return _parse_dt(value)


def _district_for(session: Session, ward_code: str) -> Optional[str]:
    """Look up a ward's district, so snapshots carry it without a join."""
    ward = session.scalar(select(WardLocation).where(WardLocation.ward_code == ward_code))
    return ward.district if ward else None