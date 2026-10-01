/**
 * The funding plan, in rank order.
 *
 * Reads `/api/v1/plan`, which returns a cumulative envelope per row — the
 * running total is what a budget discussion actually needs, since the question
 * is usually "how far down the list does X get me" rather than "what does this
 * one cost".
 */

import { useState } from "react";
import { bandColor, formatLakhs, formatSector } from "../utils/priority";

const FILTERS = ["ALL", "HIGH", "MEDIUM", "LOW"];

function rankClass(rank) {
  if (rank === 1) return "rank rank--top1";
  if (rank === 2) return "rank rank--top2";
  if (rank === 3) return "rank rank--top3";
  return "rank rank--rest";
}

export default function RecommendationsList({
  plan,
  portfolioCount,
  portfolioTotal,
  onSelect,
}) {
  const [filter, setFilter] = useState("ALL");
  const rows = plan?.items || [];
  const isPartial = Boolean(plan?.is_partial);

  if (rows.length === 0) {
    return (
      <section className="recommendations">
        <h2 className="recommendations__title">Funding plan</h2>
        <p className="empty-note">
          No plan yet. Run <code>POST /api/v1/operations/recompute</code> in
          Module 6.
        </p>
      </section>
    );
  }

  const visible = filter === "ALL" ? rows : rows.filter((r) => r.band === filter);
  const visibleCost = visible.reduce(
    (sum, r) => sum + (r.recommended_cost_lakhs || 0),
    0,
  );

  return (
    <section className="recommendations">
      <div className="recommendations__toolbar">
        <h2 className="recommendations__title">Funding plan</h2>
        <div className="recommendations__filters">
          {FILTERS.map((f) => (
            <button
              key={f}
              className={`chip${filter === f ? " chip--active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f === "ALL" ? "All" : f.toLowerCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="recommendations__totals">
        <span>
          Showing {visible.length} of {portfolioCount ?? rows.length} hotspots
        </span>
        <span>{formatLakhs(visibleCost)}</span>
      </div>

      {isPartial && (
        <p className="recommendations__warn">
          Top {rows.length} of {portfolioCount} ranked hotspots. The full
          ranking carries an indicative envelope of {formatLakhs(portfolioTotal)}.
        </p>
      )}

      <ol className="rec-list">
        {visible.map((item) => (
          <li
            key={item.hotspot_code}
            className="rec-card"
            onClick={() => onSelect?.(item.hotspot_code)}
          >
            <span className={rankClass(item.rank)}>{item.rank}</span>
            <div className="rec-card__body">
              <div className="rec-card__header">
                <span className="rec-card__title">{item.district}</span>
                <span
                  className="score-pill"
                  style={{ "--score-color": bandColor(item.band) }}
                >
                  {item.score}
                </span>
              </div>
              <p className="rec-card__rec">{item.recommendation}</p>
              <p className="rec-card__meta">
                {formatSector(item.dominant_sector)} ·{" "}
                {formatLakhs(item.recommended_cost_lakhs)} this ·{" "}
                <span className="rec-card__cumulative">
                  {formatLakhs(item.cumulative_cost_lakhs)} cumulative
                </span>
              </p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}