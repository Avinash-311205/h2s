"""Emerging risk detection: rule-based signals that something is turning.

Each rule here answers a question a district officer would otherwise have to
answer by hand, and each one records the numbers that triggered it. Rules are
deliberately *not* a learned model: with this little data, a transparent
threshold that a reviewer can dispute beats a score nobody can explain.

The five rules:

``SPIKE``
    The recent window is several times the previous one - but only above an
    absolute floor, so a jump from 1 complaint to 3 does not raise an alarm.
``NEW_CATEGORY``
    A ward-sector that was silent before and is not any more. Zero-to-nonzero
    is the clearest emerging signal there is.
``DETERIORATING_WITH_SPEND``
    Complaints are rising in a ward that already has active projects and money
    committed. This is the expensive failure: the ward is not neglected, it is
    being served and still deteriorating.
``STALLED_ABSORPTION``
    A project past half its window with almost none of its budget spent.
``CRITICAL_CONCENTRATION``
    A gap sitting in the CRITICAL band while complaints are still rising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.core.config import settings
from app.core.enums import (
    ACTIVE_PROJECT_STATUSES,
    ProjectStatus,
    RiskSeverity,
    RiskType,
    TrendDirection,
)
from app.core.utils import clamp, percent_change, safe_div

# Confidence reflects how much evidence a rule needs before it is trusted: a
# spike seen over many windows is firmer ground than a single two-window jump.
_SPIKE_BASE_CONFIDENCE = 0.6
_CRITICAL_BASE_CONFIDENCE = 0.7


@dataclass
class RiskSignal:
    """A detected risk, ready to persist."""

    risk_type: str
    rule: str
    ward_code: Optional[str]
    district: Optional[str]
    sector: Optional[str]
    severity: str
    confidence: float
    title: str
    description: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "risk_type": self.risk_type,
            "rule": self.rule,
            "ward_code": self.ward_code,
            "district": self.district,
            "sector": self.sector,
            "severity": self.severity,
            "confidence": round(self.confidence, 4),
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
        }


def risk_code_for(risk_type: str, ward_code: Optional[str], sector: Optional[str]) -> str:
    """Stable identifier, so re-running detection updates rather than duplicates."""
    return f"{risk_type}:{ward_code or 'CITY'}:{sector or 'ALL'}"


# --- individual rules --------------------------------------------------------
def detect_spike(
    ward_code: str,
    district: Optional[str],
    sector: str,
    previous: int,
    recent: int,
    *,
    ratio_threshold: Optional[float] = None,
    min_complaints: Optional[int] = None,
) -> Optional[RiskSignal]:
    """Complaints jumping several-fold between consecutive windows."""
    ratio = ratio_threshold if ratio_threshold is not None else settings.spike_ratio
    floor = min_complaints if min_complaints is not None else settings.spike_min_complaints

    if recent < floor or previous <= 0:
        return None
    observed_ratio = safe_div(recent, previous)
    if observed_ratio < ratio:
        return None

    # Severity follows how far past the threshold it went, not just that it did.
    if observed_ratio >= ratio * 3:
        severity = RiskSeverity.CRITICAL
    elif observed_ratio >= ratio * 2:
        severity = RiskSeverity.HIGH
    else:
        severity = RiskSeverity.MEDIUM

    confidence = clamp(
        _SPIKE_BASE_CONFIDENCE + 0.1 * min(observed_ratio / ratio - 1.0, 1.0), 0.0, 0.95
    )
    label = sector.replace("_", " ").title()
    return RiskSignal(
        risk_type=RiskType.SPIKE.value,
        rule=f"recent >= {ratio}x previous and recent >= {floor}",
        ward_code=ward_code,
        district=district,
        sector=sector,
        severity=severity,
        confidence=confidence,
        title=f"{label} complaints spiking in {ward_code}",
        description=(
            f"{label} complaints rose from {previous} to {recent} between consecutive "
            f"windows ({observed_ratio:.1f}x), which is beyond the {ratio:.0f}x alert "
            f"threshold."
        ),
        evidence={
            "previous_count": previous,
            "recent_count": recent,
            "observed_ratio": round(observed_ratio, 3),
            "threshold_ratio": ratio,
            "min_complaints": floor,
        },
    )


def detect_new_category(
    ward_code: str,
    district: Optional[str],
    sector: str,
    previous: int,
    recent: int,
    *,
    min_complaints: int = 3,
) -> Optional[RiskSignal]:
    """A ward-sector that was silent and now is not."""
    if previous > 0 or recent < min_complaints:
        return None

    label = sector.replace("_", " ").title()
    return RiskSignal(
        risk_type=RiskType.NEW_CATEGORY.value,
        rule=f"previous == 0 and recent >= {min_complaints}",
        ward_code=ward_code,
        district=district,
        sector=sector,
        severity=RiskSeverity.MEDIUM,
        # A first sighting is weaker evidence than a long run, hence mid confidence.
        confidence=0.5,
        title=f"New {label.lower()} problem emerging in {ward_code}",
        description=(
            f"No {label.lower()} complaints were recorded in the earlier window, "
            f"but {recent} arrived in the most recent one."
        ),
        evidence={
            "previous_count": 0,
            "recent_count": recent,
            "min_complaints": min_complaints,
        },
    )


def detect_deteriorating_with_spend(
    ward_code: str,
    district: Optional[str],
    sector: str,
    *,
    previous: int,
    recent: int,
    active_projects: int,
    committed_lakhs: float,
    spent_lakhs: float,
    threshold_pct: Optional[float] = None,
) -> Optional[RiskSignal]:
    """Complaints rising despite active projects and committed money.

    The expensive kind of failure: this ward is not being ignored, it is being
    served and still getting worse, which means the intervention is not the
    right one rather than that there is not enough of it.
    """
    threshold = (
        threshold_pct if threshold_pct is not None else settings.trend_change_threshold_pct
    )
    change = percent_change(previous, recent)
    if active_projects <= 0 or change < threshold or recent <= previous:
        return None

    absorption = safe_div(spent_lakhs, committed_lakhs)
    label = sector.replace("_", " ").title()
    if absorption < settings.stall_spend_ratio:
        severity = RiskSeverity.CRITICAL
    elif change >= threshold * 3:
        severity = RiskSeverity.HIGH
    else:
        severity = RiskSeverity.MEDIUM

    return RiskSignal(
        risk_type=RiskType.DETERIORATING_WITH_SPEND.value,
        rule="worsening trend while active projects exist",
        ward_code=ward_code,
        district=district,
        sector=sector,
        severity=severity,
        confidence=0.75,
        title=f"{label} worsening in {ward_code} despite active projects",
        description=(
            f"{label} complaints rose {change:.0f}% ({previous} to {recent}) while "
            f"{active_projects} project(s) worth Rs {committed_lakhs:.1f} lakh were "
            f"active, of which only {absorption:.0%} of the budget was spent."
        ),
        evidence={
            "previous_count": previous,
            "recent_count": recent,
            "pct_change": change,
            "active_project_count": active_projects,
            "committed_lakhs": round(committed_lakhs, 2),
            "spent_lakhs": round(spent_lakhs, 2),
            "absorption_rate": round(absorption, 4),
            "threshold_pct": threshold,
        },
    )


def detect_stalled_absorption(
    project,
    *,
    reference_date: Optional[date] = None,
    elapsed_fraction: float = 0.0,
) -> Optional[RiskSignal]:
    """A sanctioned project past half its window with almost nothing spent."""
    if project.status not in ACTIVE_PROJECT_STATUSES:
        return None
    if elapsed_fraction < settings.stall_elapsed_fraction:
        return None

    budget = float(project.budget_lakhs or 0.0)
    if budget <= 0:
        return None

    spend_ratio = safe_div(float(project.spent_lakhs or 0.0), budget)
    if spend_ratio >= settings.stall_spend_ratio:
        return None

    shortfall = float(budget - float(project.spent_lakhs or 0.0))
    sector_label = str(project.sector or "GENERAL").replace("_", " ").title()
    return RiskSignal(
        risk_type=RiskType.STALLED_ABSORPTION.value,
        rule=(
            f"elapsed >= {settings.stall_elapsed_fraction:.0%} of the project window "
            f"and spent < {settings.stall_spend_ratio:.0%} of budget"
        ),
        ward_code=project.ward_code,
        district=None,
        sector=project.sector,
        severity=RiskSeverity.HIGH if shortfall > 500 else RiskSeverity.MEDIUM,
        confidence=0.8,
        title=f"Stalled project {project.project_code} in {project.ward_code}",
        description=(
            f"{project.title} is {elapsed_fraction:.0%} through its sanctioned window "
            f"but has spent only {spend_ratio:.0%} of Rs {budget:.1f} lakh "
            f"(Rs {shortfall:.1f} lakh unspent)."
        ),
        evidence={
            "project_code": project.project_code,
            "ward_code": project.ward_code,
            "status": project.status,
            "budget_lakhs": round(budget, 2),
            "spent_lakhs": round(float(project.spent_lakhs or 0.0), 2),
            "spend_ratio": round(spend_ratio, 4),
            "elapsed_fraction": round(elapsed_fraction, 4),
            "unspent_lakhs": round(shortfall, 2),
        },
    )


def detect_critical_concentration(
    ward_code: str,
    district: Optional[str],
    sector: str,
    *,
    gap_score: float,
    gap_severity: str,
    previous: int,
    recent: int,
) -> Optional[RiskSignal]:
    """A CRITICAL-band gap that is still getting worse."""
    if gap_severity != RiskSeverity.CRITICAL.value or recent <= previous:
        return None

    label = sector.replace("_", " ").title()
    change = percent_change(previous, recent)
    return RiskSignal(
        risk_type=RiskType.CRITICAL_CONCENTRATION.value,
        rule="gap band CRITICAL and complaints still rising",
        ward_code=ward_code,
        district=district,
        sector=sector,
        severity=RiskSeverity.CRITICAL,
        confidence=_CRITICAL_BASE_CONFIDENCE,
        title=f"{label} critical and worsening in {ward_code}",
        description=(
            f"The {label.lower()} gap is in the CRITICAL band (score {gap_score:.1f}) "
            f"while complaints moved from {previous} to {recent} ({change:+.0f}%)."
        ),
        evidence={
            "gap_score": round(gap_score, 2),
            "gap_severity": gap_severity,
            "previous_count": previous,
            "recent_count": recent,
            "pct_change": change,
        },
    )


# --- orchestration -----------------------------------------------------------
def detect_risks(
    *,
    demand_rows: Sequence,
    gap_snapshots: Sequence,
    projects: Sequence,
    project_elapsed: Optional[dict[str, float]] = None,
    sectors: Optional[Sequence[str]] = None,
    reference_date: Optional[date] = None,
) -> list[RiskSignal]:
    """Run every rule and return the risks, most severe first.

    ``demand_rows`` needs at least two windows per ward-sector; ward-sectors with
    a single window have no trend to analyse and are skipped rather than being
    judged on one observation.
    """
    signals: list[RiskSignal] = []

    demand_index: dict[tuple[str, str], list] = {}
    districts: dict[str, str] = {}
    for row in demand_rows:
        demand_index.setdefault((row.ward_code, row.sector), []).append(row)
        if row.district:
            districts[row.ward_code] = row.district

    for (ward_code, sector), rows in demand_index.items():
        if sectors and sector not in sectors:
            continue
        ordered = sorted(rows, key=lambda row: row.window_end)
        if len(ordered) < 2:
            continue

        previous_row, recent_row = ordered[-2], ordered[-1]
        previous = int(previous_row.complaint_count or 0)
        recent = int(recent_row.complaint_count or 0)
        district = districts.get(ward_code)

        for signal in (
            detect_spike(ward_code, district, sector, previous, recent),
            detect_new_category(ward_code, district, sector, previous, recent),
        ):
            if signal:
                signals.append(signal)

    # --- gap-side rules, joined to the most recent demand pair --------------
    latest_gaps: dict[tuple[str, str], object] = {}
    for snapshot in gap_snapshots:
        key = (snapshot.ward_code, snapshot.sector)
        current = latest_gaps.get(key)
        if current is None or snapshot.snapshot_at > current.snapshot_at:
            latest_gaps[key] = snapshot

    for key, snapshot in latest_gaps.items():
        ward_code, sector = key
        rows = demand_index.get(key)
        # A gap with only one demand window behind it has no direction to judge,
        # so there is nothing to say about whether it is "still rising".
        if not rows or len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: row.window_end)
        previous = int(ordered[-2].complaint_count or 0)
        recent = int(ordered[-1].complaint_count or 0)
        district = districts.get(ward_code)

        signal = detect_critical_concentration(
            ward_code,
            district,
            sector,
            gap_score=float(snapshot.gap_score or 0.0),
            gap_severity=snapshot.severity,
            previous=previous,
            recent=recent,
        )
        if signal:
            signals.append(signal)

    # --- investment-side rules ----------------------------------------------
    active_by_ward_sector: dict[tuple[str, str], list] = {}
    for project in projects:
        if project.status in ACTIVE_PROJECT_STATUSES:
            active_by_ward_sector.setdefault((project.ward_code, project.sector), []).append(project)

    for (ward_code, sector), ward_projects in active_by_ward_sector.items():
        rows = demand_index.get((ward_code, sector))
        if not rows or len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda row: row.window_end)
        previous = int(ordered[-2].complaint_count or 0)
        recent = int(ordered[-1].complaint_count or 0)

        committed = sum(float(project.budget_lakhs or 0.0) for project in ward_projects)
        spent = sum(float(project.spent_lakhs or 0.0) for project in ward_projects)
        signal = detect_deteriorating_with_spend(
            ward_code,
            districts.get(ward_code),
            sector,
            previous=previous,
            recent=recent,
            active_projects=len(ward_projects),
            committed_lakhs=committed,
            spent_lakhs=spent,
        )
        if signal:
            signals.append(signal)

    elapsed = project_elapsed or {}
    for project in projects:
        signal = detect_stalled_absorption(
            project,
            reference_date=reference_date,
            elapsed_fraction=float(elapsed.get(project.project_code, 0.0)),
        )
        if signal:
            signals.append(signal)

    severity_order = {
        RiskSeverity.CRITICAL.value: 0,
        RiskSeverity.HIGH.value: 1,
        RiskSeverity.MEDIUM.value: 2,
        RiskSeverity.LOW.value: 3,
    }
    signals.sort(key=lambda signal: (severity_order[signal.severity], -signal.confidence))
    return signals


def summarise_risks(signals: Sequence[RiskSignal]) -> dict[str, int]:
    """Count risks by type and severity for a run summary."""
    by_type = {risk.value: 0 for risk in RiskType}
    by_severity = {severity.value: 0 for severity in RiskSeverity}
    for signal in signals:
        by_type[signal.risk_type] = by_type.get(signal.risk_type, 0) + 1
        by_severity[signal.severity] += 1
    return {"by_type": by_type, "by_severity": by_severity}


def confirmatory_signals(signals: Sequence[RiskSignal]) -> list[RiskSignal]:
    """Risks that two independent rules agree on.

    A spike *and* a deteriorating-with-spend flag on the same ward-sector is much
    stronger evidence than either alone, which is why it is worth surfacing.
    """
    by_ward_sector: dict[tuple[str, str], set[str]] = {}
    for signal in signals:
        key = (signal.ward_code or "", signal.sector or "")
        by_ward_sector.setdefault(key, set()).add(signal.risk_type)
    confirmed = {
        key for key, types in by_ward_sector.items()
        if len(types) > 1
    }
    return [signal for signal in signals if (signal.ward_code or "", signal.sector or "") in confirmed]