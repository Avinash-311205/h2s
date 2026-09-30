"""Tests for hotspot detection.

The behaviours worth protecting here are the judgement calls: normalising by
population, clustering by geography, and refusing to invent a hotspot where there
is no demand.
"""

from __future__ import annotations

import pytest

from app.services.hotspot_service import (
    HotspotResult,
    WardSignal,
    build_hotspot,
    cluster_wards,
    coverage_note,
    detect_hotspots,
    district_rollup,
    haversine_m,
    median_intensity,
    summarise_tiers,
    tier_for,
)


def ward(code, *, lat=13.08, lon=80.27, population=10_000, complaints=0, district="Chennai",
         critical=0, severity=3.0, sectors=None):
    return WardSignal(
        ward_code=code,
        name=code,
        district=district,
        latitude=lat,
        longitude=lon,
        population=population,
        complaints=complaints,
        critical_complaints=critical,
        peak_severity=severity,
        sectors=sectors or [],
    )


class TestHaversine:
    def test_identical_points_are_zero_apart(self):
        assert haversine_m(13.0, 80.0, 13.0, 80.0) == pytest.approx(0.0, abs=1e-6)

    def test_one_degree_of_latitude_is_about_111km(self):
        distance = haversine_m(13.0, 80.0, 14.0, 80.0)
        assert distance == pytest.approx(111_000, rel=0.01)

    def test_distance_is_symmetric(self):
        forward = haversine_m(13.0, 80.0, 13.5, 80.5)
        backward = haversine_m(13.5, 80.5, 13.0, 80.0)
        assert forward == pytest.approx(backward)


class TestIntensity:
    def test_intensity_is_complaints_per_thousand_residents(self):
        assert ward("W1", population=10_000, complaints=50).intensity == pytest.approx(5.0)

    def test_zero_population_does_not_divide_by_zero(self):
        assert ward("W1", population=0, complaints=50).intensity == pytest.approx(50000.0)

    def test_a_small_dense_ward_outranks_a_large_quiet_one(self):
        # The whole justification for normalising: 200 complaints in a dense ward
        # of 4,000 is a worse situation than 600 in a sprawling ward of 30,000.
        dense = ward("DENSE", population=4_000, complaints=200)
        sprawling = ward("WIDE", population=30_000, complaints=600)
        assert dense.intensity > sprawling.intensity


class TestClustering:
    def test_nearby_wards_form_one_cluster(self):
        wards = [ward("A", lat=13.08), ward("B", lat=13.09)]
        clusters = cluster_wards(wards, radius_m=12_000)
        assert len(clusters) == 1
        assert {w.ward_code for w in clusters[0]} == {"A", "B"}

    def test_distant_wards_stay_separate(self):
        wards = [ward("A", lat=13.08), ward("B", lat=15.08)]
        assert len(cluster_wards(wards, radius_m=12_000)) == 2

    def test_a_chain_of_neighbours_merges_transitively(self):
        # A->B and B->C are each inside the radius, so all three are one cluster.
        wards = [ward("A", lat=13.000), ward("B", lat=13.090), ward("C", lat=13.180)]
        clusters = cluster_wards(wards, radius_m=12_000)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_a_single_ward_is_its_own_cluster(self):
        assert len(cluster_wards([ward("A")], radius_m=12_000)) == 1

    def test_no_wards_is_no_clusters(self):
        assert cluster_wards([], radius_m=12_000) == []


class TestTiering:
    def test_top_of_the_distribution_is_critical(self):
        assert tier_for(1.0) == "CRITICAL"
        assert tier_for(0.95) == "CRITICAL"

    def test_upper_quartile_is_high(self):
        assert tier_for(0.80) == "HIGH"

    def test_middle_is_moderate(self):
        assert tier_for(0.55) == "MODERATE"

    def test_bottom_is_normal(self):
        assert tier_for(0.10) == "NORMAL"


