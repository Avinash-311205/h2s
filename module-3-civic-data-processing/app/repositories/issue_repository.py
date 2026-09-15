"""Issue group repository."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.enums import IssueGroupStatus
from app.core.geo import bounding_box, valid_coordinates
from app.core.logging import get_logger
from app.core.utils import to_utc, utcnow
from app.models.issue_group import IssueGroup

logger = get_logger(__name__)

ISSUE_GROUP_PREFIX = "ISSUE"


class IssueRepository:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    def get(self, issue_group_id: str) -> Optional[IssueGroup]:
        return self.db.query(IssueGroup).filter(IssueGroup.issue_group_id == issue_group_id).first()

    def next_issue_group_id(self) -> str:
        """Sequential, human-readable identifier (``ISSUE-00001``)."""
        count = self.db.query(func.count(IssueGroup.issue_group_id)).scalar() or 0
        # Guard against collisions after deletions or concurrent inserts.
        for offset in range(1, 1000):
            candidate = f"{ISSUE_GROUP_PREFIX}-{count + offset:05d}"
            if self.get(candidate) is None:
                return candidate
        return f"{ISSUE_GROUP_PREFIX}-{utcnow().strftime('%Y%m%d%H%M%S%f')}"

    def find_candidates(
        self,
        category: str,
        sub_category: str,
        since: Optional[datetime] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        radius_meters: Optional[float] = None,
        district: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[IssueGroup]:
        """Cheap, index-friendly prefilter; fine-grained matching happens in the strategy."""
        query = self.db.query(IssueGroup).filter(
            IssueGroup.category == category,
            IssueGroup.sub_category == sub_category,
            IssueGroup.status.in_([IssueGroupStatus.OPEN.value, IssueGroupStatus.IN_REVIEW.value]),
        )

        if since is not None:
            query = query.filter(IssueGroup.last_reported_at >= since)

        if valid_coordinates(latitude, longitude) and radius_meters:
            min_lat, max_lat, min_lon, max_lon = bounding_box(latitude, longitude, radius_meters)
            query = query.filter(
                IssueGroup.latitude.isnot(None),
                IssueGroup.longitude.isnot(None),
                IssueGroup.latitude.between(min_lat, max_lat),
                IssueGroup.longitude.between(min_lon, max_lon),
            )
        elif district or state:
            # Records without coordinates fall back to administrative matching.
            if district:
                query = query.filter(IssueGroup.district == district)
            elif state:
                query = query.filter(IssueGroup.state == state)

        return query.order_by(IssueGroup.last_reported_at.desc()).limit(limit).all()

    # ------------------------------------------------------------------
    def create(
        self,
        issue_group_id: str,
        category: str,
        sub_category: str,
        latitude: Optional[float],
        longitude: Optional[float],
        district: Optional[str],
        state: Optional[str],
        country: Optional[str],
        description: Optional[str],
        normalized_description: Optional[str],
        representative_request_id: str,
        severity: Optional[float],
        occurred_at: Optional[datetime],
    ) -> IssueGroup:
        now = utcnow()
        report_time = to_utc(occurred_at) if occurred_at else now
        group = IssueGroup(
            issue_group_id=issue_group_id,
            category=category,
            sub_category=sub_category,
            latitude=latitude,
            longitude=longitude,
            district=district,
            state=state,
            country=country,
            status=IssueGroupStatus.OPEN.value,
            report_count=1,
            severity_max=severity,
            severity_avg=severity,
            first_reported_at=report_time,
            last_reported_at=report_time,
            representative_request_id=representative_request_id,
            representative_description=description,
            normalized_description=normalized_description,
            created_at=now,
            updated_at=now,
        )
        self.db.add(group)
        self.db.flush()
        return group

    def add_report(
        self,
        group: IssueGroup,
        severity: Optional[float],
        occurred_at: Optional[datetime],
        latitude: Optional[float],
        longitude: Optional[float],
        district: Optional[str],
        state: Optional[str],
        country: Optional[str],
    ) -> IssueGroup:
        """Attach a new (duplicate) report to an existing issue group."""
        report_time = to_utc(occurred_at) if occurred_at else utcnow()
        previous_count = group.report_count or 0

        group.report_count = previous_count + 1
        if severity is not None:
            previous_total = (group.severity_avg or 0.0) * previous_count
            group.severity_avg = round((previous_total + severity) / group.report_count, 3)
            group.severity_max = max(group.severity_max or severity, severity)

        # Values loaded from the database may be naive; normalize before comparing.
        first_reported = to_utc(group.first_reported_at) if group.first_reported_at else None
        last_reported = to_utc(group.last_reported_at) if group.last_reported_at else None
        if first_reported is None or report_time < first_reported:
            group.first_reported_at = report_time
        if last_reported is None or report_time > last_reported:
            group.last_reported_at = report_time

        # Fill in geography the first report did not have.
        if not valid_coordinates(group.latitude, group.longitude) and valid_coordinates(latitude, longitude):
            group.latitude, group.longitude = latitude, longitude
        group.district = group.district or district
        group.state = group.state or state
        group.country = group.country or country
        group.updated_at = utcnow()

        self.db.flush()
        return group

    def remove_report(self, issue_group_id: str) -> None:
        """Detach a report (used by reprocessing); deletes empty groups."""
        group = self.get(issue_group_id)
        if group is None:
            return
        group.report_count = max(0, (group.report_count or 0) - 1)
        group.updated_at = utcnow()
        if group.report_count == 0:
            self.db.delete(group)
        self.db.flush()

    def count(self) -> int:
        return self.db.query(func.count(IssueGroup.issue_group_id)).scalar() or 0
