"""Deduplication: same issue + nearby location groups; everything else does not."""

from __future__ import annotations

from datetime import timedelta

from app.core.utils import utcnow
from app.repositories.issue_repository import IssueRepository
from app.services.deduplication_service import (
    DeduplicationCandidate,
    DeduplicationService,
    EmbeddingDeduplicationStrategy,
    RuleBasedDeduplicationStrategy,
)


def build_service(db_session) -> DeduplicationService:
    return DeduplicationService(IssueRepository(db_session))


def candidate(
    request_id: str,
    description: str,
    latitude: float = 12.9249,
    longitude: float = 80.1000,
    category: str = "ROAD",
    sub_category: str = "DAMAGED_ROAD",
    district: str = "Chengalpattu",
    state: str = "Tamil Nadu",
) -> DeduplicationCandidate:
    return DeduplicationCandidate(
        request_id=request_id,
        category=category,
        sub_category=sub_category,
        latitude=latitude,
        longitude=longitude,
        district=district,
        state=state,
        country="India",
        description=description,
        severity=4,
        occurred_at=utcnow(),
    )


def test_first_report_creates_a_new_issue_group(db_session):
    service = build_service(db_session)

    assignment = service.assign(candidate("REQ-1", "Road has many potholes"))
    db_session.commit()

    assert assignment.group is not None
    assert assignment.is_duplicate is False
    assert assignment.group.report_count == 1
    assert assignment.group.issue_group_id.startswith("ISSUE-")


def test_same_issue_and_nearby_location_join_the_same_group(db_session):
    service = build_service(db_session)

    first = service.assign(candidate("REQ-1", "Road has many potholes"))
    db_session.commit()
    # ~70m away, same category and near-identical description.
    second = service.assign(
        candidate("REQ-2", "Road has many potholes near the bus stop", latitude=12.9255, longitude=80.1005)
    )
    db_session.commit()

    assert second.is_duplicate is True
    assert second.issue_group_id == first.issue_group_id
    assert second.group.report_count == 2
    assert second.match is not None
    assert second.match.distance_meters is not None


def test_bridge_reports_from_different_citizens_group_together(db_session):
    service = build_service(db_session)

    texts = ["Bridge damaged", "Bridge is broken", "Dangerous bridge", "Bridge needs repair"]
    groups = []
    for index, text in enumerate(texts):
        assignment = service.assign(
            candidate(
                f"REQ-{100 + index}",
                text,
                category="TRANSPORT",
                sub_category="BRIDGE_DAMAGE",
            )
        )
        db_session.commit()
        groups.append(assignment)

    assert groups[0].is_duplicate is False
    assert len({group.issue_group_id for group in groups}) == 1
    assert groups[-1].group.report_count == 4


def test_different_category_creates_a_different_group(db_session):
    service = build_service(db_session)

    road = service.assign(candidate("REQ-1", "Road has many potholes"))
    water = service.assign(
        candidate("REQ-2", "no drinking water", category="WATER", sub_category="DRINKING_WATER")
    )
    db_session.commit()

    assert road.issue_group_id != water.issue_group_id


def test_same_issue_far_away_creates_a_different_group(db_session):
    service = build_service(db_session)

    near = service.assign(candidate("REQ-1", "Road has many potholes"))
    far = service.assign(
        candidate("REQ-2", "Road has many potholes", latitude=13.5000, longitude=78.0000)
    )
    db_session.commit()

    assert near.issue_group_id != far.issue_group_id


def test_different_problem_in_the_same_place_is_not_a_duplicate(db_session):
    service = build_service(db_session)

    potholes = service.assign(candidate("REQ-1", "Road has many potholes"))
    # Same category and sub-category but a clearly different complaint.
    other = service.assign(candidate("REQ-2", "Street light not working and traffic signal broken"))
    db_session.commit()

    assert potholes.issue_group_id != other.issue_group_id


def test_records_without_coordinates_match_by_district(db_session):
    service = build_service(db_session)

    first = service.assign(candidate("REQ-1", "Road has many potholes", latitude=None, longitude=None))
    second = service.assign(candidate("REQ-2", "Road is full of potholes", latitude=None, longitude=None))
    db_session.commit()

    assert first.issue_group_id == second.issue_group_id


def test_cross_language_descriptions_need_the_embedding_strategy(db_session):
    """Rule-based matching is lexical, so cross-language duplicates do not group.

    This is the documented seam for the future embedding/ML strategy: the
    category, sub-category and location already match, only the text differs.
    """
    service = build_service(db_session)

    english = service.assign(candidate("REQ-1", "Road has many potholes"))
    tamil = service.assign(candidate("REQ-2", "சாலையில் நிறைய பள்ளங்கள் உள்ளன"))
    db_session.commit()

    assert english.issue_group_id != tamil.issue_group_id


def test_non_actionable_category_is_not_grouped(db_session):
    service = build_service(db_session)

    assignment = service.assign(candidate("REQ-1", "unclear complaint", category="UNKNOWN", sub_category="UNCLASSIFIED"))

    assert assignment.group is None
    assert assignment.issue_group_id is None
    assert assignment.skipped_reason


def test_time_window_is_enforced(db_session):
    service = build_service(db_session)

    first = service.assign(candidate("REQ-1", "Road has many potholes"))
    first.group.last_reported_at = utcnow() - timedelta(days=400)
    db_session.commit()

    second = service.assign(candidate("REQ-2", "Road has many potholes"))
    db_session.commit()

    assert first.issue_group_id != second.issue_group_id


def test_embedding_strategy_is_a_documented_stub(db_session):
    strategy = EmbeddingDeduplicationStrategy()
    service = DeduplicationService(IssueRepository(db_session), strategy=strategy)

    assignment = service.assign(candidate("REQ-1", "Road has many potholes"))

    # The stub never matches, so the record still gets its own group (no data loss).
    assert assignment.is_duplicate is False
    assert assignment.group is not None


def test_rule_based_strategy_scores_are_explainable(db_session):
    service = build_service(db_session)

    first = service.assign(candidate("REQ-1", "Road has many potholes"))
    db_session.commit()
    second = service.assign(candidate("REQ-2", "Road has many potholes"))

    assert second.match is not None
    assert second.match.strategy == "rule_based"
    assert second.match.reasons
    assert 0.0 <= second.match.score <= 1.0


def test_radius_is_configurable():
    strict = RuleBasedDeduplicationStrategy(radius_meters=10, min_score=0.1, text_threshold=0.0)
    assert strict.radius_meters == 10