class TestDetectHotspots:
    def test_no_wards_yields_no_hotspots(self):
        assert detect_hotspots([]) == []

    def test_wards_with_no_complaints_are_not_hotspots(self):
        # Silence is not a finding; including it would drag the percentile
        # cut-offs down for everyone else.
        assert detect_hotspots([ward("A", complaints=0), ward("B", complaints=0)]) == []

    def test_a_single_loud_cluster_is_detected_and_tiered(self):
        wards = [ward("A", lat=13.00, complaints=300), ward("B", lat=13.05, complaints=280),
                 ward("C", lat=17.0, complaints=5, district="Madurai")]
        hotspots = detect_hotspots(wards)
        assert len(hotspots) == 2
        assert hotspots[0].district == "Chennai"
        assert hotspots[0].tier == "CRITICAL"

    def test_results_are_ranked_worst_first(self):
        wards = [ward("A", lat=13.00, complaints=400), ward("B", lat=15.0, complaints=10),
                 ward("C", lat=17.0, complaints=5)]
        intensities = [hotspot.mean_intensity for hotspot in detect_hotspots(wards)]
        assert intensities == sorted(intensities, reverse=True)

    def test_min_complaints_filters_small_clusters(self):
        wards = [ward("A", lat=13.0, complaints=3), ward("B", lat=15.0, complaints=2)]
        assert len(detect_hotspots(wards, min_complaints=10)) == 0

    def test_cluster_aggregates_population_and_sectors(self):
        wards = [
            ward("A", lat=13.00, complaints=100, sectors=["WATER"]),
            ward("B", lat=13.05, complaints=60, sectors=["WATER", "ROAD"]),
        ]
        hotspot = detect_hotspots(wards)[0]
        assert hotspot.population == 20_000
        assert hotspot.total_complaints == 160
        assert hotspot.sectors == ["ROAD", "WATER"]
        assert hotspot.ward_count == 2

    def test_critical_complaints_are_summed(self):
        wards = [ward("A", complaints=100, critical=9), ward("B", lat=13.05, complaints=50, critical=4)]
        assert detect_hotspots(wards)[0].critical_complaints == 13


class TestBuildHotspot:
    def test_intensity_is_cluster_wide_not_a_mean_of_ward_rates(self):
        # Using the cluster total stops one huge ward from masking several small
        # loud ones.
        wards = [ward("BIG", population=100_000, complaints=10, lat=13.0),
                 ward("SMALL", population=1_000, complaints=90, lat=13.05)]
        hotspot = build_hotspot(wards, [0.1, 90.0])
        assert hotspot.mean_intensity == pytest.approx(100 / 101, abs=0.01)

    def test_z_score_compares_against_the_supplied_ward_intensities(self):
        # A cluster far above the ward distribution must score positive.
        wards = [ward("A", population=1_000, complaints=900)]
        assert build_hotspot(wards, [1.0, 2.0, 3.0]).intensity_z_score > 0

    def test_z_score_is_negative_for_a_quieter_than_average_cluster(self):
        wards = [ward("A", population=100_000, complaints=10)]
        assert build_hotspot(wards, [5.0, 20.0, 40.0]).intensity_z_score < 0

    def test_as_dict_is_json_ready(self):
        wards = [ward("A", complaints=10)]
        payload = build_hotspot(wards, [1.0]).as_dict()
        assert isinstance(payload["ward_codes"], list)
        assert isinstance(payload["mean_intensity"], float)


class TestSummaries:
    def test_tier_counts_always_report_every_tier(self):
        counts = summarise_tiers([build_hotspot([ward("A", complaints=1)], [1.0])])
        assert set(counts) == {"CRITICAL", "HIGH", "MODERATE", "NORMAL"}

    def test_district_rollup_splits_by_district_and_shares_the_total(self):
        hotspots = [
            build_hotspot([ward("A", complaints=100, district="Chennai")], [1.0]),
            build_hotspot([ward("B", complaints=100, district="Madurai", lat=15.0)], [1.0]),
        ]
        rows = district_rollup(hotspots)
        assert len(rows) == 2
        assert sum(row["complaint_share"] for row in rows) == pytest.approx(1.0)

    def test_district_rollup_of_nothing_is_empty(self):
        assert district_rollup([]) == []

    def test_coverage_reveals_a_city_wide_problem(self):
        # Hotspots covering nearly everyone is the honest signal that the whole
        # city is affected, rather than a long list implying focus.
        wards = [ward("A", lat=13.0, complaints=10), ward("B", lat=13.05, complaints=10)]
        note = coverage_note(detect_hotspots(wards), wards)
        assert note["covered_share"] == pytest.approx(1.0)

    def test_median_intensity_of_nothing_is_zero(self):
        assert median_intensity([]) == 0.0

    def test_median_intensity_averages_the_middle_pair(self):
        hotspots = [
            build_hotspot([ward("A", complaints=10, population=10_000)], [1.0]),
            build_hotspot([ward("B", complaints=20, population=10_000, lat=15.0)], [1.0]),
        ]
        assert median_intensity(hotspots) == pytest.approx(1.5)


class TestHotspotResultSerialisation:
    def test_as_dict_defaults_ward_count_to_the_member_list(self):
        result = HotspotResult(
            district="Chennai", latitude=13.0, longitude=80.0,
            ward_codes=["A", "B"], sectors=["WATER"], population=100,
            total_complaints=1, critical_complaints=0, mean_intensity=1.0,
            peak_intensity=1.0, peak_severity=3.0, intensity_z_score=0.5,
            tier="HIGH", window_days=30,
        )
        assert result.as_dict()["ward_count"] == 2