"""Duplicate detection and issue grouping.

Citizens describe the same infrastructure problem in different words:

    "Bridge damaged" / "Bridge is broken" / "Dangerous bridge" / "Bridge needs repair"

Those reports must be linked, **never deleted**. Each processed record keeps its
own row and points at an ``IssueGroup`` that aggregates the underlying issue.

Matching is pluggable through :class:`DeduplicationStrategy`. The shipped
implementation is deterministic and rule-based (category + sub-category +
geographic proximity + time window + normalized description similarity), while
:class:`EmbeddingDeduplicationStrategy` documents the seam where an ML /
embedding model can be dropped in later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.core.enums import ALLOWED_CATEGORIES
from app.core.geo import haversine_meters, valid_coordinates
from app.core.logging import get_logger
from app.core.utils import normalize_description, to_utc, utcnow
from app.models.issue_group import IssueGroup
from app.repositories.issue_repository import IssueRepository
from app.services.normalization_service import UNCLASSIFIED

logger = get_logger(__name__)


@dataclass
class DeduplicationCandidate:
    request_id: str
    category: str
    sub_category: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    district: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[float] = None
    occurred_at: Optional[datetime] = None

    @property
    def normalized_description(self) -> Optional[str]:
        if not self.description:
            return None
        return normalize_description(self.description)

    @property
    def groupable(self) -> bool:
        """Only actionable, fully classified records take part in grouping."""
        return self.category in ALLOWED_CATEGORIES and self.sub_category != UNCLASSIFIED


@dataclass
class DeduplicationMatch:
    group: IssueGroup
    score: float
    geo_score: float = 0.0
    text_similarity: float = 0.0
    time_score: float = 0.0
    distance_meters: Optional[float] = None
    strategy: str = "rule_based"
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_group_id": self.group.issue_group_id,
            "score": round(self.score, 3),
            "geo_score": round(self.geo_score, 3),
            "text_similarity": round(self.text_similarity, 3),
            "time_score": round(self.time_score, 3),
            "distance_meters": round(self.distance_meters, 1) if self.distance_meters is not None else None,
            "strategy": self.strategy,
            "reasons": self.reasons,
        }


@dataclass
class IssueAssignment:
    group: Optional[IssueGroup]
    is_duplicate: bool = False
    match: Optional[DeduplicationMatch] = None
    skipped_reason: Optional[str] = None
    # True when reprocessing a record that is already in the matched group, so
    # the aggregate counters must not be touched again.
    unchanged: bool = False

    @property
    def issue_group_id(self) -> Optional[str]:
        return self.group.issue_group_id if self.group else None


class DeduplicationStrategy(ABC):
    """Contract for duplicate-detection strategies."""

    name: str = "abstract"

    @abstractmethod
    def find_match(
        self,
        candidate: DeduplicationCandidate,
        groups: Sequence[IssueGroup],
    ) -> Optional[DeduplicationMatch]:
        """Return the best matching issue group, or ``None``."""
        raise NotImplementedError


class RuleBasedDeduplicationStrategy(DeduplicationStrategy):
    """Deterministic matching on category, sub-category, distance, time and text."""

    name = "rule_based"

    def __init__(
        self,
        radius_meters: Optional[float] = None,
        time_window_hours: Optional[int] = None,
        text_threshold: Optional[float] = None,
        min_score: Optional[float] = None,
        weights: Optional[dict[str, float]] = None,
    ):
        self.radius_meters = radius_meters or settings.dedup_radius_meters
        self.time_window_hours = time_window_hours or settings.dedup_time_window_hours
        self.text_threshold = (
            text_threshold if text_threshold is not None else settings.dedup_description_similarity_threshold
        )
        self.min_score = min_score if min_score is not None else settings.dedup_min_match_score
        self.weights = weights or {"geo": 0.4, "text": 0.4, "time": 0.2}

    def find_match(
        self,
        candidate: DeduplicationCandidate,
        groups: Sequence[IssueGroup],
    ) -> Optional[DeduplicationMatch]:
        best: Optional[DeduplicationMatch] = None

        for group in groups:
            if group.category != candidate.category or group.sub_category != candidate.sub_category:
                continue

            geo_score, distance, geo_reason = self._geo_score(candidate, group)
            if geo_score is None:
                continue

            text_score = self._text_score(candidate.normalized_description, self._group_text(group))
            if (
                candidate.normalized_description
                and self._group_text(group)
                and text_score < self.text_threshold
            ):
                # Same category, same place - but clearly a different problem.
                continue

            time_score = self._time_score(candidate.occurred_at, group.last_reported_at or group.created_at)

            score = (
                self.weights["geo"] * geo_score
                + self.weights["text"] * text_score
                + self.weights["time"] * time_score
            )
            if score < self.min_score:
                continue

            reasons = ["category+sub_category match", geo_reason]
            if text_score >= self.text_threshold:
                reasons.append(f"description similarity {text_score:.2f}")
            if time_score >= 0.5:
                reasons.append("inside duplicate time window")

            match = DeduplicationMatch(
                group=group,
                score=score,
                geo_score=geo_score,
                text_similarity=text_score,
                time_score=time_score,
                distance_meters=distance,
                strategy=self.name,
                reasons=reasons,
            )
            if best is None or match.score > best.score:
                best = match

        return best

    # ------------------------------------------------------------------
    def _geo_score(
        self, candidate: DeduplicationCandidate, group: IssueGroup
    ) -> tuple[Optional[float], Optional[float], str]:
        candidate_has_point = valid_coordinates(candidate.latitude, candidate.longitude)
        group_has_point = valid_coordinates(group.latitude, group.longitude)

        if candidate_has_point and group_has_point:
            distance = haversine_meters(
                candidate.latitude, candidate.longitude, group.latitude, group.longitude
            )
            if distance is None:
                return None, None, ""
            if distance > self.radius_meters:
                return None, distance, "outside proximity radius"
            score = max(0.0, 1.0 - (distance / self.radius_meters))
            return score, distance, f"within {self.radius_meters:.0f}m of the issue location"

        # Fall back to administrative matching when coordinates are missing.
        if candidate.district and group.district and candidate.district.casefold() == group.district.casefold():
            return 0.6, None, f"same district ({group.district})"
        if candidate.state and group.state and candidate.state.casefold() == group.state.casefold():
            return 0.4, None, f"same state ({group.state})"
        if not candidate.district and not candidate.state:
            # Neither record carries geography: allow matching on category+text alone.
            return 0.3, None, "no geographic information on either report"
        return None, None, ""

    def _text_score(self, left: Optional[str], right: Optional[str]) -> float:
        if not left or not right:
            # Missing text is neutral rather than disqualifying.
            return 0.5
        if left in right or right in left:
            return 1.0

        ratio = SequenceMatcher(None, left, right).ratio()
        left_tokens, right_tokens = set(left.split()), set(right.split())
        union = left_tokens | right_tokens
        jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
        return (ratio + jaccard) / 2

    @staticmethod
    def _time_score(occurred_at: Optional[datetime], group_time: Optional[datetime]) -> float:
        if group_time is None:
            return 0.5
        # Datetimes read back from the database may be naive (SQLite) while the
        # incoming value is timezone-aware; normalize both to UTC.
        reference = to_utc(occurred_at) if occurred_at else utcnow()
        delta_hours = abs((reference - to_utc(group_time)).total_seconds()) / 3600.0
        window = settings.dedup_time_window_hours or 1
        return max(0.0, 1.0 - (delta_hours / window))

    @staticmethod
    def _group_text(group: IssueGroup) -> Optional[str]:
        return group.normalized_description or (
            normalize_description(group.representative_description)
            if group.representative_description
            else None
        )


class EmbeddingDeduplicationStrategy(DeduplicationStrategy):
    """Placeholder for semantic duplicate detection.

    Swap this in (via ``settings.dedup_strategy``) once an embedding model such
    as a multilingual sentence encoder is available. The rule-based strategy
    stays as a deterministic first pass / fallback.
    """

    name = "embedding"

    def find_match(
        self,
        candidate: DeduplicationCandidate,
        groups: Sequence[IssueGroup],
    ) -> Optional[DeduplicationMatch]:
        logger.info(
            "embedding_deduplication_not_configured",
            extra={"request_id": candidate.request_id, "candidates": len(groups)},
        )
        return None


def build_strategy(name: Optional[str] = None) -> DeduplicationStrategy:
    key = (name or settings.dedup_strategy).strip().casefold()
    if key == "embedding":
        return EmbeddingDeduplicationStrategy()
    if key != "rule_based":
        logger.warning("unknown_dedup_strategy", extra={"strategy": name})
    return RuleBasedDeduplicationStrategy()


class DeduplicationService:
    """Links a processed record to an issue group (creating one when needed)."""

    def __init__(self, repository: IssueRepository, strategy: Optional[DeduplicationStrategy] = None):
        self.repository = repository
        self.strategy = strategy or build_strategy()

    def assign(
        self,
        candidate: DeduplicationCandidate,
        preferred_group_id: Optional[str] = None,
    ) -> IssueAssignment:
        """Link ``candidate`` to an issue group.

        ``preferred_group_id`` is the group the record already belongs to
        (used by reprocessing) so that a re-matched record is left untouched
        instead of inflating ``report_count``.
        """
        if not candidate.groupable:
            if preferred_group_id:
                self.release(preferred_group_id)
            return IssueAssignment(
                group=None,
                is_duplicate=False,
                skipped_reason="category/sub_category is not actionable; the record is stored for review only",
            )

        reference_time = candidate.occurred_at or utcnow()
        since = reference_time - timedelta(hours=settings.dedup_time_window_hours)

        candidates = self.repository.find_candidates(
            category=candidate.category,
            sub_category=candidate.sub_category,
            since=since,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            radius_meters=self.strategy.radius_meters
            if isinstance(self.strategy, RuleBasedDeduplicationStrategy)
            else settings.dedup_radius_meters,
            district=candidate.district,
            state=candidate.state,
        )

        match = self.strategy.find_match(candidate, candidates)
        if match is not None and match.group is not None:
            if preferred_group_id and match.group.issue_group_id == preferred_group_id:
                logger.info(
                    "issue_group_unchanged_on_reprocess",
                    extra={"request_id": candidate.request_id, "issue_group_id": preferred_group_id},
                )
                return IssueAssignment(
                    group=match.group,
                    is_duplicate=(match.group.report_count or 0) > 1,
                    match=match,
                    unchanged=True,
                )

            if preferred_group_id:
                self.release(preferred_group_id)

            group = self.repository.add_report(
                match.group,
                severity=candidate.severity,
                occurred_at=candidate.occurred_at,
                latitude=candidate.latitude,
                longitude=candidate.longitude,
                district=candidate.district,
                state=candidate.state,
                country=candidate.country,
            )
            logger.info(
                "issue_group_matched",
                extra={
                    "request_id": candidate.request_id,
                    "issue_group_id": group.issue_group_id,
                    "score": round(match.score, 3),
                },
            )
            return IssueAssignment(group=group, is_duplicate=True, match=match)

        if preferred_group_id:
            self.release(preferred_group_id)

        group = self.repository.create(
            issue_group_id=self.repository.next_issue_group_id(),
            category=candidate.category,
            sub_category=candidate.sub_category,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            district=candidate.district,
            state=candidate.state,
            country=candidate.country,
            description=candidate.description,
            normalized_description=candidate.normalized_description,
            representative_request_id=candidate.request_id,
            severity=candidate.severity,
            occurred_at=candidate.occurred_at,
        )
        logger.info(
            "issue_group_created",
            extra={"request_id": candidate.request_id, "issue_group_id": group.issue_group_id},
        )
        return IssueAssignment(group=group, is_duplicate=False)

    def release(self, issue_group_id: Optional[str]) -> None:
        """Detach a report during reprocessing."""
        if issue_group_id:
            self.repository.remove_report(issue_group_id)
