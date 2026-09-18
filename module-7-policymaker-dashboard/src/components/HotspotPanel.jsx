/**
 * Side panel opened when a hotspot marker is clicked.
 *
 * Shows the category, request count, the full priority-score circle
 * breakdown (each contributing factor with raw value, normalised value,
 * weight, and contribution -- not just the final number), and up to three
 * sample citizen complaint texts as evidence.
 */
import { FACTOR_LABELS, formatRaw, priorityBand, priorityColor } from "../utils/priority";

function FactorRow({ name, factor }) {
  const pctOfScore = (factor.weight * 100).toFixed(1);
  return (
    <div className="factor-row">
      <div className="factor-row__header">
        <span className="factor-row__name">{FACTOR_LABELS[name] || name}</span>
        <span className="factor-row__weight">weight {factor.weight.toFixed(2)}</span>
      </div>
      <div className="factor-row__numbers">
        <span>raw {formatRaw(factor.raw)}</span>
        <span>→ {factor.normalised}/100 × {factor.weight}</span>
        <strong>+{factor.contribution.toFixed(2)} pts</strong>
      </div>
      {/* Visual bar: how much this factor contributes to the 0-100 total. */}
      <div className="factor-row__bar">
        <span
          className="factor-row__bar-fill"
          style={{ width: `${pctOfScore}%` }}
        />
      </div>
    </div>
  );
}

export default function HotspotPanel({ hotspot, onClose }) {
  if (!hotspot) return null;
  const factors = hotspot.priority_factors || {};

  return (
    <aside className="panel">
      <div className="panel__header">
        <div>
          <span className="panel__badge">{hotspot.category}</span>
          <h2>{hotspot.region}</h2>
        </div>
        <button className="panel__close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div className="panel__score">
        <div
          className="panel__score-ring"
          style={{ "--score-color": priorityColor(hotspot.priority_score) }}
        >
          {hotspot.priority_score}
        </div>
        <div className="panel__score-meta">
          <strong>{priorityBand(hotspot.priority_score)} priority</strong>
          <span>
            {hotspot.request_count} citizen request(s) · avg severity{" "}
            {hotspot.avg_severity}
          </span>
          <span>unresolved ~{Math.round(hotspot.avg_days_open)} days</span>
        </div>
      </div>

      <h3>How this score was built</h3>
      <p className="panel__hint">
        Each factor is normalised to 0-100, then multiplied by its weight. The
        weighted sum is the final score -- derivable by hand, no black box.
      </p>
      <div className="factors">
        {factors.volume && <FactorRow name="volume" factor={factors.volume} />}
        {factors.severity && <FactorRow name="severity" factor={factors.severity} />}
        {factors.population_density && (
          <FactorRow name="population_density" factor={factors.population_density} />
        )}
        {factors.days_open && <FactorRow name="days_open" factor={factors.days_open} />}
      </div>

      {hotspot.sample_texts && hotspot.sample_texts.length > 0 && (
        <>
          <h3>Citizen evidence</h3>
          <ul className="evidence">
            {hotspot.sample_texts.map((text, i) => (
              <li key={i}>“{text}”</li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}