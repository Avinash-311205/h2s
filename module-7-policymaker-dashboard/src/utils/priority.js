/**
 * Priority-score helpers shared across the dashboard.
 *
 * Turn a 0-100 priority score into a colour + size + plain-language label so
 * the map, list, and chart all speak the same visual language.
 */

/** Map a 0-100 score to a colour: red = high, amber = medium, green = low. */
export function priorityColor(score) {
  if (score >= 75) return "#d64545"; // high  -- urgent, act now
  if (score >= 50) return "#e8a33d"; // medium -- plan an intervention
  return "#43a047";                   // low   -- monitor / schedule
}

/**
 * Map a 0-100 score to a radius (pixels on the map). Scaled non-linearly so
 * small differences near the top still read clearly.
 */
export function priorityRadius(score) {
  const minRadius = 10;
  const maxRadius = 34;
  const t = Math.min(1, Math.max(0, score / 100));
  return Math.round(minRadius + (maxRadius - minRadius) * Math.pow(t, 1.4));
}

/** Human label for a 0-100 score band. */
export function priorityBand(score) {
  if (score >= 75) return "High";
  if (score >= 50) return "Medium";
  return "Low";
}

/** Short name for each contributing factor (used in the side panel / chart). */
export const FACTOR_LABELS = {
  volume: "Volume of requests",
  severity: "Average severity",
  population_density: "Population density",
  days_open: "Days unresolved",
};

/** Pretty-printer for a factor's raw value (handles None / missing). */
export function formatRaw(raw) {
  if (raw === null || raw === undefined) return "—";
  return Number(raw).toLocaleString(undefined, { maximumFractionDigits: 1 });
}