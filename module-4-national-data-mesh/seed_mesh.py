"""Seed the national data mesh with a coherent synthetic Tamil Nadu mesh.

    python seed_mesh.py --reset

The point of this dataset is *internal consistency*, not realism: the wards are
placed on a grid with non-overlapping bounding boxes, assets are sized to the
capacity maths in `capacity_service`, and the citizens who complain most are the
ones in wards with the least provision. That makes the resulting gap rankings
checkable by hand, which a randomly generated dataset never is.

Everything is deterministic (fixed RNG seed) so demos and screenshots stay
stable across runs.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from app.core.enums import (
    AssetStatus,
    AssetType,
    ConditionGrade,
    DataDomain,
    ProjectStatus,
    Sector,
)
from app.core.utils import utcnow
from app.database.connection import SessionLocal, init_db
from app.models.mesh_tables import (
    CensusIndicator,
    CitizenDemand,
    InfrastructureAsset,
    InvestmentProject,
    Ward,
)
from app.repositories.mesh_repository import MeshRepository
from app.services import gap_service, mesh_service

RNG_SEED = 20240917

# Anchor districts across Tamil Nadu. Coordinates are real; bounding boxes are
# a synthetic but tidy grid so no two wards overlap.
DISTRICTS = [
    # (district, state, zone, lat, lon, span_lat, span_lon, ward_count)
    ("Chennai", "Tamil Nadu", "Metropolitan", 13.0827, 80.2707, 0.05, 0.05, 4),
    ("Coimbatore", "Tamil Nadu", "Western", 11.0168, 76.9558, 0.05, 0.05, 3),
    ("Madurai", "Tamil Nadu", "Southern", 9.9252, 78.1198, 0.05, 0.05, 3),
    ("Salem", "Tamil Nadu", "Western", 11.6643, 78.1460, 0.05, 0.05, 2),
    ("Tiruchirappalli", "Tamil Nadu", "Central", 10.7905, 78.7047, 0.05, 0.05, 2),
    ("Thanjavur", "Tamil Nadu", "Delta", 10.7870, 79.1378, 0.05, 0.05, 2),
]

WARD_PREFIX = {
    "Chennai": "CHN",
    "Coimbatore": "CBE",
    "Madurai": "MDU",
    "Salem": "SLM",
    "Tiruchirappalli": "TRC",
    "Thanjavur": "TJC",
}

# Base stock for a ward provisioned at 1.0. The counts are chosen so 1.0 roughly
# covers a typical ward population under `capacity_service`'s people-per-asset
# figures, which is what makes the seeded gap spread meaningful rather than
# uniformly catastrophic.
SECTOR_ASSET_MIX: dict[str, list[tuple[AssetType, int]]] = {
    # sector -> (asset type, units at provisioning 1.0)
    Sector.WATER.value: [(AssetType.WATER_SUPPLY, 12), (AssetType.BOREWELL, 6)],
    Sector.ELECTRICITY.value: [(AssetType.TRANSFORMER, 10), (AssetType.ELECTRICITY_POLE, 18)],
    Sector.ROAD.value: [(AssetType.ROAD, 6)],
    Sector.SANITATION.value: [
        (AssetType.SEWAGE_LINE, 3),
        (AssetType.DRAIN, 5),
        (AssetType.WASTE_BIN, 8),
        (AssetType.PUBLIC_TOILET, 2),
    ],
    Sector.HEALTHCARE.value: [(AssetType.HEALTH_CLINIC, 5)],
    Sector.EDUCATION.value: [(AssetType.SCHOOL, 8)],
    Sector.TRANSPORT.value: [(AssetType.BUS_STOP, 8)],
    Sector.DIGITAL_CONNECTIVITY.value: [(AssetType.WIFI_HOTSPOT, 6)],
}

PROJECT_TEMPLATES = [
    # (title, sector, budget_lakhs, months)
    ("Ward water distribution network upgrade", Sector.WATER, 850.0, 14),
    ("Borewell deepening and pump house", Sector.WATER, 320.0, 8),
    ("Transformer capacity augmentation", Sector.ELECTRICITY, 460.0, 10),
    ("Road resurfacing and storm drains", Sector.ROAD, 1200.0, 18),
    ("Sewer line and treatment augmentation", Sector.SANITATION, 980.0, 20),
    ("Ward health clinic renovation", Sector.HEALTHCARE, 240.0, 9),
    ("School toilet and drinking water block", Sector.EDUCATION, 130.0, 6),
    ("Bus depot and shelter upgrade", Sector.TRANSPORT, 610.0, 15),
    ("Public wifi and digital literacy centre", Sector.DIGITAL_CONNECTIVITY, 95.0, 7),
]

# Complaint intensity by (sector, provisioning level). The whole dataset is built
# around this table: wards that end up under-provisioned complain the most.
_COMPLAINT_WEIGHTS = {
    Sector.WATER.value: 30,
    Sector.ELECTRICITY.value: 26,
    Sector.ROAD.value: 22,
    Sector.SANITATION.value: 18,
    Sector.HEALTHCARE.value: 12,
    Sector.TRANSPORT.value: 10,
    Sector.EDUCATION.value: 8,
    Sector.DIGITAL_CONNECTIVITY.value: 6,
}

CATEGORIES = {
    Sector.WATER.value: "WATER_SUPPLY_DISRUPTION",
    Sector.ELECTRICITY.value: "ELECTRICITY_FAILURE",
    Sector.ROAD.value: "ROAD_DAMAGE",
    Sector.SANITATION.value: "GARBAGE_DISPOSAL",
    Sector.HEALTHCARE.value: "HEALTHCARE_SHORTAGE",
    Sector.EDUCATION.value: "EDUCATION_ACCESS",
    Sector.TRANSPORT.value: "PUBLIC_TRANSPORT",
    Sector.DIGITAL_CONNECTIVITY.value: "INTERNET_CONNECTIVITY",
}


def build_wards(rng: random.Random) -> list[Ward]:
    wards: list[Ward] = []
    for district, state, zone, lat, lon, span_lat, span_lon, count in DISTRICTS:
        for index in range(count):
            offset = (index - (count - 1) / 2) * span_lat
            centroid_lat = lat + offset
            half = span_lat / 2
            ward_code = f"{WARD_PREFIX[district]}-{index + 1:02d}"
            population = rng.randint(7_000, 32_000)
            wards.append(
                Ward(
                    ward_code=ward_code,
                    name=f"{district} Ward {index + 1}",
                    district=district,
                    state=state,
                    zone=zone,
                    centroid_latitude=round(centroid_lat, 6),
                    centroid_longitude=round(lon, 6),
                    min_latitude=round(centroid_lat - half, 6),
                    max_latitude=round(centroid_lat + half, 6),
                    min_longitude=round(lon - span_lon, 6),
                    max_longitude=round(lon + span_lon, 6),
                    area_km2=round(rng.uniform(4.0, 18.0), 2),
                    population=population,
                    households=int(population / rng.uniform(3.8, 4.8)),
                    is_boundary=True,
                    source="synthetic",
                )
            )
    return wards


def build_assets(rng: random.Random, wards: list[Ward]) -> list[InfrastructureAsset]:
    """Size assets so roughly a third of wards are deliberately under-provisioned.

    Provisioning level 0 gets 35% of the stock a fully covered ward would get,
    which is what produces a visible HIGH/CRITICAL spread in the gap table.
    """
    assets: list[InfrastructureAsset] = []
    counter = 0
    for ward in wards:
        # Deterministic per-ward provisioning level so the demo is reproducible.
        provisioning = rng.choice([0.35, 0.7, 1.0, 1.3])
        for sector, mix in SECTOR_ASSET_MIX.items():
            for asset_type, base_count in mix:
                count = max(1, int(round(base_count * provisioning * rng.uniform(0.8, 1.2))))
                for _ in range(count):
                    counter += 1
                    status = _sample_status(rng, provisioning)
                    installed_year = rng.randint(2004, 2024)
                    assets.append(
                        InfrastructureAsset(
                            asset_code=f"AST-{counter:05d}",
                            ward_code=ward.ward_code,
                            asset_type=asset_type.value,
                            status=status.value,
                            condition=_sample_condition(rng, status).value,
                            latitude=round(
                                ward.centroid_latitude + rng.uniform(-0.02, 0.02), 6
                            ),
                            longitude=round(
                                ward.centroid_longitude + rng.uniform(-0.02, 0.02), 6
                            ),
                            capacity_units=max(1, int(rng.uniform(1, 3))),
                            installed_year=installed_year,
                            last_maintained_on=date(installed_year, 1, 1)
                            + timedelta(days=rng.randint(30, 900)),
                            source="synthetic",
                        )
                    )
    return assets


def _sample_status(rng: random.Random, provisioning: float) -> AssetStatus:
    """Under-provisioned wards also carry a worse status mix."""
    if provisioning < 0.5:
        weights = [0.45, 0.20, 0.20, 0.15]
    elif provisioning < 1.0:
        weights = [0.70, 0.12, 0.10, 0.08]
    else:
        weights = [0.88, 0.06, 0.04, 0.02]
    return rng.choices(
        [AssetStatus.FUNCTIONAL, AssetStatus.PARTIAL, AssetStatus.BROKEN, AssetStatus.UNDER_REPAIR],
        weights=weights,
        k=1,
    )[0]


def _sample_condition(rng: random.Random, status: AssetStatus) -> ConditionGrade:
    if status is AssetStatus.BROKEN:
        return rng.choice([ConditionGrade.CRITICAL, ConditionGrade.POOR])
    if status is AssetStatus.UNDER_REPAIR:
        return rng.choice([ConditionGrade.POOR, ConditionGrade.FAIR])
    return rng.choices(
        [ConditionGrade.GOOD, ConditionGrade.FAIR, ConditionGrade.POOR],
        weights=[0.65, 0.25, 0.10],
        k=1,
    )[0]


def build_projects(rng: random.Random, wards: list[Ward]) -> list[InvestmentProject]:
    """Sanctioned projects, with a deliberate set of stalled absorbers.

    Stalled projects are what let Module 5 distinguish "money committed but not
    spent" from "no money at all".
    """
    projects: list[InvestmentProject] = []
    today = date.today()
    for ward in wards:
        for _ in range(rng.randint(1, 3)):
            title, sector, budget, months = rng.choice(PROJECT_TEMPLATES)
            sanctioned = today - timedelta(days=rng.randint(120, 900))
            expected = sanctioned + timedelta(days=months * 30)
            status = _sample_project_status(rng, today, expected)
            absorption = {
                ProjectStatus.PLANNED.value: 0.02,
                ProjectStatus.IN_PROGRESS.value: rng.uniform(0.25, 0.70),
                ProjectStatus.DELAYED.value: rng.uniform(0.10, 0.35),
                ProjectStatus.COMPLETED.value: 1.0,
                ProjectStatus.CANCELLED.value: rng.uniform(0.10, 0.40),
            }[status.value]
            delay_days = 0 if status in {ProjectStatus.COMPLETED, ProjectStatus.PLANNED} else rng.randint(0, 240)
            projects.append(
                InvestmentProject(
                    project_code=f"PRJ-{len(projects) + 1:05d}",
                    ward_code=ward.ward_code,
                    title=title,
                    sector=sector.value,
                    status=status.value,
                    budget_lakhs=budget,
                    spent_lakhs=round(budget * absorption, 2),
                    beneficiaries=rng.randint(500, 12_000),
                    sanctioned_on=sanctioned,
                    expected_completion_on=expected,
                    completed_on=expected if status is ProjectStatus.COMPLETED else None,
                    delay_days=delay_days,
                    source="synthetic",
                )
            )
    return projects


def _sample_project_status(rng: random.Random, today: date, expected: date) -> ProjectStatus:
    overdue = today > expected
    if overdue:
        return rng.choices(
            [ProjectStatus.DELAYED, ProjectStatus.IN_PROGRESS, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED],
            weights=[0.55, 0.25, 0.12, 0.08],
            k=1,
        )[0]
    return rng.choices(
        [ProjectStatus.IN_PROGRESS, ProjectStatus.PLANNED, ProjectStatus.COMPLETED],
        weights=[0.65, 0.25, 0.10],
        k=1,
    )[0]


def build_demand(rng: random.Random, wards: list[Ward], assets: list[InfrastructureAsset]) -> list[CitizenDemand]:
    """Aggregated citizen complaints, correlated with actual under-provisioning.

    Complaint volume is driven by the ward's real asset shortfall rather than a
    random draw, so the demand component of the gap score and the citizen
    dataset agree with each other.
    """
    from app.services import capacity_service

    assets_by_ward: dict[str, list] = {}
    for asset in assets:
        assets_by_ward.setdefault(asset.ward_code, []).append(asset)

    rows: list[CitizenDemand] = []
    now = utcnow()
    for ward in wards:
        for sector in _COMPLAINT_WEIGHTS:
            provision = capacity_service.summarise_provision(
                sector=sector,
                population=ward.population,
                assets=assets_by_ward.get(ward.ward_code, []),
            )
            shortfall = max(0.0, 1.0 - provision.coverage_ratio)
            broken_share = provision.degraded_asset_count / max(provision.asset_count, 1)
            intensity = shortfall * 0.7 + broken_share * 0.3
            complaints = int(
                _COMPLAINT_WEIGHTS[sector]
                * (0.25 + rng.uniform(0.0, 0.5) + 2.2 * intensity)
            )
            if complaints <= 0:
                continue
            severity_base = 2.0 + 2.0 * intensity
            avg_severity = round(min(5.0, max(1.0, severity_base + rng.uniform(-0.4, 0.6))), 2)
            # Critical complaints mean "someone is at risk", so they only appear
            # where the ward is both noisy and badly provisioned.
            critical = 0
            if intensity >= 0.55 and complaints >= 20:
                critical = max(1, int(complaints * 0.04 * intensity))
            rows.append(
                CitizenDemand(
                    ward_code=ward.ward_code,
                    sector=sector,
                    category=CATEGORIES[sector],
                    sub_category="UNCLASSIFIED",
                    window_days=30,
                    complaint_count=complaints,
                    avg_severity=avg_severity,
                    max_severity=min(5, int(avg_severity + 1)),
                    critical_count=critical,
                    languages=rng.choice([["ta"], ["ta", "en"], ["ta", "en", "hi"]]),
                    observed_from=now - timedelta(days=30),
                    observed_to=now,
                    # These rows are generated, not received from Module 2, so
                    # they must not claim otherwise. Real submissions arrive via
                    # the Module 3 consumer and are labelled by the ingest path.
                    source="synthetic",
                )
            )
    return rows


def build_census(wards: list[Ward]) -> list[CensusIndicator]:
    """Census rows aligned with the ward population snapshot."""
    rows: list[CensusIndicator] = []
    for ward in wards:
        slum_households = int(ward.households * 0.08)
        rows.append(
            CensusIndicator(
                ward_code=ward.ward_code,
                census_year=2011,
                population=ward.population,
                households=ward.households,
                slum_households=slum_households,
                literacy_rate=round(72.0 + (ward.population % 17), 2),
                avg_monthly_income=8_000 + (ward.population % 11) * 750,
                sc_st_population=int(ward.population * 0.21),
                female_population=int(ward.population * 0.49),
                source="synthetic",
            )
        )
    return rows


def seed(reset: bool = False) -> dict:
    """Load the whole mesh and run one gap analysis pass."""
    init_db()
    rng = random.Random(RNG_SEED)

    with SessionLocal() as db:
        repo = MeshRepository(db)

        if reset:
            from app.models.mesh_tables import DataProduct, GapRecord

            for model in (GapRecord, DataProduct, CitizenDemand, CensusIndicator,
                          InvestmentProject, InfrastructureAsset, Ward):
                db.query(model).delete()
            db.commit()

        wards = build_wards(rng)
        assets = build_assets(rng, wards)
        projects = build_projects(rng, wards)
        demand = build_demand(rng, wards, assets)
        census = build_census(wards)

        for ward in wards:
            repo.upsert_ward(**{
                "ward_code": ward.ward_code,
                "name": ward.name,
                "district": ward.district,
                "state": ward.state,
                "zone": ward.zone,
                "centroid_latitude": ward.centroid_latitude,
                "centroid_longitude": ward.centroid_longitude,
                "min_latitude": ward.min_latitude,
                "max_latitude": ward.max_latitude,
                "min_longitude": ward.min_longitude,
                "max_longitude": ward.max_longitude,
                "area_km2": ward.area_km2,
                "population": ward.population,
                "households": ward.households,
                "is_boundary": ward.is_boundary,
                "source": ward.source,
            })

        if not repo.list_assets():
            repo.add_assets(assets)
        if not repo.list_projects():
            repo.add_projects(projects)
        if not repo.list_demand():
            repo.add_demand(demand)
        if not db.query(CensusIndicator).count():
            repo.add_census(census)
        db.commit()

        # --- score the mesh ---------------------------------------------------
        results = gap_service.run_gap_analysis(
            wards=repo.list_wards(),
            demand_by_ward=repo.demand_by_ward(),
            assets_by_ward=repo.assets_by_ward(),
            projects_by_ward=repo.projects_by_ward(),
            reference_year=date.today().year,
        )
        repo.replace_gaps(results)

        verdicts = mesh_service.refresh_all(
            wards=repo.list_wards(),
            assets=repo.list_assets(),
            projects=repo.list_projects(),
            demand=repo.list_demand(),
        )
        for verdict in verdicts:
            repo.upsert_data_product(
                verdict,
                product_key=f"{verdict.domain.lower()}.primary",
                name=f"{verdict.domain.title()} primary dataset",
                description=mesh_service.PRODUCT_DESCRIPTIONS.get(verdict.domain, ""),
            )
        db.commit()

        counts = repo.counts()
        by_severity: dict[str, int] = {}
        for result in results:
            by_severity[result.severity] = by_severity.get(result.severity, 0) + 1

        top = [
            (r.ward_code, r.sector, round(r.gap_score, 1), r.severity, r.recommended_action)
            for r in results[:10]
        ]

    return {"counts": counts, "by_severity": by_severity, "top_gaps": top}


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Module 4 national data mesh")
    parser.add_argument("--reset", action="store_true", help="delete existing mesh rows first")
    args = parser.parse_args()

    summary = seed(reset=args.reset)
    print("Seeded counts:", summary["counts"])
    print("Gap bands:", summary["by_severity"])
    print("\nTop gaps:")
    print(f"{'ward':<8} {'sector':<22} {'score':>6} {'band':<9} action")
    for row in summary["top_gaps"]:
        print(f"{row[0]:<8} {row[1]:<22} {row[2]:>6} {row[3]:<9} {row[4]}")


if __name__ == "__main__":
    main()