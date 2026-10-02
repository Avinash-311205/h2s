"""Data access for the mesh: one repository, grouped reads for gap analysis.

Gap analysis needs each domain grouped by ward in a single pass. Doing that with
N+1 queries would be needlessly slow on a national dataset, so the grouped
readers here use explicit ``GROUP BY`` queries and return plain lists.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import DataDomain, Sector
from app.core.utils import utcnow
from app.models.mesh_tables import (
    CensusIndicator,
    CivicRecordLineage,
    CitizenDemand,
    DataProduct,
    GapRecord,
    InfrastructureAsset,
    InvestmentProject,
    Ward,
)


class MeshRepository:
    """CRUD and grouped reads over every mesh table."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- wards ---------------------------------------------------------------
    def upsert_ward(self, **fields: Any) -> Ward:
        ward_code = fields["ward_code"]
        ward = self.session.scalar(select(Ward).where(Ward.ward_code == ward_code))
        if ward is None:
            ward = Ward(**fields)
            self.session.add(ward)
        else:
            for key, value in fields.items():
                setattr(ward, key, value)
        self.session.flush()
        return ward

    def list_wards(
        self, *, district: Optional[str] = None, state: Optional[str] = None
    ) -> list[Ward]:
        statement = select(Ward).order_by(Ward.ward_code)
        if district:
            statement = statement.where(Ward.district == district)
        if state:
            statement = statement.where(Ward.state == state)
        return list(self.session.scalars(statement).all())

    def get_ward(self, ward_code: str) -> Optional[Ward]:
        return self.session.scalar(select(Ward).where(Ward.ward_code == ward_code))

    def list_districts(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Ward.district, Ward.state, func.count(Ward.id), func.sum(Ward.population))
            .group_by(Ward.district, Ward.state)
            .order_by(Ward.state, Ward.district)
        ).all()
        return [
            {
                "district": district,
                "state": state,
                "ward_count": ward_count,
                "population": int(population or 0),
            }
            for district, state, ward_count, population in rows
        ]

    # --- assets --------------------------------------------------------------
    def add_assets(self, assets: Sequence[InfrastructureAsset]) -> list[InfrastructureAsset]:
        self.session.add_all(list(assets))
        self.session.flush()
        return list(assets)

    def list_assets(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[InfrastructureAsset]:
        statement = select(InfrastructureAsset).order_by(InfrastructureAsset.asset_code)
        if ward_code:
            statement = statement.where(InfrastructureAsset.ward_code == ward_code)
        if sector:
            types = _asset_types_for(sector)
            statement = statement.where(InfrastructureAsset.asset_type.in_(types))
        if status:
            statement = statement.where(InfrastructureAsset.status == status)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    def assets_by_ward(self) -> dict[str, list[InfrastructureAsset]]:
        rows = self.session.scalars(
            select(InfrastructureAsset).order_by(InfrastructureAsset.ward_code)
        ).all()
        grouped: dict[str, list[InfrastructureAsset]] = {}
        for asset in rows:
            grouped.setdefault(asset.ward_code, []).append(asset)
        return grouped

    def asset_type_breakdown(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(
                InfrastructureAsset.asset_type,
                InfrastructureAsset.status,
                func.count(InfrastructureAsset.id),
            )
            .group_by(InfrastructureAsset.asset_type, InfrastructureAsset.status)
            .order_by(InfrastructureAsset.asset_type)
        ).all()
        return [
            {"asset_type": asset_type, "status": status, "count": count}
            for asset_type, status, count in rows
        ]

    # --- projects ------------------------------------------------------------
    def add_projects(self, projects: Sequence[InvestmentProject]) -> list[InvestmentProject]:
        self.session.add_all(list(projects))
        self.session.flush()
        return list(projects)

    def list_projects(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[InvestmentProject]:
        statement = select(InvestmentProject).order_by(InvestmentProject.project_code)
        if ward_code:
            statement = statement.where(InvestmentProject.ward_code == ward_code)
        if sector:
            statement = statement.where(InvestmentProject.sector == sector)
        if status:
            statement = statement.where(InvestmentProject.status == status)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    def projects_by_ward(self) -> dict[str, list[InvestmentProject]]:
        rows = self.session.scalars(
            select(InvestmentProject).order_by(InvestmentProject.ward_code)
        ).all()
        grouped: dict[str, list[InvestmentProject]] = {}
        for project in rows:
            grouped.setdefault(project.ward_code, []).append(project)
        return grouped

    # --- citizen demand ------------------------------------------------------
    def add_demand(self, rows: Sequence[CitizenDemand]) -> list[CitizenDemand]:
        self.session.add_all(list(rows))
        self.session.flush()
        return list(rows)

    def list_demand(
        self, *, ward_code: Optional[str] = None, sector: Optional[str] = None
    ) -> list[CitizenDemand]:
        statement = select(CitizenDemand).order_by(CitizenDemand.ward_code, CitizenDemand.sector)
        if ward_code:
            statement = statement.where(CitizenDemand.ward_code == ward_code)
        if sector:
            statement = statement.where(CitizenDemand.sector == sector)
        return list(self.session.scalars(statement).all())

    def demand_by_ward(self) -> dict[str, list[CitizenDemand]]:
        rows = self.session.scalars(
            select(CitizenDemand).order_by(CitizenDemand.ward_code)
        ).all()
        grouped: dict[str, list[CitizenDemand]] = {}
        for row in rows:
            grouped.setdefault(row.ward_code, []).append(row)
        return grouped

    def demand_totals_by_sector(self) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(
                CitizenDemand.sector,
                func.sum(CitizenDemand.complaint_count),
                func.avg(CitizenDemand.avg_severity),
            )
            .group_by(CitizenDemand.sector)
            .order_by(func.sum(CitizenDemand.complaint_count).desc())
        ).all()
        return [
            {
                "sector": sector,
                "complaints": int(total or 0),
                "avg_severity": round(float(avg or 0.0), 3),
            }
            for sector, total, avg in rows
        ]

    # --- census --------------------------------------------------------------
    def add_census(self, rows: Sequence[CensusIndicator]) -> list[CensusIndicator]:
        self.session.add_all(list(rows))
        self.session.flush()
        return list(rows)

    def census_by_ward(self, *, census_year: Optional[int] = None) -> dict[str, CensusIndicator]:
        statement = select(CensusIndicator)
        if census_year:
            statement = statement.where(CensusIndicator.census_year == census_year)
        rows = self.session.scalars(statement).all()
        return {row.ward_code: row for row in rows}

    # --- gap records ---------------------------------------------------------
    def replace_gaps(self, results: Sequence) -> list[GapRecord]:
        """Upsert computed gaps in one transaction, keyed by (ward, sector)."""
        existing = {
            (record.ward_code, record.sector): record
            for record in self.session.scalars(select(GapRecord)).all()
        }
        for result in results:
            record = existing.get((result.ward_code, result.sector))
            if record is None:
                record = GapRecord(ward_code=result.ward_code, sector=result.sector)
                self.session.add(record)
            _apply_gap(record, result)
        self.session.flush()
        return list(results)

    def list_gaps(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        severity: Optional[str] = None,
        min_score: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[GapRecord]:
        statement = select(GapRecord).order_by(GapRecord.gap_score.desc())
        if ward_code:
            statement = statement.where(GapRecord.ward_code == ward_code)
        if sector:
            statement = statement.where(GapRecord.sector == sector)
        if severity:
            statement = statement.where(GapRecord.severity == severity)
        if min_score is not None:
            statement = statement.where(GapRecord.gap_score >= min_score)
        if limit:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement).all())

    # --- data products (catalogue) -------------------------------------------
    def upsert_data_product(self, quality, product_key: str, name: str, description: str) -> DataProduct:
        from app.services.mesh_service import lineage_for, refresh_interval_for

        product = self.session.scalar(
            select(DataProduct).where(DataProduct.product_key == product_key)
        )
        if product is None:
            product = DataProduct(product_key=product_key)
            self.session.add(product)
        product.name = name
        product.description = description
        product.domain = quality.domain
        product.upstream_sources = lineage_for(quality.domain)
        product.record_count = quality.record_count
        product.completeness = quality.completeness
        product.validity = quality.validity
        product.timeliness = quality.timeliness
        product.quality_status = quality.status
        product.quality_score = quality.quality_score
        # Taken from the same map the scorer uses, so the advertised cadence can
        # never disagree with the cadence timeliness was measured against.
        product.refresh_interval_days = refresh_interval_for(quality.domain)
        product.generated_at = utcnow()
        self.session.flush()
        return product

    def list_data_products(self, *, domain: Optional[str] = None) -> list[DataProduct]:
        statement = select(DataProduct).order_by(DataProduct.domain)
        if domain:
            statement = statement.where(DataProduct.domain == domain)
        return list(self.session.scalars(statement).all())

    def counts(self) -> dict[str, int]:
        """Row counts per mesh table, for the health endpoint."""
        return {
            "wards": self.session.scalar(select(func.count(Ward.id))) or 0,
            "assets": self.session.scalar(select(func.count(InfrastructureAsset.id))) or 0,
            "projects": self.session.scalar(select(func.count(InvestmentProject.id))) or 0,
            "demand": self.session.scalar(select(func.count(CitizenDemand.id))) or 0,
            "census": self.session.scalar(select(func.count(CensusIndicator.id))) or 0,
            "gaps": self.session.scalar(select(func.count(GapRecord.id))) or 0,
            "data_products": self.session.scalar(select(func.count(DataProduct.id))) or 0,
        }


    # --- civic record lineage (Module 3 -> Module 4) -------------------------
    def get_lineage(self, request_id: str) -> Optional[CivicRecordLineage]:
        """Look up one ingested request by its Module 1 correlation id."""
        return self.session.scalar(
            select(CivicRecordLineage).where(CivicRecordLineage.request_id == request_id)
        )

    def get_lineage_by_source_event(self, source_event_id: str) -> Optional[CivicRecordLineage]:
        return self.session.scalar(
            select(CivicRecordLineage).where(
                CivicRecordLineage.source_event_id == source_event_id
            )
        )

    def upsert_lineage(self, *, request_id: str, **fields: Any) -> CivicRecordLineage:
        """Insert or refresh the lineage row for ``request_id``.

        Idempotent by construction: a redelivered event updates the existing row
        instead of appending a second one, so ``request_id`` stays unique and the
        caller can use it to decide whether to re-apply the demand aggregate.
        """
        lineage = self.get_lineage(request_id)
        if lineage is None:
            lineage = CivicRecordLineage(request_id=request_id, **fields)
            self.session.add(lineage)
        else:
            for key, value in fields.items():
                setattr(lineage, key, value)
        self.session.flush()
        return lineage

    def list_lineage(
        self,
        *,
        ward_code: Optional[str] = None,
        sector: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> list[CivicRecordLineage]:
        """List individual submissions, for auditing an aggregate or gap score."""
        statement = select(CivicRecordLineage).order_by(CivicRecordLineage.received_at.desc())
        if ward_code:
            statement = statement.where(CivicRecordLineage.ward_code == ward_code)
        if sector:
            statement = statement.where(CivicRecordLineage.sector == sector)
        if category:
            statement = statement.where(CivicRecordLineage.category == category)
        return list(self.session.scalars(statement.limit(limit)).all())

    def list_lineage_for_request_group(
        self, issue_group_id: str, limit: int = 500
    ) -> list[CivicRecordLineage]:
        """Every submission that belongs to one Module 3 issue group."""
        return list(
            self.session.scalars(
                select(CivicRecordLineage)
                .where(CivicRecordLineage.issue_group_id == issue_group_id)
                .order_by(CivicRecordLineage.received_at)
                .limit(limit)
            ).all()
        )

    def get_ward_by_name(
        self, ward_name: str, *, district: Optional[str] = None
    ) -> Optional[Ward]:
        """Resolve Module 3's free-text ward label to a mesh ward_code."""
        statement = select(Ward).where(Ward.name == ward_name)
        if district:
            statement = statement.where(Ward.district == district)
        return self.session.scalars(statement.limit(2)).first()

    def apply_demand_increment(
        self,
        *,
        ward_code: str,
        sector: str,
        category: str,
        sub_category: str,
        severity: int,
        language: Optional[str],
        observed_at: Any,
        window_days: int = 30,
    ) -> Optional[CitizenDemand]:
        """Fold one accepted request into the ``citizen_demand`` aggregate.

        The row is keyed on ``(ward_code, sector, category, window_days)``. The
        caller is responsible for idempotency (see ``upsert_lineage``), because
        ``complaint_count`` is a running total and cannot be replay-safe on its
        own -- this method always increments.
        """
        existing = self.session.scalar(
            select(CitizenDemand).where(
                CitizenDemand.ward_code == ward_code,
                CitizenDemand.sector == sector,
                CitizenDemand.category == category,
                CitizenDemand.window_days == window_days,
            )
        )

        if existing is None:
            existing = CitizenDemand(
                ward_code=ward_code,
                sector=sector,
                category=category,
                sub_category=sub_category,
                window_days=window_days,
                complaint_count=0,
                avg_severity=0.0,
                max_severity=severity,
                critical_count=0,
                languages=[],
                source="module-4-ingest",
            )
            self.session.add(existing)

        existing.sub_category = sub_category
        existing.complaint_count = int(existing.complaint_count or 0) + 1
        # Running mean, recomputed rather than incremented, so the average stays
        # correct as the count grows.
        existing.avg_severity = round(
            (
                float(existing.avg_severity or 0.0) * (existing.complaint_count - 1) + severity
            )
            / existing.complaint_count,
            4,
        )
        existing.max_severity = max(int(existing.max_severity or 0), severity)
        # gap_service._recommendation treats this as the count of severity>=4
        # complaints, so it must stay consistent with the severity we just saw.
        if severity >= 4:
            existing.critical_count = int(existing.critical_count or 0) + 1

        if language:
            languages = list(existing.languages or [])
            if language not in languages:
                languages.append(language)
            existing.languages = languages

        observed = _as_utc(observed_at)
        current_from, current_to = _as_utc(existing.observed_from), _as_utc(existing.observed_to)
        existing.observed_from = observed if current_from is None else min(current_from, observed)
        existing.observed_to = observed if current_to is None else max(current_to, observed)
        existing.updated_at = utcnow()
        self.session.flush()
        return existing


def _as_utc(value: Any) -> Any:
    """Normalise a datetime to timezone-aware UTC.

    SQLite drops tzinfo on read, so a value fetched from ``citizen_demand`` can
    come back naive while a freshly parsed timestamp is aware. Comparing the two
    raises, so everything is normalised on the way into ``observed_from`` /
    ``observed_to``.
    """
    from datetime import timezone

    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _asset_types_for(sector: str) -> list[str]:
    from app.core.utils import SECTOR_ASSET_TYPES

    return SECTOR_ASSET_TYPES.get(sector, [])


def _apply_gap(record: GapRecord, result) -> None:
    record.gap_score = result.gap_score
    record.severity = result.severity
    record.demand_score = result.demand_score
    record.absence_score = result.absence_score
    record.quality_score = result.quality_score
    record.population = result.population
    record.complaints = result.complaints
    record.avg_severity = result.avg_severity
    record.asset_count = result.asset_count
    record.functional_asset_count = result.functional_asset_count
    record.served_population = result.served_population
    record.coverage_ratio = result.coverage_ratio
    record.committed_capex_lakhs = result.committed_capex_lakhs
    record.spent_capex_lakhs = result.spent_capex_lakhs
    record.active_project_count = result.active_project_count
    record.recommended_action = result.recommended_action
    record.rationale = result.rationale
    record.evidence = result.evidence
    record.computed_at = utcnow()
