"""Repository behaviour: upserts, grouped reads and gap persistence."""

from __future__ import annotations

from datetime import timedelta

from app.core.enums import AssetType, GapSeverity, ProjectStatus, Sector
from app.core.utils import utcnow
from app.repositories.mesh_repository import MeshRepository
from app.services import gap_service, mesh_service
from tests.conftest import make_asset, make_demand, make_project, make_ward


def test_upsert_ward_is_idempotent(repo: MeshRepository):
    ward = make_ward("CHN-01", population=20_000)

    repo.upsert_ward(**{
        "ward_code": ward.ward_code, "name": ward.name, "district": ward.district,
        "state": ward.state, "zone": ward.zone, "centroid_latitude": ward.centroid_latitude,
        "centroid_longitude": ward.centroid_longitude, "population": ward.population,
        "households": ward.households,
    })
    repo.upsert_ward(**{
        "ward_code": "CHN-01", "name": ward.name, "district": ward.district,
        "state": ward.state, "zone": ward.zone, "centroid_latitude": ward.centroid_latitude,
        "centroid_longitude": ward.centroid_longitude, "population": 25_000,
        "households": ward.households,
    })
    repo.session.commit()

    wards = repo.list_wards()
    assert len(wards) == 1
    assert wards[0].population == 25_000


def test_ward_filters_and_district_rollup(repo: MeshRepository):
    for ward in (
        make_ward("CHN-01", district="Chennai", population=20_000),
        make_ward("CHN-02", district="Chennai", population=30_000),
        make_ward("CBE-01", district="Coimbatore", population=10_000),
    ):
        repo.upsert_ward(**{
            "ward_code": ward.ward_code, "name": ward.name, "district": ward.district,
            "state": ward.state, "centroid_latitude": ward.centroid_latitude,
            "centroid_longitude": ward.centroid_longitude, "population": ward.population,
            "households": ward.households,
        })
    repo.session.commit()

    assert len(repo.list_wards(district="Chennai")) == 2
    assert len(repo.list_wards(state="Tamil Nadu")) == 3
    assert len(repo.list_wards(district="Nowhere")) == 0

    districts = {row["district"]: row for row in repo.list_districts()}
    assert districts["Chennai"]["ward_count"] == 2
    assert districts["Chennai"]["population"] == 50_000
    assert districts["Coimbatore"]["population"] == 10_000


