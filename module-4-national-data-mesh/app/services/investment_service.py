"""Investment analytics: delivery, absorption and stall detection.

The question this answers is "is the money that was sanctioned actually
producing capacity?" - which is what turns a gap list into an actionable
investment plan rather than a wish list.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from app.core.enums import ProjectStatus
from app.core.utils import clamp, mean, safe_div

# A project past this share of its sanctioned cost with this little elapsed time
# is flagged as under-spending.
ABSORPTION_STALL_THRESHOLD = 0.35


@dataclass
class InvestmentSummary:
    """Delivery health of a portfolio of projects."""

    project_count: int
    sanctioned_lakhs: float
    spent_lakhs: float
    absorption_rate: float
    completed_count: int
    delayed_count: int
    cancelled_count: int
    active_count: int
    mean_delay_days: float
    stalled_projects: list[dict]

    def as_dict(self) -> dict:
        return {
            "project_count": self.project_count,
            "sanctioned_lakhs": round(self.sanctioned_lakhs, 2),
            "spent_lakhs": round(self.spent_lakhs, 2),
            "absorption_rate": round(self.absorption_rate, 4),
            "completed_count": self.completed_count,
            "delayed_count": self.delayed_count,
            "cancelled_count": self.cancelled_count,
            "active_count": self.active_count,
            "mean_delay_days": round(self.mean_delay_days, 2),
            "stalled_projects": self.stalled_projects,
        }


def is_delayed(project, reference_date: Optional[date] = None) -> bool:
    """A project is delayed once its expected completion date has passed unfinished."""
    if project.status == ProjectStatus.COMPLETED.value:
        return False
    if project.status == ProjectStatus.CANCELLED.value:
        return False
    expected = project.expected_completion_on
    if expected is None:
        return int(project.delay_days or 0) > 0
    return (reference_date or date.today()) > expected


def elapsed_fraction(project, reference_date: Optional[date] = None) -> float:
    """Share of the sanctioned project period that has already passed."""
    start = project.sanctioned_on
    end = project.expected_completion_on
    if start is None or end is None or end <= start:
        return 1.0
    reference = reference_date or date.today()
    if reference <= start:
        return 0.0
    total_days = (end - start).days
    if total_days <= 0:
        return 1.0
    return clamp(safe_div((reference - start).days, total_days), 0.0, 1.0)


def is_absorption_stalled(project, reference_date: Optional[date] = None) -> bool:
    """Sanctioned but barely spent, with most of the project window gone.

    This is the classic Indian municipal failure mode: a budget line exists, so
    the ward looks funded, while nothing has actually been built.
    """
    if project.status in {ProjectStatus.COMPLETED.value, ProjectStatus.CANCELLED.value}:
        return False
    budget = float(project.budget_lakhs or 0.0)
    if budget <= 0:
        return False
    spend_ratio = safe_div(float(project.spent_lakhs or 0.0), budget)
    return spend_ratio < ABSORPTION_STALL_THRESHOLD and elapsed_fraction(project, reference_date) >= 0.5


def summarise_investment(
    projects: Sequence, *, reference_date: Optional[date] = None
) -> InvestmentSummary:
    """Summarise delivery health across a project portfolio."""
    reference = reference_date or date.today()
    total = len(projects)

    sanctioned = sum(float(p.budget_lakhs or 0.0) for p in projects)
    spent = sum(float(p.spent_lakhs or 0.0) for p in projects)

    completed = sum(1 for p in projects if p.status == ProjectStatus.COMPLETED.value)
    cancelled = sum(1 for p in projects if p.status == ProjectStatus.CANCELLED.value)
    delayed = sum(1 for p in projects if is_delayed(p, reference))
    active = sum(
        1
        for p in projects
        if p.status
        in {
            ProjectStatus.PLANNED.value,
            ProjectStatus.IN_PROGRESS.value,
            ProjectStatus.DELAYED.value,
        }
    )

    delays = [float(p.delay_days or 0) for p in projects if is_delayed(p, reference)]
    stalled = [
        {
            "project_code": p.project_code,
            "title": p.title,
            "ward_code": p.ward_code,
            "budget_lakhs": round(float(p.budget_lakhs or 0.0), 2),
            "spent_lakhs": round(float(p.spent_lakhs or 0.0), 2),
            "spend_ratio": round(
                safe_div(float(p.spent_lakhs or 0.0), float(p.budget_lakhs or 0.0)), 4
            ),
            "status": p.status,
        }
        for p in projects
        if is_absorption_stalled(p, reference)
    ]

    return InvestmentSummary(
        project_count=total,
        sanctioned_lakhs=sanctioned,
        spent_lakhs=spent,
        absorption_rate=safe_div(spent, sanctioned),
        completed_count=completed,
        delayed_count=delayed,
        cancelled_count=cancelled,
        active_count=active,
        mean_delay_days=mean(delays),
        stalled_projects=sorted(stalled, key=lambda row: -row["budget_lakhs"]),
    )


def delivery_score(summary: InvestmentSummary) -> float:
    """0-1 delivery health, used as context when interpreting a sector gap."""
    if summary.project_count == 0:
        return 1.0  # nothing sanctioned, so nothing is failing
    completion_share = safe_div(summary.completed_count, summary.project_count)
    absorption = clamp(summary.absorption_rate, 0.0, 1.0)
    delay_penalty = clamp(summary.mean_delay_days / 180.0, 0.0, 1.0)
    return round(clamp(0.4 * completion_share + 0.4 * absorption + 0.2 * (1 - delay_penalty), 0.0, 1.0), 4)