/**
 * Detail panel for one hotspot: why it scored what it did, and what to do.
 *
 * The factor breakdown is the important part. A priority score a policymaker
 * cannot interrogate is not usable, so each factor shows its raw value, its
 * weight and its contribution. Factors Module 6 could not measure are listed
 * explicitly rather than quietly showing zero.
 */

import {
  bandColor,
  bandLabel,
  describeMovement,
  formatComponent,
  formatFactor,
  formatLakhs,
  formatRaw,
  formatSector,
} from "../utils/priority";

const EVIDENCE_ROWS = [
  ["complaints_per_1000", "Complaints per 1,000", (v) => (v == null ? null : v.toFixed(1))],
  ["total_complaints", "Complaints in window", (v) => formatRaw(v, 0)],
  ["critical_complaints", "Critical complaints", (v) => formatRaw(v, 0)],
  ["population", "Population covered", (v) => formatRaw(v, 0)],
  ["avg_severity", "Average severity", (v) => (v == null ? null : `${v.toFixed(1)} / 5`)],
  ["trend_direction", "Trend", (v) => (v == null ? null : String(v).toLowerCase())],
  ["pct_growth", "Change in complaints", (v) => (v == null ? null : `${v.toFixed(0)}%`)],
  ["gap_score", "Coverage gap", (v) => (v == null ? null : `${v.toFixed(0)}%`)],
  ["stalled_share", "Budget stalled", (v) => (v == null ? null : `${(v * 100).toFixed(0)}%`)],
  ["unspent_share", "Budget unspent", (v) => (v == null ? null : `${(v * 100).toFixed(0)}%`)],
  ["delay_days", "Average delay", (v) => (v == null ? null : `${v.toFixed(0)} days`)],
];

function FactorRow({ name, signal, label }) {
  if (!signal) return null;
  const measured = signal.measured !== false;
  return (
    <div className={`factor-row${measured ? "" : " factor-row--unmeasured"}`}>
      <div className="factor-row__head">
        <span className="factor-row__label">{label || formatFactor(name)}</span>
        <span className="factor-row__value">
          {measured ? formatComponent(signal.component) : "not measured"}
        </span>
      </div>
      <div className="factor-row__bar">
        <span
          className="factor-row__bar-fill"
          style={{ width: measured ? formatComponent(signal.component) : "100%" }}
        />
      </div>
      <p className={`factor-row__hint${measured ? "" : " factor-row__hint--warn"}`}>
        weight {formatComponent(signal.weight)}
        {signal.note ? ` · ${signal.note}` : ""}
      </p>
    </div>
  );
}

export default function HotspotPanel({ priority, onClose }) {
  if (!priority) {
    return (
      <aside className="panel panel--empty">
        <p className="empty-note">
          Select a hotspot on the map or in the plan to see how its score was
          reached.
        </p>
      </aside>
    );
  }

  const factors = priority.factors || {};
  const evidence = priority.evidence || {};
  const unmeasured = priority.unmeasured_factors || [];
  const labels = priority.factor_labels || {};

  return (
    <aside className="panel">
      <div className="panel__head">
        <div>
          <h2 className="panel__title">{priority.district}</h2>
          <p className="panel__code">{priority.hotspot_code}</p>
        </div>
        <button className="panel__close" onClick={onClose} aria-label="Close panel">
          ×
        </button>
      </div>

      <div className="panel__score">
        <span className="score-pill" style={{ "--score-color": bandColor(priority.band) }}>
          {priority.score}
        </span>
        <span className="panel__band">{bandLabel(priority.band)}</span>
        <span className="panel__rank">rank #{priority.rank}</span>
      </div>

      <p className="panel__driver">
        Main driver: {formatSector(priority.dominant_sector)} ·{" "}
        {describeMovement(priority.movement)}
      </p>

      {unmeasured.length > 0 && (
        <p className="panel__warn">
          Not measured here: {unmeasured.map(formatFactor).join(", ")}. The score
          has been scaled down by the share of weight that was measurable.
        </p>
      )}

      <h3 className="panel__subtitle">How the score was reached</h3>
      <div className="factor-list">
        {Object.entries(factors).map(([name, signal]) => (
          <FactorRow key={name} name={name} signal={signal} label={labels[name]} />
        ))}
      </div>

      <h3 className="panel__subtitle">Evidence</h3>
      <dl className="evidence">
        {EVIDENCE_ROWS.filter(([key]) => evidence[key] != null).map(([key, label, fmt]) => (
          <div className="evidence__row" key={key}>
            <dt>{label}</dt>
            <dd>{fmt(evidence[key])}</dd>
          </div>
        ))}
      </dl>

      <h3 className="panel__subtitle">Recommended action</h3>
      <p className="panel__recommendation">{priority.recommendation}</p>

      <p className="panel__footnote">
        Indicative envelope {formatLakhs(priority.recommended_cost_lakhs)} — a
        planning figure, not a cost estimate.
      </p>
    </aside>
  );
}