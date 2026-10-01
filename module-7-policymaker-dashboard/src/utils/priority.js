/**
 * Formatting and presentation helpers for priority data.
 *
 * The band *thresholds* deliberately do not live here. Module 6 owns the policy
 * and returns a `band` on every row; duplicating the cut-offs in the frontend
 * would guarantee the two drift apart and the dashboard would end up colouring a
 * hotspot the wrong way. Everything here reads the API's answer.
 */

/** Colours per band. Chosen to stay distinguishable without relying on hue alone. */
export const BAND_COLORS = {
  HIGH: "#d64545",
  MEDIUM: "#e8a33d",
  LOW: "#7ca8c9",
};

export const BAND_LABELS = {
  HIGH: "High priority",
  MEDIUM: "Medium priority",
  LOW: "Low priority",
};

export function bandColor(band) {
  return BAND_COLORS[band] || "#94a3b8";
}

export function bandLabel(band) {
  return BAND_LABELS[band] || band || "Unbanded";
}

/** A sector code like `WATER_SUPPLY` for display. */
export function formatSector(sector) {
  if (!sector) return "Unspecified";
  return String(sector)
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Turns `service_failure` into `Service failure`. */
export function formatFactor(factor) {
  if (!factor) return "";
  const spaced = String(factor).replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** A 0-100 factor component as a percentage string. */
export function formatComponent(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

/**
 * Lakhs as a readable amount.
 *
 * Lakh is the unit Indian budgets are actually written in, so costs stay in
 * lakhs rather than being converted to a larger unit the reader would have to
 * divide in their head.
 */
export function formatLakhs(lakhs) {
  if (lakhs === null || lakhs === undefined) return "—";
  if (lakhs >= 100) return `₹${Math.round(lakhs)}L`;
  return `₹${lakhs.toFixed(1)}L`;
}

export function formatRaw(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** "12.4 complaints per 1,000 residents" */
export function describeRate(rate) {
  if (rate === null || rate === undefined) return "not measured";
  return `${Number(rate).toFixed(1)} per 1,000 residents`;
}

/** The factor contributing most weight to a score, for a one-line headline. */
export function dominantFactor(item) {
  const factors = item?.factors || {};
  let best = null;
  Object.entries(factors).forEach(([name, sig]) => {
    if (!sig || sig.measured === false) return;
    const contribution = (sig.component || 0) * (sig.weight || 0);
    if (!best || contribution > best.contribution) {
      best = { name, contribution };
    }
  });
  return best?.name || null;
}

/** Rank movement, phrased for a person rather than a spreadsheet. */
export function describeMovement(movement) {
  if (!movement || movement.rank_change === null || movement.rank_change === 0) {
    return "No change since last run";
  }
  if (movement.rank_change > 0) return `Up ${movement.rank_change} since last run`;
  return `Down ${Math.abs(movement.rank_change)} since last run`;
}

/**
 * A map bounding box that fits the given points, with padding.
 *
 * Derived from the data rather than hardcoded to Tamil Nadu, so the map stays
 * correct if the data covers different places. Fits with a small margin so
 * markers are never clipped at the edge.
 */
export function boundsFor(items) {
  const points = (items || []).filter(
    (i) => i.centroid_latitude != null && i.centroid_longitude != null,
  );
  if (points.length === 0) return null;
  const lats = points.map((p) => Number(p.centroid_latitude));
  const lngs = points.map((p) => Number(p.centroid_longitude));
  const pad = 0.15;
  const padLat = pad;
  const padLng = pad;
  return [
    [Math.min(...lats) - padLat, Math.min(...lngs) - padLng],
    [Math.max(...lats) + padLat, Math.max(...lngs) + padLng],
  ];
}