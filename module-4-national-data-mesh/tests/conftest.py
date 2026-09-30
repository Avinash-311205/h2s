"""Shared fixtures: an isolated in-memory mesh plus a seeded API client.

Tests must never touch the developer's ``national_data_mesh.db``, so every test
runs against a fresh in-memory database with the schema created on it.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Iterator

# Point the app at an in-memory database *before* app modules are imported, so a
# test run can never create a stray national_data_mesh.db in the repo.
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import (
    AssetStatus,
    AssetType,
    ConditionGrade,
    ProjectStatus,
    Sector,
)
from app.core.utils import utcnow
from app.database.base import Base
from app.database import models_registry  # noqa: F401  (registers tables)
from app.database.connection import get_db
from app.main import app
from app.models.mesh_tables import (
    CitizenDemand,
    InfrastructureAsset,
    InvestmentProject,
    Ward,
)
from app.repositories.mesh_repository import MeshRepository


@pytest.fixture()
def engine():
    """In-memory engine with a single shared connection."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def repo(db: Session) -> MeshRepository:
    return MeshRepository(db)


def make_ward(
    code: str = "CHN-01",
    *,
    name: str = "Chennai Ward 1",
    district: str = "Chennai",
    state: str = "Tamil Nadu",
    population: int = 20_000,
    households: int = 4_500,
    lat: float = 13.08,
    lon: float = 80.27,
    span: float = 0.05,
) -> Ward:
    return Ward(
        ward_code=code,
        name=name,
        district=district,
        state=state,
        zone="Metropolitan",
        centroid_latitude=lat,
        centroid_longitude=lon,
        min_latitude=lat - span / 2,
        max_latitude=lat + span / 2,
        min_longitude=lon - span / 2,
        max_longitude=lon + span / 2,
        area_km2=9.5,
        population=population,
        households=households,
        is_boundary=True,
        source="test",
    )


def make_asset(
    code: str,
    ward_code: str,
    asset_type: AssetType = AssetType.WATER_SUPPLY,
    *,
    status: AssetStatus = AssetStatus.FUNCTIONAL,
    condition: ConditionGrade = ConditionGrade.GOOD,
    units: int = 1,
    installed_year: int = 2020,
) -> InfrastructureAsset:
    return InfrastructureAsset(
        asset_code=code,
        ward_code=ward_code,
        asset_type=asset_type.value,
        status=status.value,
        condition=condition.value,
        latitude=None,
        longitude=None,
        capacity_units=units,
        installed_year=installed_year,
        source="test",
    )


def make_demand(
    ward_code: str,
    sector: Sector = Sector.WATER,
    *,
    complaints: int = 20,
    avg_severity: float = 3.0,
    critical: int = 1,
    window_days: int = 30,
) -> CitizenDemand:
    return CitizenDemand(
        ward_code=ward_code,
        sector=sector.value,
        category="WATER_SUPPLY_DISRUPTION",
        sub_category="UNCLASSIFIED",
        window_days=window_days,
        complaint_count=complaints,
        avg_severity=avg_severity,
        max_severity=min(5, int(avg_severity) + 1),
        critical_count=critical,
        languages=["ta", "en"],
        observed_from=utcnow() - timedelta(days=window_days),
        observed_to=utcnow(),
        source="test",
    )


def make_project(
    code: str,
    ward_code: str,
    sector: Sector = Sector.WATER,
    *,
    status: ProjectStatus = ProjectStatus.IN_PROGRESS,
    budget: float = 500.0,
    spent: float = 250.0,
    sanctioned_days_ago: int = 400,
    duration_months: int = 12,
    delay_days: int = 0,
) -> InvestmentProject:
    sanctioned = date.today() - timedelta(days=sanctioned_days_ago)
    return InvestmentProject(
        project_code=code,
        ward_code=ward_code,
        title=f"Project {code}",
        sector=sector.value,
        status=status.value,
        budget_lakhs=budget,
        spent_lakhs=spent,
        beneficiaries=2_000,
        sanctioned_on=sanctioned,
        expected_completion_on=sanctioned + timedelta(days=duration_months * 30),
        completed_on=None,
        delay_days=delay_days,
        source="test",
    )


@pytest.fixture()
def seeded(repo: MeshRepository, db: Session):
    """A three-ward mesh with one clear gap, one healthy ward, and projects."""
    wards = [
        make_ward("CHN-01", name="Under-served", population=30_000, households=7_000, lat=13.08, lon=80.27),
        make_ward("CHN-02", name="Well served", population=25_000, households=5_500, lat=13.13, lon=80.27),
        make_ward("CBE-01", name="Other district", district="Coimbatore", population=18_000,
                  households=4_000, lat=11.02, lon=76.96),
    ]
    for ward in wards:
        repo.upsert_ward(**{
            column.name: getattr(ward, column.name)
            for column in Ward.__table__.columns
            if column.name not in {"id", "updated_at"}
        })

    assets = [
        # CHN-01: one working asset for 30k people -> a large absence gap.
        make_asset("AST-01", "CHN-01", AssetType.WATER_SUPPLY, units=1, installed_year=2008),
        make_asset("AST-02", "CHN-01", AssetType.BOREWELL, status=AssetStatus.BROKEN,
                   condition=ConditionGrade.CRITICAL, installed_year=2004),
        # CHN-02: enough capacity to cover its population, all in good order.
        make_asset("AST-03", "CHN-02", AssetType.WATER_SUPPLY, units=25, installed_year=2023),
        make_asset("AST-04", "CHN-02", AssetType.BOREWELL, units=5, installed_year=2022),
        # CBE-01: mid-provision, with an old transformer.
        make_asset("AST-05", "CBE-01", AssetType.WATER_SUPPLY, units=2, installed_year=2010),
        make_asset("AST-06", "CBE-01", AssetType.TRANSFORMER, condition=ConditionGrade.POOR,
                   installed_year=2003),
    ]
    repo.add_assets(assets)

    demand = [
        make_demand("CHN-01", Sector.WATER, complaints=140, avg_severity=4.2, critical=12),
        make_demand("CHN-01", Sector.ELECTRICITY, complaints=60, avg_severity=3.5, critical=4),
        make_demand("CHN-02", Sector.WATER, complaints=6, avg_severity=1.6, critical=0),
        make_demand("CBE-01", Sector.WATER, complaints=45, avg_severity=3.0, critical=2),
        make_demand("CBE-01", Sector.ELECTRICITY, complaints=90, avg_severity=3.8, critical=6),
    ]
    repo.add_demand(demand)

    projects = [
        make_project("PRJ-01", "CHN-01", Sector.WATER, status=ProjectStatus.PLANNED,
                     budget=800.0, spent=10.0, sanctioned_days_ago=500),
        make_project("PRJ-02", "CHN-02", Sector.WATER, status=ProjectStatus.COMPLETED,
                     budget=600.0, spent=600.0, sanctioned_days_ago=700),
        make_project("PRJ-03", "CBE-01", Sector.WATER, status=ProjectStatus.IN_PROGRESS,
                     budget=400.0, spent=260.0, sanctioned_days_ago=300),
    ]
    repo.add_projects(projects)
    db.commit()

    return repo


@pytest.fixture()
def client(seeded, db: Session) -> Iterator[TestClient]:
    """TestClient whose requests hit the seeded in-memory database."""
    def override_get_db() -> Iterator[Session]:
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()