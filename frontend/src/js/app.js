// Entry point: wires tabs, registers pollers, boots the dashboard.

import { get } from "./api.js";
import { poller } from "./poller.js";
import * as panels from "./panels.js";
import { initSnapshots } from "./snapshots.js";
import { initRequirements } from "./requirements.js";
import { initControls } from "./controls.js";

const $ = (id) => document.getElementById(id);

// Pages whose content is fetched once on first visit rather than polled.
const lazyPages = {
  snapshots: initSnapshots,
  requirements: initRequirements,
  controls: initControls,
};
const loaded = new Set();

function setConnection(state) {
  const el = $("conn");
  if (!el) return;
  const map = {
    live: ["badge badge-ok", "● live"],
    stale: ["badge badge-warn", "● reconnecting"],
    down: ["badge badge-crit", "● offline"],
  };
  const [cls, label] = map[state] || map.down;
  el.className = cls;
  el.textContent = label;
}

/** Wrap a fetch+render pair so a failure renders panel state, not a crash. */
function job(id, page, intervalMs, path, render, targetId, params = {}) {
  return {
    id, page, intervalMs,
    run: async ({ force }) => {
      const { data } = await get(path, force ? { ...params, force: true } : params);
      render(data);
      panels.stamp();
      setConnection("live");
    },
    onError: (error, failures) => {
      panels.renderError(targetId, error);
      // A degraded tool (HTTP 200, ok:false) is not a connectivity problem.
      if (error.status !== 200) setConnection(failures >= 3 ? "down" : "stale");
    },
  };
}

function registerJobs() {
  // Overview -- fast tier (sysfs + psutil, sub-millisecond)
  poller.register(job("host", "overview", 2000, "/telemetry/host", panels.host, "host-panel"));
  poller.register(job("thermal", "overview", 2000, "/telemetry/host", panels.thermal, "thermal-panel"));

  // Overview -- medium tier (subprocess-backed)
  poller.register(job("gpu", "overview", 5000, "/telemetry/gpu", panels.gpu, "gpu-panel"));
  poller.register(job("memory", "overview", 5000, "/telemetry/memory", panels.memory, "memory-panel"));
  poller.register(job("npu", "overview", 30000, "/telemetry/flm/validate", panels.npu, "npu-panel"));
  poller.register(job("caps", "overview", 60000, "/capabilities", panels.capabilities, "capabilities-panel"));

}

function showPage(page) {
  document.querySelectorAll("section[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.page === page;
    tab.classList.toggle("tab-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  history.replaceState(null, "", `#${page}`);
  poller.setPage(page);

  if (lazyPages[page] && !loaded.has(page)) {
    loaded.add(page);
    lazyPages[page]().catch((error) => console.error(`${page} init failed`, error));
  }
}

function main() {
  registerJobs();

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showPage(tab.dataset.page));
  });

  $("refresh")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await poller.refreshNow(true);
    } finally {
      button.disabled = false;
    }
  });

  const initial = (location.hash || "#overview").slice(1);
  const known = [...document.querySelectorAll("section[data-page]")].map((s) => s.dataset.page);
  showPage(known.includes(initial) ? initial : "overview");
}

main();
