// Formatting helpers. Kept dependency-free and pure so panels stay declarative.

export function bytes(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = Number(value);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${units[i]}`;
}

export function pct(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(digits)}%`;
}

export function num(value, suffix = "", digits = 0) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

export function text(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export function clock(date = new Date()) {
  return date.toLocaleTimeString(undefined, { hour12: false });
}

// Escape before interpolating anything that came from a tool's stdout.
export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

export function kv(key, value, { mono = false } = {}) {
  return `<div class="kv"><span class="kv-k">${esc(key)}</span>` +
         `<span class="kv-v${mono ? " font-mono" : ""}">${esc(value)}</span></div>`;
}

/** Severity for a utilisation percentage. Thresholds are deliberately high --
 *  a busy machine is not a problem. */
export function loadTone(percentage) {
  if (percentage === null || percentage === undefined) return "idle";
  if (percentage >= 90) return "crit";
  if (percentage >= 75) return "warn";
  return "ok";
}

export function meter(percentage, tone = null) {
  const p = Math.max(0, Math.min(100, Number(percentage) || 0));
  const t = tone || loadTone(p);
  const colour = { ok: "bg-ok-500", warn: "bg-warn-500", crit: "bg-crit-500", idle: "bg-ink-600" }[t];
  return `<div class="meter"><div class="meter-fill ${colour}" style="width:${p}%"></div></div>`;
}

const GLYPH = { ok: "✓", warn: "⚠", crit: "✕", idle: "•" };

/** Status chip. Colour is never the only signal -- each tone carries a glyph. */
export function badge(tone, label) {
  return `<span class="badge badge-${tone}">${GLYPH[tone] || ""} ${esc(label)}</span>`;
}
