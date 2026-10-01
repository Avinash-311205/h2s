"""The Module 5 boundary: aggregation, dominant sector, and missing tables."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.intelligence_client import IntelligenceClient, IntelligenceUnavailable
import synthetic_snapshot as factory


@pytest.fixture
def client_factory(tmp_path: Path):
    def build(conn=None, name: str = "intel.db") -> IntelligenceClient:
        if conn is None:
            conn = factory.create_tables(tmp_path / name)
        return IntelligenceClient(f"sqlite:///{tmp_path / name}")

    return build


class TestSnapshot:
    def test_healthy_snapshot_is_not_degraded(self, client_factory):
        client = client_factory()
        conn = factory.healthy_snapshot(Path(client.database_url.replace("sqlite:///", "")))
        snapshot = client.fetch_snapshot()
        assert snapshot.available is True
        assert snapshot.degraded_factors == ()
        conn.close()

    def test_missing_database_raises(self, tmp_path):
        client = IntelligenceClient(f"sqlite:///{tmp_path / 'absent.db'}")
        with pytest.raises(IntelligenceUnavailable) as exc:
            client.fetch_snapshot()
        assert "not found" in str(exc.value)

    def test_database_without_hotspots_table_raises(self, tmp_path):
        path = tmp_path / "empty.db"
        factory.create_tables(path, tables=[])
        client = IntelligenceClient(f"sqlite:///{path}")
        with pytest.raises(IntelligenceUnavailable):
            client.fetch_snapshot()


class TestDegradation:
    """A missing upstream table degrades one factor rather than failing the run."""

    def _degraded(self, tmp_path, missing: str) -> tuple[set[str], IntelligenceClient]:
        path = tmp_path / f"no_{missing}.db"
        tables = [
            "hotspots",
            "demand_windows",
            "gap_snapshots",
            "project_snapshots",
            "trends",
        ]
        tables.remove(missing)
        conn = factory.create_tables(path, tables=tables)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        return set(client_snapshot(tmp_path, path).degraded_factors), None

    def test_missing_gap_snapshots_degrades_coverage(self, tmp_path):
        degraded, _ = self._degraded(tmp_path, "gap_snapshots")
        assert "coverage" in degraded

    def test_missing_project_snapshots_degrades_service_failure(self, tmp_path):
        degraded, _ = self._degraded(tmp_path, "project_snapshots")
        assert "service_failure" in degraded

    def test_missing_demand_windows_degrades_demand_and_severity(self, tmp_path):
        degraded, _ = self._degraded(tmp_path, "demand_windows")
        assert "demand" in degraded
        assert "severity" in degraded

    def test_missing_trends_degrades_trend(self, tmp_path):
        degraded, _ = self._degraded(tmp_path, "trends")
        assert "trend" in degraded


def client_snapshot(tmp_path: Path, path: Path):
    """Convenience: build a client for ``path`` and read its snapshot."""
    return IntelligenceClient(f"sqlite:///{path}").fetch_snapshot()


class TestAggregation:
    def test_population_is_summed_across_wards(self, tmp_path):
        path = tmp_path / "agg.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1", "W2"], sectors=["WATER"], population=0)
        factory.add_demand(conn, "W1", complaints=100, population=6_000)
        factory.add_demand(conn, "W2", complaints=100, population=4_000)
        snapshot = client_snapshot(tmp_path, path)
        hotspot = snapshot.hotspots[0]
        assert hotspot.population == 10_000
        assert hotspot.demand_rate_per_1000 == pytest.approx(20.0)

    def test_demand_excludes_wards_outside_the_hotspot(self, tmp_path):
        """Another ward's complaints must not inflate this hotspot."""
        path = tmp_path / "scope.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_demand(conn, "W1", complaints=100, population=10_000)
        factory.add_demand(conn, "OTHER", complaints=9_999, population=10_000)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.demand_rate_per_1000 == pytest.approx(10.0)

    def test_trend_is_restricted_to_the_hotspot_sectors(self, tmp_path):
        path = tmp_path / "sector.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_trend(conn, "W1", "WATER", "WORSENING", 60.0)
        factory.add_trend(conn, "W1", "ELECTRICITY", "WORSENING", 999.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.pct_growth == pytest.approx(60.0)


class TestDominantSector:
    def test_dominant_sector_comes_from_the_worst_trend_not_alphabetical_order(self, tmp_path):
        path = tmp_path / "dom.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER", "ELECTRICITY"])
        factory.add_trend(conn, "W1", "WATER", "WORSENING", 10.0)
        factory.add_trend(conn, "W1", "ELECTRICITY", "WORSENING", 90.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.dominant_sector == "ELECTRICITY"

    def test_dominant_sector_falls_back_to_the_worst_gap(self, tmp_path):
        path = tmp_path / "dom2.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER", "ROAD"])
        factory.add_gap(conn, "W1", "WATER", 10.0)
        factory.add_gap(conn, "W1", "ROAD", 80.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.dominant_sector == "ROAD"

    def test_dominant_sector_is_none_when_no_sector_data_exists(self, tmp_path):
        path = tmp_path / "dom3.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.dominant_sector is None


class TestTrendVocabulary:
    """Guard against a trend direction that matches nothing upstream.

    Regression test. The scoring code originally expected ``RISING``/``FALLING``
    while Module 5 writes ``WORSENING``/``IMPROVING``. Every real hotspot then
    fell through to a zero contribution while still reporting ``measured=True``,
    so the trend factor silently did nothing on real data and every hotspot
    scored LOW. These assert the two vocabularies actually meet.
    """

    def test_worsening_direction_is_recognised(self, tmp_path):
        path = tmp_path / "vocab.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_trend(conn, "W1", "WATER", "WORSENING", 100.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.trend_direction == "WORSENING"

    def test_worsening_direction_actually_moves_the_score(self, tmp_path):
        """The factor must contribute, not just be recorded as measured."""
        from app.services.priority_service import score_hotspot

        worsening = score_hotspot(
            client_snapshot(tmp_path, _db(tmp_path, "WORSENING")).hotspots[0]
        )
        improving = score_hotspot(
            client_snapshot(tmp_path, _db(tmp_path, "IMPROVING")).hotspots[0]
        )
        assert worsening["factors"]["trend"]["component"] > 0.0
        assert worsening["factors"]["trend"]["measured"] is True
        assert worsening["score"] > improving["score"]

    def test_unknown_direction_degrades_to_neutral(self, tmp_path):
        from app.services.intelligence_client import _weighted_growth

        assert _weighted_growth("SOME_FUTURE_VALUE", 100.0) == 0.0


def _db(tmp_path: Path, direction: str) -> Path:
    """A snapshot whose single trend has the given direction."""
    path = tmp_path / f"trend_{direction}.db"
    conn = factory.create_tables(path)
    factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
    factory.add_trend(conn, "W1", "WATER", direction, 100.0)
    conn.close()
    return path


class TestGapRecency:
    def test_only_the_latest_snapshot_per_ward_sector_counts(self, tmp_path):
        """A gap fixed last year must stop scoring."""
        path = tmp_path / "recency.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_gap(conn, "W1", "WATER", 90.0, snapshot_at="2025-01-01")
        factory.add_gap(conn, "W1", "WATER", 12.0, snapshot_at="2026-09-01")
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.gap_score == pytest.approx(12.0)


class TestDeliverySignals:
    def test_stalled_share_counts_only_non_delivering_statuses(self, tmp_path):
        path = tmp_path / "stall.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_project(conn, "P1", "W1", status="IN_PROGRESS", budget=100.0, spent=90.0)
        factory.add_project(conn, "P2", "W1", status="STALLED", budget=100.0, spent=0.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.stalled_share == pytest.approx(0.5)

    def test_unstarted_project_is_not_counted_as_unspent(self, tmp_path):
        """An unstarted project has spent nothing but is not a delivery failure."""
        path = tmp_path / "unspent.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        factory.add_project(conn, "P1", "W1", status="PLANNED", budget=100.0, spent=0.0)
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert hotspot.unspent_share == pytest.approx(0.0)

    def test_hotspot_with_no_projects_is_unmeasured(self, tmp_path):
        path = tmp_path / "noproj.db"
        conn = factory.create_tables(path)
        factory.add_hotspot(conn, "HS-1", wards=["W1"], sectors=["WATER"])
        hotspot = client_snapshot(tmp_path, path).hotspots[0]
        assert "service_failure" in hotspot.unmeasured_factors