// Panel renderers. Each takes fetched data and returns HTML; none of them
// fetch, so they can be reasoned about (and eventually tested) in isolation.

import { badge, bytes, clock, duration, esc, kv, loadTone, meter, num, pct, text } from "./fmt.js";

const $ = (id) => document.getElementById(id);

function setBadge(id, tone, label) {
  const el = $(id);
  if (el) el.outerHTML = badge(tone, label).replace("<span", `<span id="${id}"`);
}

/** Render an ApiError as panel state rather than an error message. */
export function renderError(targetId, error) {
  const degraded = error.status === 200;
  const tone = degraded ? "warn" : "crit";
  const el = $(targetId);
  if (!el) return;
  el.innerHTML = `
    <div class="flex flex-col gap-2">
      <div>${badge(tone, error.code)}</div>
      <p class="text-sm text-ink-300">${esc(error.message)}</p>
      ${error.hint ? `<p class="text-xs text-ink-400">${esc(error.hint)}</p>` : ""}
      ${error.installCommand
        ? `<code class="text-xs bg-ink-950 border border-ink-700 rounded px-2 py-1
             font-mono text-ink-300 break-all">${esc(error.installCommand)}</code>`
        : ""}
    </div>`;
}

export function npu(data) {
  const failed = data.failed_checks || [];
  const tone = data.ready ? "ok" : failed.length ? "crit" : "warn";
  setBadge("npu-badge", tone, data.ready ? "ready" : "not ready");

  const device = (data.devices || [])[0] || {};
  const checks = (data.checks || []).map((c) => {
    const t = c.ok ? "ok" : "crit";
    return `<div class="flex items-center justify-between gap-3 py-1
                 border-b border-ink-800/60 last:border-0">
              <span class="text-sm text-ink-300">${esc(c.id)}</span>
              <span class="flex items-center gap-2">
                <span class="text-xs font-mono text-ink-400">${esc(c.value ?? "—")}</span>
                ${badge(t, c.ok ? "ok" : "fail")}
              </span>
            </div>`;
  }).join("");

  $("npu-panel").innerHTML = `
    ${kv("Device", device.device ?? "—", { mono: true })}
    ${kv("Columns", device.columns ?? "—")}
    ${kv("Firmware", device.firmware_version ?? "—", { mono: true })}
    ${kv("amdxdna", data.amdxdna_version ?? "—", { mono: true })}
    ${kv("memlock", data.memlock_limit ?? "—", { mono: true })}
    <div class="mt-3 pt-2 border-t border-ink-700">${checks}</div>`;
}

export function memory(data) {
  const p = data.primary || {};
  const s = data.secondary || {};
  const t = data.ttm || {};
  const h = data.host || {};
  setBadge("mem-badge", loadTone(p.percent), pct(p.percent));

  $("memory-panel").innerHTML = `
    <div class="mb-3">
      <div class="flex items-baseline justify-between mb-1">
        <span class="text-sm font-medium text-ink-100">${esc(p.label || "Unified")}</span>
        <span class="text-sm tabular text-ink-300">
          ${bytes(p.used)} / ${bytes(p.total)} &middot; ${pct(p.percent)}</span>
      </div>
      ${meter(p.percent)}
      <p class="text-[0.68rem] text-ink-500 mt-1">${esc(p.note || "")}</p>
    </div>

    <div class="mb-3">
      <div class="flex items-baseline justify-between mb-1">
        <span class="text-sm text-ink-300">${esc(s.label || "Carveout")}</span>
        <span class="text-sm tabular text-ink-400">
          ${bytes(s.used)} / ${bytes(s.total)} &middot; ${pct(s.percent)}</span>
      </div>
      ${meter(s.percent, "idle")}
      <p class="text-[0.68rem] text-ink-500 mt-1">${esc(s.note || "")}</p>
    </div>

    ${kv("TTM pages limit", `${num(t.pages_limit)} (${num(t.limit_gb, " GB", 2)})`)}
    ${kv("System RAM", `${bytes(h.used)} / ${bytes(h.total)}`)}`;
}

export function gpu(data) {
  setBadge("gpu-badge", loadTone(data.gpu_percent), pct(data.gpu_percent, 0));
  $("gpu-panel").innerHTML = `
    <div class="mb-3">
      <div class="flex items-baseline justify-between mb-1">
        <span class="text-sm text-ink-300">Utilisation</span>
        <span class="text-sm tabular">${pct(data.gpu_percent, 0)}</span>
      </div>
      ${meter(data.gpu_percent)}
    </div>
    ${kv("Temperature", num(data.temperature_c, " °C", 1))}
    ${kv("Package power", num(data.power_w, " W", 2))}
    ${kv("GTT used", bytes(data.gtt_used))}
    ${kv("VRAM carveout used", bytes(data.vram_used))}`;
}

export function host(payload) {
  const s = payload.static || {};
  const l = payload.live || {};
  setBadge("host-badge", loadTone(l.cpu_percent), pct(l.cpu_percent, 0));

  const hostName = $("host-name");
  if (hostName) hostName.textContent = s.hostname || "";
  const os = $("foot-os");
  if (os) os.textContent = s.os_pretty || "";
  const kernel = $("foot-kernel");
  if (kernel) kernel.textContent = s.kernel || "";

  const battery = l.battery
    ? `${pct(l.battery.percent, 0)}${l.battery.plugged ? " (AC)" : " (battery)"}`
    : "—";

  $("host-panel").innerHTML = `
    <div class="mb-3">
      <div class="flex items-baseline justify-between mb-1">
        <span class="text-sm text-ink-300">CPU</span>
        <span class="text-sm tabular">${pct(l.cpu_percent, 0)} &middot; ${num(l.cpu_freq_mhz, " MHz")}</span>
      </div>
      ${meter(l.cpu_percent)}
    </div>
    ${kv("Load", `${l.load?.["1m"]} / ${l.load?.["5m"]} / ${l.load?.["15m"]}`)}
    ${kv("Memory", `${bytes(l.memory?.used)} / ${bytes(l.memory?.total)}`)}
    ${kv("Uptime", duration(l.uptime_s))}
    ${kv("Battery", battery)}
    ${kv("Secure Boot", text(l.secure_boot))}
    ${kv("Model", text(s.product))}`;
}

export function thermal(payload) {
  const l = payload.live || {};
  const fans = (payload.fans || {}).fans || [];

  const fanRows = fans.length
    ? fans.map((f) => kv(f.label, `${num(f.rpm)} rpm`)).join("")
    : `<p class="text-sm text-ink-500">No fan sensors detected.</p>`;

  const temps = Object.entries(l.temperatures || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([label, value]) => kv(label, num(value, " °C", 1)))
    .join("");

  $("thermal-panel").innerHTML = fanRows +
    `<div class="mt-2 pt-2 border-t border-ink-700">${temps}</div>`;
}

export function capabilities(data) {
  const degraded = data.summary?.degraded || [];
  setBadge("cap-badge", degraded.length ? "warn" : "ok",
           `${data.summary.tools_available}/${data.summary.tools_total} tools`);

  const rows = Object.entries(data.features || {}).map(([name, cap]) => {
    const tone = cap.available ? "ok" : "warn";
    return `<div class="flex items-start justify-between gap-3 py-1
                 border-b border-ink-800/60 last:border-0">
              <div class="min-w-0">
                <div class="text-sm text-ink-200">${esc(name)}</div>
                ${cap.reason ? `<div class="text-[0.68rem] text-ink-500">${esc(cap.reason)}</div>` : ""}
              </div>
              ${badge(tone, cap.available ? "yes" : "no")}
            </div>`;
  }).join("");

  $("capabilities-panel").innerHTML = rows;

  // Surface degraded prerequisites at the top of the app, not just this card.
  const bar = $("alert-bar");
  if (bar) {
    if (degraded.length) {
      bar.classList.remove("hidden");
      bar.innerHTML = `<span>⚠</span><span>Degraded: ` +
        `<strong>${degraded.map(esc).join(", ")}</strong>` +
        ` — see the Capabilities card for details.</span>`;
    } else {
      bar.classList.add("hidden");
    }
  }
}

export function stamp() {
  const el = $("foot-updated");
  if (el) el.textContent = `updated ${clock()}`;
}
