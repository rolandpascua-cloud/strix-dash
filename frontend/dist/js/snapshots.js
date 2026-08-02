// Snapshot auditor page.
//
// Diffs are strictly on-demand: each one reads thousands of dpkg records from
// two admindirs, so it is never attached to a poller.

import { get, post } from "./api.js";
import { badge, esc, num } from "./fmt.js";

const $ = (id) => document.getElementById(id);

let snapshots = [];

function options(selected) {
  return snapshots
    .map((s) => {
      const count = s.package_count === null ? "?" : s.package_count;
      const alias = s.alias_of ? " — alias" : "";
      return `<option value="${esc(s.id)}"${s.id === selected ? " selected" : ""}>` +
             `${esc(s.label)} (${count} pkgs)${alias}</option>`;
    })
    .join("");
}

function summaryRow(summary) {
  const cell = (label, value, tone) => `
    <div class="flex flex-col gap-0.5">
      <span class="text-[0.68rem] uppercase tracking-wide text-ink-500">${label}</span>
      <span class="text-lg font-semibold tabular ${tone}">${num(value)}</span>
    </div>`;
  return `<div class="flex gap-8 flex-wrap mb-4 pb-3 border-b border-ink-700">
      ${cell("Added", summary.added, "text-ok-300")}
      ${cell("Removed", summary.removed, "text-crit-300")}
      ${cell("Changed", summary.changed, "text-warn-300")}
      ${cell("Base total", summary.base_total, "text-ink-300")}
      ${cell("Target total", summary.target_total, "text-ink-300")}
    </div>`;
}

function packageList(title, entries, tone, render) {
  if (!entries.length) {
    return `<div><h3 class="text-sm font-semibold mb-2">${title}
              <span class="text-ink-500 font-normal">— none</span></h3></div>`;
  }
  const rows = entries
    .map((e) => `<div class="flex items-baseline justify-between gap-3 py-0.5 text-xs">
        <span class="font-mono text-ink-200 truncate">${esc(e.package)}</span>
        <span class="font-mono text-ink-500 tabular shrink-0">${render(e)}</span>
      </div>`)
    .join("");
  return `<div class="min-w-0">
      <h3 class="text-sm font-semibold mb-2">${title}
        ${badge(tone, String(entries.length))}</h3>
      <div class="max-h-[26rem] overflow-auto pr-1">${rows}</div>
    </div>`;
}

async function runDiff() {
  const base = $("diff-base").value;
  const target = $("diff-target").value;
  const output = $("diff-output");
  const button = $("diff-run");

  button.disabled = true;
  output.innerHTML = `<p class="text-sm text-ink-400">Comparing…</p>`;
  try {
    const { data } = await get("/snapshots/diff", { base, target });
    output.innerHTML = summaryRow(data.summary) + `
      <div class="grid gap-6" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
        ${packageList("Added", data.added, "ok", (e) => esc(e.version))}
        ${packageList("Removed", data.removed, "crit", (e) => esc(e.version))}
        ${packageList("Changed", data.changed, "warn",
                      (e) => `${esc(e.from)} → ${esc(e.to)}`)}
      </div>`;
  } catch (error) {
    output.innerHTML = `<p class="text-sm text-crit-300">${esc(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function createSnapshot(button) {
  const status = $("create-status");
  // State the retention caveat BEFORE taking it, not after: the platform's own
  // cleanup preserves only factory.snapshot and removes everything else.
  const proceed = window.confirm(
    "Take a read-only btrfs snapshot of the running system?\n\n" +
    "It is named from the current date and time.\n\n" +
    "Retention: the platform's own snapshot cleanup preserves ONLY " +
    "factory.snapshot and deletes every other *.snapshot when it next runs " +
    "(for example during a system update). Treat this as a short-term " +
    "checkpoint, not durable storage."
  );
  if (!proceed) return;

  button.disabled = true;
  status.innerHTML = `<span class="text-ink-400">Creating snapshot…</span>`;
  try {
    const { data } = await post("/snapshots/create");
    status.innerHTML =
      `<span class="text-ok-300">Created <code class="font-mono">${esc(data.created)}</code>` +
      (data.package_count ? ` — ${data.package_count} packages` : "") + `</span>`;
    await initSnapshots();
  } catch (error) {
    status.innerHTML =
      `<span class="${error.status === 200 ? "text-warn-300" : "text-crit-300"}">` +
      `${esc(error.message)}${error.hint ? ` — ${esc(error.hint)}` : ""}</span>`;
  } finally {
    button.disabled = false;
  }
}

export async function initSnapshots() {
  const panel = $("snapshots-panel");
  try {
    const { data } = await get("/snapshots");
    snapshots = data;
  } catch (error) {
    panel.innerHTML = `<p class="text-sm text-crit-300">${esc(error.message)}</p>`;
    return;
  }

  const rows = snapshots
    .map((s) => `<tr class="border-b border-ink-800/60">
        <td class="py-1 pr-4 font-mono text-xs text-ink-200">${esc(s.id)}</td>
        <td class="py-1 pr-4 text-xs text-ink-300">${esc(s.label)}</td>
        <td class="py-1 pr-4 text-xs tabular text-right">${num(s.package_count)}</td>
        <td class="py-1 text-xs">${s.alias_of
            ? badge("idle", `alias of ${s.alias_of}`)
            : badge(s.kind === "live" ? "ok" : "idle", s.kind)}</td>
      </tr>`)
    .join("");

  // Default to the widest interesting comparison: factory vs. now.
  const factory = snapshots.find((s) => s.id.startsWith("factory"))?.id || snapshots[0]?.id;

  panel.innerHTML = `
    <table class="w-full mb-5"><thead>
      <tr class="text-[0.68rem] uppercase tracking-wide text-ink-500 border-b border-ink-700">
        <th class="text-left font-medium py-1 pr-4">Snapshot</th>
        <th class="text-left font-medium py-1 pr-4">Label</th>
        <th class="text-right font-medium py-1 pr-4">Packages</th>
        <th class="text-left font-medium py-1">Kind</th>
      </tr></thead><tbody>${rows}</tbody></table>

    <div class="flex items-center gap-3 flex-wrap mb-4 pb-4 border-b border-ink-700">
      <button id="create-snapshot" class="btn">+ Create snapshot</button>
      <span class="text-[0.68rem] text-ink-500">
        Named from the current date and time. Preserved only until the
        platform's next snapshot cleanup.</span>
      <span id="create-status" class="text-xs ml-auto"></span>
    </div>

    <div class="flex items-end gap-3 flex-wrap mb-4">
      <label class="flex flex-col gap-1">
        <span class="text-[0.68rem] uppercase tracking-wide text-ink-500">Base</span>
        <select id="diff-base" class="bg-ink-800 border border-ink-600 rounded
                px-2 py-1.5 text-sm min-w-[16rem]">${options(factory)}</select>
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-[0.68rem] uppercase tracking-wide text-ink-500">Target</span>
        <select id="diff-target" class="bg-ink-800 border border-ink-600 rounded
                px-2 py-1.5 text-sm min-w-[16rem]">${options("current")}</select>
      </label>
      <button id="diff-run" class="btn">Compare</button>
    </div>

    <div id="diff-output"><p class="text-sm text-ink-500">
      Choose two snapshots and press Compare. Diffs are on-demand — each reads
      the full package database from both snapshots.</p></div>`;

  $("diff-run").addEventListener("click", runDiff);
  $("create-snapshot").addEventListener("click", (e) => createSnapshot(e.currentTarget));
}
