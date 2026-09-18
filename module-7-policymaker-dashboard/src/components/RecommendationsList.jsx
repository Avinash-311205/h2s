/**
 * Ranked list of recommended projects from Module 6's /recommendations.
 *
 * Includes two dropdown filters (category + region) applied client-side so
 * policymakers can narrow down to their ward / issue type instantly. Being
 * ranked top-to-bottom by priority score, the list doubles as a funding
 * order-of-operations view.
 */
import { useMemo, useState } from "react";
import { priorityBand, priorityColor } from "../utils/priority";

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

function RankBadge({ rank }) {
  return (
    <span className={`rank rank--${rank <= 3 ? `top${rank}` : "rest"}`}>
      #{rank}
    </span>
  );
}

function ScorePill({ score }) {
  return (
    <span
      className="score-pill"
      style={{ "--score-color": priorityColor(score) }}
    >
      {score} · {priorityBand(score)}
    </span>
  );
}

export default function RecommendationsList({ recommendations }) {
  const [category, setCategory] = useState("all");
  const [region, setRegion] = useState("all");

  const categories = useMemo(
    () => unique(recommendations.map((r) => r.category)),
    [recommendations],
  );
  const regions = useMemo(
    () => unique(recommendations.map((r) => r.region)),
    [recommendations],
  );

  // Never filter labels themselves in/out: keep both full dropdowns stable.
  const filtered = recommendations.filter(
    (r) =>
      (category === "all" || r.category === category) &&
      (region === "all" || r.region === region),
  );

  return (
    <section className="recommendations">
      <div className="recommendations__toolbar">
        <h3 className="recommendations__title">Recommended projects</h3>
        <div className="recommendations__filters">
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            aria-label="Filter by category"
          >
            <option value="all">All categories</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            aria-label="Filter by region"
          >
            <option value="all">All regions</option>
            {regions.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
      </div>

      <span className="recommendations__count">
        {filtered.length} of {recommendations.length} project(s) shown
      </span>

      <ol className="rec-list">
        {filtered.map((rec, i) => (
          <li key={rec.id} className="rec-card">
            <RankBadge rank={i + 1} />
            <div className="rec-card__body">
              <header className="rec-card__header">
                <span className="rec-card__title">
                  {rec.category} — {rec.region}
                </span>
                <ScorePill score={rec.priority_score} />
              </header>
              <p className="rec-card__rec">{rec.recommendation}</p>
              <footer className="rec-card__meta">
                {rec.request_count} requests · avg severity {rec.avg_severity} ·{" "}
                ~{Math.round(rec.avg_days_open)} days open
              </footer>
            </div>
          </li>
        ))}
      </ol>

      {filtered.length === 0 && (
        <p className="empty-note">No projects match the selected filters.</p>
      )}
    </section>
  );
}