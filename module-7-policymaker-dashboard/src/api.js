/**
 * API client for the Niti-Setu Policymaker Dashboard.
 *
 * Talks to Module 6's FastAPI service. The base URL is configurable via the
 * `VITE_API_BASE_URL` environment variable (see .env / .env.example) so the
 * dashboard can point at any running instance without code changes.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://localhost:8006";

async function getJSON(path) {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status} on GET ${path}`);
  }
  return res.json();
}

/** Fetch all hotspots (with priority-factor breakdowns). */
export function fetchHotspots() {
  return getJSON("/hotspots");
}

/** Fetch the top-ranked recommendations (optionally filtered client-side). */
export function fetchRecommendations(limit = 100) {
  return getJSON(`/recommendations?limit=${limit}`);
}

export { API_BASE_URL };