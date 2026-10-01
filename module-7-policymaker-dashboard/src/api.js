/**
 * API client for the Niti-Setu Policymaker Dashboard.
 *
 * Talks to Module 6's FastAPI service on port 8006. The base URL comes from
 * `VITE_API_BASE_URL` so the dashboard can point at any instance.
 *
 * Errors are separated by kind, because the dashboard has to react differently
 * to each. "Module 6 is not running" and "Module 6 is running but has not
 * scored anything yet" are both failures to a naive `fetch`, but only one of
 * them is something the user can fix by starting a service.
 */

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8006";

/** Error carrying the distinctions the UI needs to explain itself. */
export class ApiError extends Error {
  constructor(message, { status = 0, detail = null, unreachable = false } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.isUnreachable = unreachable;
    // 409 is Module 6 saying "healthy, but no ranking yet" - not an error state
    // in the sense of something being broken.
    this.isNotScored = status === 409;
  }
}

async function getJSON(path, signal) {
  let res;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, { signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(`Cannot reach Module 6 at ${API_BASE_URL}`, {
      unreachable: true,
    });
  }

  if (!res.ok) {
    let detail = null;
    try {
      const body = await res.json();
      detail = body.detail || null;
    } catch {
      // A non-JSON error body (proxy timeout, HTML error page) has no detail,
      // and that is fine - the status code still tells us something useful.
    }
    const message =
      detail ||
      (res.status === 409
        ? "Nothing has been scored yet."
        : `Module 6 returned ${res.status} on ${path}`);
    throw new ApiError(message, { status: res.status, detail });
  }
  return res.json();
}

/** Service health, including whether anything has been scored. */
export function fetchHealth(signal) {
  return getJSON("/health", signal);
}

/** The full ranking, optionally narrowed to one district or band. */
export function fetchPriorities(filters = {}, signal) {
  const params = new URLSearchParams();
  if (filters.district) params.set("district", filters.district);
  if (filters.band) params.set("band", filters.band);
  const query = params.toString();
  return getJSON(`/api/v1/priorities${query ? `?${query}` : ""}`, signal);
}

/** Portfolio totals for the header tiles. */
export function fetchSummary(signal) {
  return getJSON("/api/v1/priorities/summary", signal);
}

/** Funding plan in rank order, with a running cumulative envelope. */
export function fetchPlan(size = 10, signal) {
  return getJSON(`/api/v1/plan?size=${size}`, signal);
}

/** Previous rankings for one hotspot. */
export function fetchHistory(hotspotCode, signal) {
  return getJSON(`/api/v1/priorities/${encodeURIComponent(hotspotCode)}/history`, signal);
}