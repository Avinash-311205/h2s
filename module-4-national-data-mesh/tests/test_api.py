"""HTTP contract for Module 4.

These tests exercise the routes end-to-end through the real app, so they catch
serialisation and dependency-wiring mistakes that unit tests on services cannot.
"""

from __future__ import annotations

import pytest

from app.core.enums import GapSeverity, Sector
from app.services import capacity_service


def test_health_reports_ok_when_wards_exist(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_ready"] is True
    assert body["counts"]["wards"] == 3
    assert body["counts"]["assets"] == 6
    assert "GIS" in body["domains"]


def test_health_is_degraded_without_wards(client, db):
    from app.models.mesh_tables import Ward

    db.query(Ward).delete()
    db.commit()

    body = client.get("/api/v1/health").json()

    assert body["status"] == "degraded"
    assert body["database_ready"] is False


def test_root_lists_the_endpoint_map(client):
    body = client.get("/").json()

    assert body["service"] == "national-data-mesh"
    assert body["endpoints"]["gaps"] == "/api/v1/gaps"


def test_wards_endpoint_returns_geo_fields(client):
    response = client.get("/api/v1/wards")

    assert response.status_code == 200
    wards = response.json()
    assert len(wards) == 3
    assert {ward["ward_code"] for ward in wards} == {"CHN-01", "CHN-02", "CBE-01"}
    by_code = {ward["ward_code"]: ward for ward in wards}
    assert by_code["CHN-01"]["district"] == "Chennai"
    assert by_code["CBE-01"]["state"] == "Tamil Nadu"
    # Ordered by ward_code, so CBE-01 comes first.
    assert [ward["ward_code"] for ward in wards] == sorted(by_code)


def test_wards_filter_by_district(client):
    wards = client.get("/api/v1/wards", params={"district": "Coimbatore"}).json()

    assert len(wards) == 1
    assert wards[0]["ward_code"] == "CBE-01"


def test_districts_rollup(client):
    rows = client.get("/api/v1/districts").json()

    by_name = {row["district"]: row for row in rows}
    assert by_name["Chennai"]["ward_count"] == 2
    assert by_name["Chennai"]["population"] == 55_000


def test_locate_inside_a_ward_is_confident(client):
    response = client.post("/api/v1/locate", json={"latitude": 13.081, "longitude": 80.271})

    assert response.status_code == 200
    body = response.json()
    assert body["ward_code"] == "CHN-01"
    assert body["method"] == "bounding_box"
    assert body["confident"] is True


def test_locate_far_away_is_unassigned(client):
    body = client.post("/api/v1/locate", json={"latitude": 25.0, "longitude": 75.0}).json()

    assert body["ward_code"] is None
    assert body["confident"] is False


def test_locate_rejects_out_of_range_coordinates(client):
    response = client.post("/api/v1/locate", json={"latitude": 999.0, "longitude": 80.27})

    assert response.status_code == 422


def test_assets_endpoint_flags_degraded_assets(client):
    assets = client.get("/api/v1/assets").json()
    by_code = {asset["asset_code"]: asset for asset in assets}

    assert by_code["AST-02"]["degraded"] is True  # broken, installed 2004
    assert by_code["AST-03"]["degraded"] is False  # new and functional


def test_assets_filter_by_sector_maps_to_asset_types(client):
    water = client.get("/api/v1/assets", params={"sector": Sector.WATER.value}).json()

    assert {asset["asset_code"] for asset in water} == {"AST-01", "AST-02", "AST-03", "AST-04", "AST-05"}


def test_asset_breakdown_groups_by_status(client):
    rows = client.get("/api/v1/assets/breakdown").json()["rows"]

    broken = next(row for row in rows if row["status"] == "BROKEN")
    assert broken["count"] == 1


def test_demand_totals(client):
    rows = client.get("/api/v1/demand/totals").json()

    assert rows[0]["sector"] == "WATER"
    assert rows[0]["complaints"] == 191


def test_capacity_targets_expose_the_scoring_assumptions(client):
    body = client.get("/api/v1/capacity-targets").json()

    assert body["asset_population_capacity"]["WATER_SUPPLY"] == 1500
    assert body["sector_asset_types"]["HEALTHCARE"] == ["HEALTH_CLINIC"]
    assert "UNKNOWN" not in body["sector_asset_types"]


def test_quality_endpoint_scores_every_domain_live(client):
    rows = client.get("/api/v1/quality").json()
    by_domain = {row["domain"]: row for row in rows}

    assert set(by_domain) == {"GIS", "INFRASTRUCTURE", "INVESTMENT", "CITIZEN"}
    assert by_domain["GIS"]["record_count"] == 3
    assert 0.0 <= by_domain["INFRASTRUCTURE"]["quality_score"] <= 1.0


def test_analyse_persists_gaps_and_ranks_worst_first(client):
    response = client.post("/api/v1/gaps/analyse", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["ward_count"] == 3
    assert body["persisted"] is True
    assert body["gap_count"] == 3 * len(capacity_service.all_sectors())
    scores = [gap["gap_score"] for gap in body["top_gaps"]]
    assert scores == sorted(scores, reverse=True)
    assert body["by_severity"]
    assert sum(body["by_severity"].values()) == body["gap_count"]


def test_analyse_can_be_restricted_to_one_sector(client):
    body = client.post("/api/v1/gaps/analyse", json={"sectors": ["WATER"]}).json()

    assert body["sector_count"] == 1
    assert {gap["sector"] for gap in body["top_gaps"]} == {"WATER"}


def test_analyse_rejects_unknown_sector(client):
    response = client.post("/api/v1/gaps/analyse", json={"sectors": ["NOT_A_SECTOR"]})

    assert response.status_code == 422


def test_analyse_can_skip_persistence(client):
    body = client.post("/api/v1/gaps/analyse", json={"persist": False}).json()

    assert body["persisted"] is False
    assert client.get("/api/v1/gaps").json() == []


def test_analyse_without_persistence_still_ranks(client):
    body = client.post("/api/v1/gaps/analyse", json={"persist": False}).json()

    worst = body["top_gaps"][0]
    assert worst["ward_code"] == "CHN-01"
    assert worst["sector"] == "WATER"
    # CHN-01 has a broken borewell and critical complaints, so repair comes first.
    assert worst["recommended_action"] == "URGENT_REPAIR"
    assert "not functional" in worst["rationale"]


def test_analyse_conflicts_when_the_mesh_is_empty(client, db):
    from app.models.mesh_tables import Ward

    db.query(Ward).delete()
    db.commit()

    response = client.post("/api/v1/gaps/analyse", json={})

    assert response.status_code == 409
    assert "load GIS wards" in response.json()["detail"]


def test_persisted_gaps_are_listable_with_filters(client):
    client.post("/api/v1/gaps/analyse", json={})

    water_gaps = client.get("/api/v1/gaps", params={"sector": "WATER"}).json()
    assert len(water_gaps) == 3
    assert {gap["ward_code"] for gap in water_gaps} == {"CHN-01", "CHN-02", "CBE-01"}

    critical = client.get("/api/v1/gaps", params={"severity": GapSeverity.CRITICAL.value}).json()
    assert all(gap["severity"] == "CRITICAL" for gap in critical)

    limited = client.get("/api/v1/gaps", params={"limit": 2}).json()
    assert len(limited) == 2


def test_stored_gap_exposes_components_and_evidence(client):
    client.post("/api/v1/gaps/analyse", json={})

    worst = client.get("/api/v1/gaps", params={"ward_code": "CHN-01", "sector": "WATER"}).json()[0]

    assert set(worst["components"]) == {"demand", "absence", "quality"}
    # One working asset serves 5% of 30,000 people.
    assert worst["components"]["absence"] == pytest.approx(0.95, abs=0.01)
    # Reference is the busiest *water* ward: CHN-01 itself, with 140.
    assert worst["evidence"]["sector_demand_reference"] == 140
    assert worst["evidence"]["absence_measurable"] is True
    assert worst["evidence"]["broken_asset_codes"] == ["AST-02"]
    assert worst["computed_at"] is not None
    assert worst["functional_asset_count"] == 1


def test_well_served_ward_has_no_gap(client):
    client.post("/api/v1/gaps/analyse", json={})

    gaps = client.get("/api/v1/gaps", params={"ward_code": "CHN-02", "sector": "WATER"}).json()

    assert len(gaps) == 1
    assert gaps[0]["severity"] == GapSeverity.LOW.value
    assert gaps[0]["complaints"] == 6


def test_investment_projects_report_absorption(client):
    projects = client.get("/api/v1/investment/projects").json()
    by_code = {project["project_code"]: project for project in projects}

    assert by_code["PRJ-02"]["absorption_rate"] == 1.0
    assert by_code["PRJ-01"]["absorption_rate"] == 0.0125
    assert by_code["PRJ-01"]["status"] == "PLANNED"


def test_investment_summary_reports_delivery_health(client):
    body = client.get("/api/v1/investment/summary").json()

    assert body["project_count"] == 3
    assert body["sanctioned_lakhs"] == 1800.0
    assert body["completed_count"] == 1
    assert 0.0 <= body["delivery_score"] <= 1.0


def test_investment_summary_filters_by_sector(client):
    body = client.get("/api/v1/investment/summary", params={"sector": "ROAD"}).json()

    assert body["project_count"] == 0
    assert body["delivery_score"] == 1.0


def test_catalogue_lists_products_with_lineage_after_a_run(client):
    client.post("/api/v1/gaps/analyse", json={})

    products = client.get("/api/v1/catalog").json()
    by_domain = {product["domain"]: product for product in products}

    assert set(by_domain) == {"GIS", "INFRASTRUCTURE", "INVESTMENT", "CITIZEN"}
    assert by_domain["CITIZEN"]["upstream_sources"]
    # Cadence comes from the same map the scorer uses.
    assert by_domain["CITIZEN"]["refresh_interval_days"] == 7
    assert by_domain["GIS"]["record_count"] == 3
    assert by_domain["GIS"]["refresh_interval_days"] == 90


def test_gap_run_is_idempotent(client):
    first = client.post("/api/v1/gaps/analyse", json={}).json()
    second = client.post("/api/v1/gaps/analyse", json={}).json()

    assert first["gap_count"] == second["gap_count"]
    assert len(client.get("/api/v1/gaps").json()) == first["gap_count"]