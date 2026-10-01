/**
 * Niti-Setu · Policymaker Dashboard
 *
 * Reads Module 6's ranking and shows where the pressure is, why each hotspot
 * scored what it did, and what to fund first.
 *
 * The load sequence is health, then ranking, then summary and plan, because it
 * has to tell two failure modes apart. If Module 6 is not running, the dashboard
 * says so and says how to start it. If Module 6 is running but has nothing
 * scored, it says *that* instead — a ranking that does not exist yet is a very
 * different situation from a broken service.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API_BASE_URL,
  fetchHealth,
  fetchPlan,
  fetchPriorities,
  fetchSummary,
} from "./api";

import HotspotMap from "./components/HotspotMap";
import HotspotPanel from "./components/HotspotPanel";
import PriorityChart from "./components/PriorityChart";
import RecommendationsList from "./components/RecommendationsList";
import { formatLakhs, formatRaw } from "./utils/priority";

const PLAN_SIZE = 10;

function StatTiles({ summary, health }) {
  if (!summary) return null;
  const tiles = [
    { label: "Hotspots ranked", value: formatRaw(summary.scored, 0) },
    { label: "Districts", value: formatRaw(summary.districts, 0) },
    { label: "People covered", value: formatRaw(summary.population_covered, 0) },
    { label: "Complaints covered", value: formatRaw(summary.complaints_covered, 0) },
    { label: "Indicative envelope", value: formatLakhs(summary.total_cost_lakhs) },
    { label: "Average score", value: formatRaw(summary.average_score) },
  ];

  return (
    <div className="tiles">
      {tiles.map((tile) => (
        <div className="tile" key={tile.label}>
          <span className="tile__value">{tile.value}</span>
          <span className="tile__label">{tile.label}</span>
        </div>
      ))}
      {summary.hotspots_with_unmeasured_factors > 0 && (
        <div className="tile tile--warn">
          <span className="tile__value">
            {summary.hotspots_with_unmeasured_factors}
          </span>
          <span className="tile__label">with unmeasured factors</span>
        </div>
      )}
      {health && health.status !== "healthy" && summary.scored > 0 && (
        <div className="tile tile--warn">
          <span className="tile__value">Degraded</span>
          <span className="tile__label">
            {health.degraded_factor_labels?.join(", ") || "upstream incomplete"}
          </span>
        </div>
      )}
    </div>
  );
}

function Notice({ error }) {
  if (error.isNotScored) {
    return (
      <div className="status-note status-note--warn">
        <strong>Nothing has been scored yet.</strong>
        <p>{error.detail || "Module 6 is running but has produced no ranking."}</p>
        <p className="status-note__hint">
          Seed Module 5 with{" "}
          <code>python ../module-5-civic-intelligence/seed_intelligence.py</code>,
          then recompute in Module 6.
        </p>
      </div>
    );
  }
  if (error.isUnreachable) {
    return (
      <div className="status-note status-note--error">
        <strong>Module 6 is not responding.</strong>
        <p>{error.message}</p>
        <p className="status-note__hint">
          Start it with <code>uvicorn app.main:app --port 8006</code> from{" "}
          <code>module-6-priority-recommendation</code>, or point{" "}
          <code>VITE_API_BASE_URL</code> somewhere else.
        </p>
      </div>
    );
  }
  return (
    <div className="status-note status-note--error">
      <strong>Could not load the ranking.</strong>
      <p>{error.message}</p>
      <p className="status-note__hint">Module 6 responded at {API_BASE_URL}.</p>
    </div>
  );
}

export default function App() {
  const [priorities, setPriorities] = useState([]);
  const [summary, setSummary] = useState(null);
  const [health, setHealth] = useState(null);
  const [plan, setPlan] = useState({ items: [], count: 0, is_partial: false });
  const [selectedCode, setSelectedCode] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async (signal) => {
    setLoading(true);
    try {
      // Health first: it is the only call that succeeds when nothing has been
      // scored, so it tells us whether a 409 downstream is a real problem.
      const healthBody = await fetchHealth(signal);
      setHealth(healthBody);

      const [ranking, summaryBody, planBody] = await Promise.all([
        fetchPriorities({}, signal),
        fetchSummary(signal),
        fetchPlan(PLAN_SIZE, signal),
      ]);
      setPriorities(ranking.priorities || []);
      setSummary(summaryBody);
      setPlan(planBody);
      setError(null);
    } catch (e) {
      if (e.name === "AbortError") return;
      setError(e);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // Re-resolve the selection against live data, so a recompute that drops a
  // hotspot closes its panel instead of showing a stale row.
  const selected = useMemo(
    () => priorities.find((p) => p.hotspot_code === selectedCode) || null,
    [priorities, selectedCode],
  );

  const hasData = priorities.length > 0;

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1>Niti-Setu · Policymaker Dashboard</h1>
          <p>
            Where civic pressure is highest, why it scored what it did, and what
            to fund first.
          </p>
        </div>
        <div className="app__status">
          {health && (
            <span
              className={`pill pill--${health.status === "healthy" ? "ok" : "warn"}`}
            >
              Module 6 {health.status}
            </span>
          )}
          <button
            className="button"
            onClick={() => load()}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {loading && !hasData && (
        <p className="status-note">Loading the ranking…</p>
      )}
      {error && <Notice error={error} />}

      {hasData && (
        <>
          <StatTiles summary={summary} health={health} />

          <div className="map-row">
            <HotspotMap
              priorities={priorities}
              onSelect={(p) => setSelectedCode(p.hotspot_code)}
              selectedCode={selectedCode}
            />
            <HotspotPanel
              priority={selected}
              onClose={() => setSelectedCode(null)}
            />
          </div>

          <div className="lower-grid">
            <PriorityChart priorities={priorities} summary={summary} />
            <RecommendationsList
              plan={plan}
              portfolioCount={summary?.scored}
              portfolioTotal={summary?.total_cost_lakhs}
              onSelect={(code) => setSelectedCode(code)}
            />
          </div>
        </>
      )}

      <footer className="app__footer">
        Priority scores computed by Module 6 from Modules 4 and 5 · OpenStreetMap
        tiles · Indicative costs are planning envelopes, not estimates
      </footer>
    </div>
  );
}