def test_grouped_reads_split_by_ward(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.upsert_ward(ward_code="CHN-02", name="Two", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.1, centroid_longitude=80.0, population=12_000,
                     households=2_400)
    repo.add_assets([
        make_asset("A1", "CHN-01"),
        make_asset("A2", "CHN-01", AssetType.BOREWELL),
        make_asset("A3", "CHN-02"),
    ])
    repo.add_demand([make_demand("CHN-01", complaints=10), make_demand("CHN-02", complaints=30)])
    repo.add_projects([make_project("P1", "CHN-01"), make_project("P2", "CHN-02")])
    db.commit()

    assets_by_ward = repo.assets_by_ward()
    assert len(assets_by_ward["CHN-01"]) == 2
    assert len(assets_by_ward["CHN-02"]) == 1
    assert len(repo.projects_by_ward()) == 2
    assert sum(len(rows) for rows in repo.demand_by_ward().values()) == 2


def test_sector_filter_maps_to_asset_types(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.add_assets([
        make_asset("A1", "CHN-01"),
        make_asset("A2", "CHN-01", AssetType.SCHOOL),
        make_asset("A3", "CHN-01", AssetType.BOREWELL),
    ])
    db.commit()

    water = repo.list_assets(sector=Sector.WATER.value)

    assert {asset.asset_code for asset in water} == {"A1", "A3"}


def test_demand_totals_rank_the_loudest_sector_first(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.add_demand([
        make_demand("CHN-01", Sector.WATER, complaints=50, avg_severity=4.0),
        make_demand("CHN-01", Sector.ROAD, complaints=120, avg_severity=2.0),
    ])
    db.commit()

    totals = repo.demand_totals_by_sector()

    assert totals[0]["sector"] == "ROAD"
    assert totals[0]["complaints"] == 120
    assert totals[1]["avg_severity"] == 4.0


def test_asset_breakdown_groups_by_type_and_status(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.add_assets([make_asset("A1", "CHN-01"), make_asset("A2", "CHN-01"), make_asset("A3", "CHN-01")])
    db.commit()

    rows = repo.asset_type_breakdown()

    assert len(rows) == 1
    assert rows[0]["count"] == 3


def test_replace_gaps_is_upsert_not_insert(repo: MeshRepository, db):
    ward = make_ward("CHN-01", population=20_000)
    repo.upsert_ward(**{
        "ward_code": ward.ward_code, "name": ward.name, "district": ward.district,
        "state": ward.state, "centroid_latitude": ward.centroid_latitude,
        "centroid_longitude": ward.centroid_longitude, "population": ward.population,
        "households": ward.households,
    })
    repo.add_assets([make_asset("A1", "CHN-01")])
    repo.add_demand([make_demand("CHN-01", complaints=100, avg_severity=4.5, critical=5)])
    db.commit()

    first = gap_service.compute_gap(
        ward_code="CHN-01", sector=Sector.WATER.value, population=20_000,
        demand_rows=repo.list_demand(ward_code="CHN-01"), assets=repo.list_assets(),
        projects=[], reference_year=2025, demand_reference=100,
    )
    repo.replace_gaps([first])
    db.commit()

    # A second run with better provision must update the same row, not add one.
    repo.add_assets([make_asset("A2", "CHN-01", units=30)])
    db.commit()
    second = gap_service.compute_gap(
        ward_code="CHN-01", sector=Sector.WATER.value, population=20_000,
        demand_rows=repo.list_demand(ward_code="CHN-01"), assets=repo.list_assets(),
        projects=[], reference_year=2025, demand_reference=100,
    )
    repo.replace_gaps([second])
    db.commit()

    gaps = repo.list_gaps()
    assert len(gaps) == 1
    assert gaps[0].gap_score == second.gap_score
    # Coverage is now ample, but 100 loud complaints keep the ward in MODERATE.
    assert gaps[0].severity == GapSeverity.MODERATE.value
    assert second.gap_score < first.gap_score


def test_gap_filters_and_sorting(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    results = [
        gap_service.compute_gap(
            ward_code="CHN-01", sector=sector, population=10_000,
            demand_rows=[make_demand("CHN-01", complaints=60 if sector == "WATER" else 5)],
            assets=[] if sector == "WATER" else [make_asset("A1", "CHN-01", units=50)],
            projects=[], reference_year=2025, demand_reference=60,
        )
        for sector in ("WATER", "ROAD")
    ]
    repo.replace_gaps(results)
    db.commit()

    water_only = repo.list_gaps(sector=Sector.WATER.value)
    assert len(water_only) == 1

    # WATER scores ~67 and ROAD ~45, so a 60 floor keeps only the worse ward.
    min_score = repo.list_gaps(min_score=60.0)
    assert [gap.sector for gap in min_score] == ["WATER"]

    limited = repo.list_gaps(limit=1)
    assert len(limited) == 1
    assert limited[0].gap_score >= min_score[0].gap_score


def test_catalogue_upsert_records_lineage_and_quality(repo: MeshRepository, db):
    wards = [make_ward("CHN-01")]
    verdict = mesh_service.assess_wards(wards)

    repo.upsert_data_product(
        verdict,
        product_key="gis.primary",
        name="GIS primary dataset",
        description=mesh_service.PRODUCT_DESCRIPTIONS["GIS"],
    )
    # Second refresh must update the same product.
    repo.upsert_data_product(
        verdict,
        product_key="gis.primary",
        name="GIS primary dataset",
        description=mesh_service.PRODUCT_DESCRIPTIONS["GIS"],
    )
    db.commit()

    products = repo.list_data_products()
    assert len(products) == 1
    assert products[0].upstream_sources
    assert products[0].quality_status == verdict.status
    assert (utcnow() - products[0].generated_at.replace(tzinfo=utcnow().tzinfo)) < timedelta(minutes=5)


def test_counts_cover_every_table(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    db.commit()

    counts = repo.counts()
    assert set(counts) == {"wards", "assets", "projects", "demand", "census", "gaps", "data_products"}
    assert counts["wards"] == 1
    assert counts["assets"] == 0


def test_project_filters(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.add_projects([
        make_project("P1", "CHN-01", Sector.WATER),
        make_project("P2", "CHN-01", Sector.ROAD, status=ProjectStatus.COMPLETED),
    ])
    db.commit()

    assert len(repo.list_projects(sector=Sector.WATER.value)) == 1
    assert len(repo.list_projects(status=ProjectStatus.COMPLETED.value)) == 1
    assert len(repo.list_projects(ward_code="ZZZ")) == 0
    assert len(repo.list_projects(limit=1)) == 1


def test_list_assets_respects_limit_and_status(repo: MeshRepository, db):
    repo.upsert_ward(ward_code="CHN-01", name="One", district="Chennai", state="Tamil Nadu",
                     centroid_latitude=13.0, centroid_longitude=80.0, population=10_000,
                     households=2_000)
    repo.add_assets([
        make_asset("A1", "CHN-01"),
        make_asset("A2", "CHN-01"),
        make_asset("A3", "CHN-01"),
    ])
    db.commit()

    assert len(repo.list_assets(limit=2)) == 2
    assert len(repo.list_assets(status="FUNCTIONAL")) == 3
    assert len(repo.list_assets(status="BROKEN")) == 0