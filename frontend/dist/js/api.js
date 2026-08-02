// API wrapper: unwraps the standard envelope and routes writes through the
// CSRF header the backend requires.

const BASE = "/api/v1";
const WRITE_HEADER = "X-Strix-Dash";

export class ApiError extends Error {
  constructor(payload, status) {
    super(payload?.message || "Request failed");
    this.code = payload?.code || "UNKNOWN";
    this.hint = payload?.hint || null;
    this.installCommand = payload?.install_command || null;
    this.detail = payload?.detail || {};
    this.status = status;
  }
}

/** GET an endpoint. Returns {data, meta}.
 *
 *  Degraded-but-understood states (tool missing, daemon down) arrive as HTTP
 *  200 with ok:false; they still throw ApiError so a panel renders its reason,
 *  but they are distinguishable from transport failures by `status === 200`.
 */
export async function get(path, params = {}) {
  const url = new URL(BASE + path, window.location.origin);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, v);
  });

  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => null);

  if (!payload) throw new ApiError({ message: `HTTP ${response.status}` }, response.status);
  if (!payload.ok) throw new ApiError(payload.error, response.status);
  return { data: payload.data, meta: payload.meta || {} };
}

/** POST a state-changing request. */
export async function post(path, body = null) {
  const response = await fetch(BASE + path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      [WRITE_HEADER]: "1", // required; also forces a cross-origin preflight
    },
    body: body === null ? null : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => null);

  if (!payload) throw new ApiError({ message: `HTTP ${response.status}` }, response.status);
  if (!payload.ok) throw new ApiError(payload.error, response.status);
  return { data: payload.data, meta: payload.meta || {} };
}
