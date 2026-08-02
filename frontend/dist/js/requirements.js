// Requirements page: what's needed, what's missing, what can be updated.
//
// Detection is local and instant. Checking upstream for a newer release is a
// per-item button -- never automatic, and there is deliberately no "check all".

import { get, post } from "./api.js";
import { badge, bytes, esc } from "./fmt.js";

const $ = (id) => document.getElementById(id);

const TONE = {
  satisfied: "ok",
  outdated: "warn",
  degraded: "warn",
  "local-only": "warn",
  missing: "crit",
  unknown: "idle",
};

const LABEL = {
  satisfied: "satisfied",
  outdated: "update available",
  degraded: "degraded",
  "local-only": "local only",
  missing: "missing",
  unknown: "unknown",
};

function row(item) {
  const tone = item.optional && item.status === "missing" ? "idle" : TONE[item.status];
  const canCheck = item.source_kind === "github-release";

  return `
    <div class="py-3 border-b border-ink-800/60 last:border-0" data-req="${esc(item.id)}">
      <div class="flex items-start justify-between gap-4 flex-wrap">
        <div class="min-w-0 grow">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm font-semibold text-ink-100">${esc(item.name)}</span>
            ${badge(tone, LABEL[item.status] || item.status)}
            ${item.optional ? `<span class="text-[0.68rem] text-ink-500">optional</span>` : ""}
          </div>
          <p class="text-xs text-ink-400 mt-0.5">${esc(item.summary)}</p>
          <p class="text-[0.68rem] text-ink-500 mt-0.5">
            Needed for: ${item.required_for.map(esc).join(", ")}</p>
          ${item.detail?.note
            ? `<p class="text-[0.68rem] text-warn-300 mt-1">${esc(item.detail.note)}</p>` : ""}
          ${item.remediation
            ? `<p class="text-[0.68rem] text-ink-400 mt-1">${esc(item.remediation)}</p>` : ""}
        </div>

        <div class="flex items-center gap-3 shrink-0">
          <div class="text-right">
            <div class="text-xs font-mono text-ink-200">${esc(item.installed_version || "—")}</div>
            <div class="text-[0.68rem] text-ink-500">installed</div>
          </div>
          ${canCheck
            ? `<button class="btn text-xs" data-check="${esc(item.id)}">Check for updates</button>`
            : ""}
          ${canCheck && ["missing", "outdated"].includes(item.status)
            ? `<button class="btn text-xs border-accent-500 text-accent-300"
                       data-install="${esc(item.id)}">Install</button>`
            : ""}
        </div>
      </div>
      <div class="mt-2 text-xs" data-result="${esc(item.id)}"></div>
    </div>`;
}

/** Two-step install: preview the exact artefact, then confirm. */
async function installOne(id, button) {
  const target = document.querySelector(`[data-result="${CSS.escape(id)}"]`);
  button.disabled = true;
  target.innerHTML = `<span class="text-ink-400">Resolving release…</span>`;

  try {
    const { data: plan } = await post(`/requirements/${encodeURIComponent(id)}/preview`);
    const asset = plan.asset;

    // The user approves a named file of a known size from a named source --
    // never a vague "update".
    const proceed = window.confirm(
      `${plan.warning}\n\n` +
      `File:      ${asset.name}\n` +
      `Size:      ${asset.size.toLocaleString()} bytes\n` +
      `Source:    github.com/${plan.repo}\n` +
      `Version:   ${plan.installed_version || "not installed"} -> ${plan.latest_version}\n` +
      `Digest:    ${asset.digest || "none"}\n\n` +
      `Install now?`
    );
    if (!proceed) { target.innerHTML = `<span class="text-ink-400">Cancelled.</span>`; return; }

    target.innerHTML = `<span class="text-ink-400">Downloading, verifying and installing…</span>`;
    const { data } = await post(`/requirements/${encodeURIComponent(id)}/install`,
                                { confirm_token: plan.token });
    target.innerHTML =
      `<span class="text-ok-300">Installed ${esc(data.version || "")} `
      + `(${esc(data.asset || "")})</span>`;
    await initRequirements();
  } catch (error) {
    target.innerHTML =
      `<span class="${error.status === 200 ? "text-warn-300" : "text-crit-300"}">` +
      `${esc(error.message)}${error.hint ? ` — ${esc(error.hint)}` : ""}</span>`;
  } finally {
    button.disabled = false;
  }
}

async function checkOne(id, button) {
  const target = document.querySelector(`[data-result="${CSS.escape(id)}"]`);
  button.disabled = true;
  target.innerHTML = `<span class="text-ink-400">Checking upstream…</span>`;

  try {
    const { data } = await post(`/requirements/${encodeURIComponent(id)}/check`);
    const asset = data.asset;
    const installed = document
      .querySelector(`[data-req="${CSS.escape(id)}"] .font-mono`)
      ?.textContent?.trim();

    const current = installed && data.latest_version === installed;
    target.innerHTML = `
      <div class="flex flex-col gap-1">
        <div class="flex items-center gap-2 flex-wrap">
          ${badge(current ? "ok" : "warn",
                  current ? `already at ${data.latest_version}` : `latest ${data.latest_version}`)}
          <span class="text-ink-500">for ${esc(data.distro_tag)}</span>
        </div>
        ${asset
          ? `<div class="font-mono text-[0.68rem] text-ink-400 break-all">
               ${esc(asset.name)} · ${bytes(asset.size)}
               ${asset.digest ? `<br>${esc(asset.digest)}` : ""}
             </div>`
          : `<div class="text-crit-300">No asset matching ${esc(data.distro_tag)} in this release.</div>`}
      </div>`;
  } catch (error) {
    const degraded = error.status === 200;
    target.innerHTML =
      `<span class="${degraded ? "text-ink-400" : "text-crit-300"}">` +
      `${esc(error.message)}${error.hint ? ` — ${esc(error.hint)}` : ""}</span>`;
  } finally {
    button.disabled = false;
  }
}

export async function initRequirements() {
  const panel = $("requirements-panel");
  panel.innerHTML = `<p class="text-sm text-ink-400">Detecting…</p>`;

  let data;
  try {
    ({ data } = await get("/requirements"));
  } catch (error) {
    panel.innerHTML = `<p class="text-sm text-crit-300">${esc(error.message)}</p>`;
    return;
  }

  const s = data.summary;
  panel.innerHTML = `
    <div class="flex items-center gap-4 flex-wrap mb-4 pb-3 border-b border-ink-700">
      ${badge(s.needs_attention ? "warn" : "ok",
              `${s.satisfied}/${s.total} satisfied`)}
      ${s.needs_attention
        ? `<span class="text-xs text-warn-300">Needs attention:
             ${s.blocking.map(esc).join(", ")}</span>`
        : `<span class="text-xs text-ink-400">All required prerequisites are met.</span>`}
      <span class="ml-auto text-[0.68rem] text-ink-500 font-mono">
        release assets resolved for <strong>${esc(data.distro_tag)}</strong></span>
    </div>
    ${data.items.map(row).join("")}`;

  panel.querySelectorAll("[data-check]").forEach((button) => {
    button.addEventListener("click", () => checkOne(button.dataset.check, button));
  });
  panel.querySelectorAll("[data-install]").forEach((button) => {
    button.addEventListener("click", () => installOne(button.dataset.install, button));
  });
}
