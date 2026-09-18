/**
 * Niti-Setu · Policymaker Dashboard
 *
 * Root component: fetches hotspots + recommendations from Module 6, lays
 * out the Leaflet map, the detail side panel, the recommendations list,
 * and the summary chart. All state lives here and is passed down.
 */
import { useEffect, useMemo, useState } from "react";
import { fetchHotspots, fetchRecommendations, API_BASE_URL } from "./api";

import HotspotMap from "./components/HotspotMap";
import HotspotPanel from "./components/HotspotPanel";
import RecommendationsList from "./components/RecommendationsList";
import PriorityChart from "./components/PriorityChart";

export default function App() {
  const [hotspots, setHotspots] = useState([]);
  const [recommendations, setRecommendations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [hot, recs] = await Promise.all([
          fetchHotspots(),
          fetchRecommendations(100),
        ]);
        if (cancelled) return;
        setHotspots(hot.hotspots || []);
        setRecommendations(recs.recommendations || []);
        setError(null);
      } catch (e) {
        if (!cancelled) {
          setError(
            `${e.message}. Is Module 6 running at ${API_BASE_URL}? ` +
              "See module-7 README for setup.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the side panel in sync if data reloads / hotspot disappears.
  const selectedId = selected?.id;
  const liveSelected = useMemo(
    () => hotspots.find((h) => h.id === selectedId) || null,
    [hotspots, selectedId],
  );

  return (
    <div className="app">
      <header className="app__header">
        <h1>Niti-Setu · Policymaker Dashboard</h1>
        <p>
          Transparent, data-driven prioritisation of citizen infrastructure
          requests.
        </p>
      </header>

      {loading && <p className="status-note">Loading hotspot data…</p>}
      {!loading && error && <p className="status-note status-note--error">{error}</p>}

      {!loading && !error && (
        <>
          <div className="map-row">
            <div className="map-wrap">
              <HotspotMap hotspots={hotspots} onSelect={setSelected} />
            </div>
            <HotspotPanel hotspot={liveSelected} onClose={() => setSelected(null)} />
          </div>

          <div className="lower-grid">
            <div className="chart-wrap">
              <PriorityChart hotspots={hotspots} />
            </div>
            <div className="rec-wrap">
              <RecommendationsList recommendations={recommendations} />
            </div>
          </div>
        </>
      )}

      <footer className="app__footer">
        Powered by Niti-Setu Modules 6 &amp; 7 · OpenStreetMap tiles · Recharts
      </footer>
    </div>
  );
}