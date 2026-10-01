"""Persistence for scores, history and runs.

The important behaviour here is the ordering in :meth:`replace_scores`. History
is written *before* the current table is cleared, and the whole thing happens in
one transaction. If any part fails the previous ranking survives intact, so a
reader never sees an empty table that a crashed recompute left behind.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.enums import RunStatus
from app.core.utils import as_utc, utcnow
from app.models.priority_tables import PriorityHistory, PriorityRun, PriorityScore

logger = logging.getLogger(__name__)


class PriorityRepository:
    """All database access for Module 6."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- runs -------------------------------------------------------------

    def start_run(self, trigger: str = "manual", source: Optional[str] = None) -> PriorityRun:
        run = PriorityRun(status=RunStatus.RUNNING.value, trigger=trigger, intelligence_source=source)
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def finish_run(
        self, run: PriorityRun, status: str, hotspots_scored: int = 0, error: str | None = None
    ) -> None:
        """Close a run out, recording success or the failure that ended it."""
        run.status = status
        run.hotspots_scored = hotspots_scored
        run.error = error
        run.finished_at = utcnow()
        if run.started_at:
            started = as_utc(run.started_at)
            run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        self.session.commit()

    def recent_runs(self, limit: int = 10) -> list[PriorityRun]:
        rows = self.session.scalars(
            select(PriorityRun).order_by(PriorityRun.started_at.desc()).limit(limit)
        )
        return list(rows)

    def run_count(self) -> int:
        return len(list(self.session.scalars(select(PriorityRun))))

    def latest_successful_run(self) -> Optional[PriorityRun]:
        return self.session.scalars(
            select(PriorityRun)
            .where(PriorityRun.status == RunStatus.SUCCESS.value)
            .order_by(PriorityRun.finished_at.desc())
            .limit(1)
        ).first()

    # ---- scores -----------------------------------------------------------

    def replace_scores(
        self, rows: list[dict[str, Any]], run_id: int | None, source: str | None
    ) -> None:
        """Archive the current ranking, then install the new one atomically.

        An archived row is stamped with the run that *produced* that ranking, so
        history stays traceable to the run it came from. Rows that predate run
        tracking fall back to the run doing the archiving - which is known -
        rather than a placeholder zero that would collide on the unique
        constraint and silently drop history rows.
        """
        existing = list(self.session.scalars(select(PriorityScore)))
        if existing:
            recorded_at = utcnow()
            self.session.add_all(
                [
                    PriorityHistory(
                        hotspot_code=row.hotspot_code,
                        run_id=row.run_id if row.run_id is not None else (run_id or 0),
                        rank=row.rank,
                        score=row.score,
                        band=row.band,
                        snapshot={
                            "district": row.district,
                            "factors": row.factors,
                            "evidence": row.evidence,
                            "dominant_sector": row.dominant_sector,
                            "recommended_cost_lakhs": row.recommended_cost_lakhs,
                        },
                        recorded_at=recorded_at,
                    )
                    for row in existing
                ]
            )
            self.session.execute(delete(PriorityScore))
            self.session.flush()

        self.session.add_all(
            [
                PriorityScore(
                    hotspot_code=row["hotspot_code"],
                    district=row["district"],
                    rank=row["rank"],
                    centroid_latitude=row["centroid_latitude"],
                    centroid_longitude=row["centroid_longitude"],
                    score=row["score"],
                    band=row["band"],
                    factors=row["factors"],
                    evidence=row["evidence"],
                    total_complaints=row.get("total_complaints", 0),
                    critical_complaints=row.get("critical_complaints", 0),
                    population=row.get("population", 0),
                    ward_codes=row.get("ward_codes", []),
                    sectors=row.get("sectors", []),
                    dominant_sector=row.get("dominant_sector"),
                    recommendation=row.get("recommendation"),
                    recommended_cost_lakhs=row.get("recommended_cost_lakhs", 0.0),
                    unmeasured_factors=row.get("unmeasured_factors", []),
                    intelligence_source=source,
                    window_days=row.get("window_days", 30),
                    run_id=run_id,
                )
                for row in rows
            ]
        )
        self.session.commit()

    def scored_rows(self) -> list[PriorityScore]:
        return list(self.session.scalars(select(PriorityScore).order_by(PriorityScore.rank)))

    def count_scores(self) -> int:
        return len(list(self.session.scalars(select(PriorityScore))))

    def get_score(self, hotspot_code: str) -> Optional[PriorityScore]:
        return self.session.scalars(
            select(PriorityScore).where(PriorityScore.hotspot_code == hotspot_code)
        ).first()

    # ---- history ----------------------------------------------------------

    def movement_for(self, hotspot_code: str) -> dict[str, Any]:
        """Compare a hotspot's current score with its previous recorded one.

        Returns the previous score, the change, and the rank movement. Rank
        movement is reported separately from score movement because a score can
        hold steady while everything around it shifts - which for a policymaker
        is still a change in relative standing.
        """
        current = self.get_score(hotspot_code)
        if current is None:
            return {"previous_score": None, "score_change": None, "rank_change": None}
        previous = self.session.scalars(
            select(PriorityHistory)
            .where(PriorityHistory.hotspot_code == hotspot_code)
            .order_by(PriorityHistory.recorded_at.desc())
            .limit(1)
        ).first()
        if previous is None:
            return {"previous_score": None, "score_change": None, "rank_change": None}
        return {
            "previous_score": previous.score,
            "score_change": round(current.score - previous.score, 1),
            "rank_change": previous.rank - current.rank,
        }

    def history_for(self, hotspot_code: str, limit: int = 20) -> list[PriorityHistory]:
        return list(
            self.session.scalars(
                select(PriorityHistory)
                .where(PriorityHistory.hotspot_code == hotspot_code)
                .order_by(PriorityHistory.recorded_at.desc())
                .limit(limit)
            )
        )

    def history_count(self) -> int:
        return len(list(self.session.scalars(select(PriorityHistory))))


__all__ = ["PriorityRepository"]