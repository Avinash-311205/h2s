/**
 * Band mix and average score per district.
 *
 * The band counts come from Module 6's summary endpoint rather than being tallied
 * here, so the legend always agrees with the ranking. The bars are scaled to the
 * largest district count, and the average is the mean of the API's scores rather
 * than a band midpoint guessed from the thresholds — the thresholds live in
 * Module 6 and are not duplicated here.
 */

import { useMemo } from "react";
import {
  bandColor,
  bandLabel,
  formatComponent,
  formatLakhs,
  formatRaw,
} from "../utils/priority";

const BAND_ORDER = ["HIGH", "MEDIUM", "LOW"];

function BandMix({ bands, total }) {
  if (!bands || !total) return null;
  const max = Math.max(...BAND_ORDER.map((b) => bands[b] || 0), 1);

  return (
    <div className="band-mix">
      {BAND_ORDER.map((band) => {
        const count = bands[band] || 0;
        return (
          <div className="band-mix__row" key={band}>
            <span className="band-mix__label">
              <span
                className="band-mix__dot"
                style={{ background: bandColor(band) }}
              />
              {band.toLowerCase()}
            </span>
            <span className="band-mix__track">
              <span
                className="band-mix__fill"
                style={{
                  width: `${(count / max) * 100}%`,
                  background: bandColor(band),
                }}
              />
            </span>
            <span className="band-mix__count">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function PriorityChart({ priorities, summary }) {
  const rows = useMemo(() => {
    const byDistrict = new Map();
    (priorities || []).forEach((p) => {
      const entry = byDistrict.get(p.district) || {
        district: p.district,
        total: 0,
        count: 0,
        cost: 0,
        unmeasured: 0,
      };
      entry.total += Number(p.score || 0);
      entry.count += 1;
      entry.cost += Number(p.recommended_cost_lakhs || 0);
      if ((p.unmeasured_factors || []).length > 0) entry.unmeasured += 1;
      byDistrict.set(p.district, entry);
    });
    return [...byDistrict.values()]
      .map((d) => ({
        ...d,
        average: d.total / d.count,
      }))
      .sort((a, b) => b.average - a.average);
  }, [priorities]);

  if (rows.length === 0) {
    return (
      <section className="chart-wrap">
        <h2 className="chart__title">District overview</h2>
        <p className="empty-note">No ranking to summarise yet.</p>
      </section>
    );
  }

  const maxAverage = Math.max(...rows.map((r) => r.average), 1);
  const unmeasuredTotal = summary?.hotspots_with_unmeasured_factors || 0;

  return (
    <section className="chart-wrap">
      <h2 className="chart__title">District overview</h2>

      <BandMix bands={summary?.bands} total={summary?.scored} />

      <ul className="district-list">
        {rows.map((row) => (
          <li className="district-row" key={row.district}>
            <span className="district-row__name">{row.district}</span>
            <span className="district-row__track">
              <span
                className="district-row__fill"
                style={{ width: `${(row.average / maxAverage) * 100}%` }}
              />
            </span>
            <span className="district-row__score">{formatRaw(row.average)}</span>
            <span className="district-row__cost">{formatLakhs(row.cost)}</span>
          </li>
        ))}
      </ul>

      {unmeasuredTotal > 0 && (
        <p className="chart__warn">
          {unmeasuredTotal} of {summary.scored} hotspots have at least one factor
          that could not be measured. Their scores are scaled down accordingly.
        </p>
      )}

      <p className="chart__footnote">
        Bars show the mean score per district; the mix shows how many hotspots
        fall in each band. {bandLabel("HIGH")} is Module 6's own cut-off.
      </p>
    </section>
  );
}