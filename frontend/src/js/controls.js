// Controls page.
//
// Rendered entirely from GET /controls, so adding a backend control needs no
// change here. Every write shows the read-back, and a value the firmware
// clamped is reported as a warning rather than silently accepted.

import { get, post } from "./api.js";
import { badge, esc } from "./fmt.js";

const $ = (id) => document.getElementById(id);
let state = {};

function feedback(id, tone, message) {
  const el = document.querySelector(`[data-feedback="${CSS.escape(id)}"]`);
  if (!el) return;
  const colour = { ok: "text-ok-300", warn: "text-warn-300", crit: "text-crit-300" }[tone];
  el.innerHTML = `<span class="${colour}">${esc(message)}</span>`;
}

/** Report the read-back; `verified:false` means firmware applied something else. */
function reportWrite(id, data) {
  if (data.verified) {
    feedback(id, "ok", `applied: ${JSON.stringify(data.applied)}`);
  } else {
    feedback(id, "warn",
      `requested ${JSON.stringify(data.requested)} but hardware holds ` +
      `${JSON.stringify(data.applied)} — the firmware clamped this.`);
  }
}

async function write(id, path, body) {
  feedback(id, "ok", "applying…");
  try {
    const { data } = await post(path, body);
    reportWrite(id, data);
    await refresh();
  } catch (error) {
    feedback(id, error.status === 200 ? "warn" : "crit",
             `${error.message}${error.hint ? ` — ${error.hint}` : ""}`);
  }
}

/** Two-step write: fetch a token showing current vs proposed, then confirm. */
async function writeWithConfirm(id, path, controlId, value, body) {
  try {
    const { data: token } = await post("/controls/confirm", {
      control_id: controlId, value,
    });
    const proceed = window.confirm(
      `${token.warning}\n\n` +
      `Current:  ${JSON.stringify(token.current_value)}\n` +
      `Proposed: ${JSON.stringify(token.proposed_value)}\n\n` +
      `This confirmation expires in ${token.expires_in_s}s.`
    );
    if (!proceed) { feedback(id, "warn", "cancelled"); return; }
    await write(id, path, { ...body, confirm_token: token.token });
  } catch (error) {
    feedback(id, "crit", error.message);
  }
}

function controlShell(id, title, note, body, { writable, reason }) {
  return `
    <div class="card mb-4">
      <div class="card-head">
        <span class="card-title">${esc(title)}</span>
        ${writable ? badge("ok", "writable") : badge("idle", "read-only")}
      </div>
      <div class="card-body">
        ${note ? `<p class="text-xs text-ink-400 mb-3">${esc(note)}</p>` : ""}
        ${!writable && reason
          ? `<p class="text-xs text-warn-300 mb-3">${esc(reason)}</p>` : ""}
        <div class="${writable ? "" : "opacity-50 pointer-events-none"}">${body}</div>
        <div class="mt-2 text-xs" data-feedback="${esc(id)}"></div>
      </div>
    </div>`;
}

function choiceButtons(id, value, choices) {
  return `<div class="flex gap-2 flex-wrap">${choices.map((c) => `
      <button class="btn ${c === value ? "border-accent-500 text-accent-300" : ""}"
              data-choice="${esc(id)}" data-value="${esc(c)}">${esc(c)}</button>`).join("")}</div>`;
}

function slider(id, value, min, max, unit = "") {
  return `<div class="flex items-center gap-3">
      <input type="range" min="${min}" max="${max}" value="${value ?? min}"
             data-slider="${esc(id)}" class="grow accent-accent-500">
      <output class="text-sm tabular w-16 text-right" data-out="${esc(id)}">${value}${unit}</output>
      <button class="btn" data-apply="${esc(id)}">Apply</button>
    </div>`;
}

function fanCurveTable(fan) {
  const rows = fan.points.map((p) => `
      <tr>
        <td class="pr-2 text-ink-500 text-xs">${p.point}</td>
        <td class="pr-2"><input type="number" class="w-16 bg-ink-800 border border-ink-600
              rounded px-1.5 py-0.5 text-xs tabular" value="${p.temp}"
              data-curve="${fan.index}" data-field="temp" data-point="${p.point}"></td>
        <td><input type="number" class="w-16 bg-ink-800 border border-ink-600
              rounded px-1.5 py-0.5 text-xs tabular" value="${p.pwm}"
              data-curve="${fan.index}" data-field="pwm" data-point="${p.point}"></td>
      </tr>`).join("");

  return `<div class="mb-3">
      <div class="text-xs text-ink-300 mb-1">Fan ${fan.index}
        <span class="text-ink-500">(enable mode ${fan.enable})</span></div>
      <table class="text-xs"><thead><tr class="text-ink-500">
        <th class="pr-2 text-left font-normal">#</th>
        <th class="pr-2 text-left font-normal">°C</th>
        <th class="text-left font-normal">PWM</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <button class="btn mt-2 text-xs" data-curve-apply="${fan.index}">Apply fan ${fan.index} curve</button>
    </div>`;
}

function render() {
  const c = state;
  const panel = $("controls-panel");

  const tuned = c.tuned || {};
  const tunedBody = tuned.available
    ? (tuned.daemon_active
        ? choiceButtons("tuned", tuned.active_profile, tuned.profiles.slice(0, 12))
        : `<p class="text-xs text-warn-300">Daemon stopped — showing the
             <strong>preset</strong> profile
             <code>${esc(tuned.active_profile || "?")}</code>.
             Start it with <code>sudo systemctl start tuned</code>.</p>`)
    : `<p class="text-xs text-ink-400">tuned is not installed.</p>`;

  const fan = c.fan_curve || {};
  const fanWritable = (fan.fans || []).some((f) => f.writable);

  // These controls drive the asus-nb-wmi platform driver and the
  // asus_custom_fan_curve hwmon device. If neither is present we are not on
  // supported hardware, and saying so is more useful than a page of disabled
  // widgets with no explanation.
  const chassisPresent = Boolean(fan.available || c.platform_profile?.value);

  panel.innerHTML = `
    <div class="mb-4 rounded-md border px-4 py-2.5 text-xs
                ${chassisPresent
                  ? "border-ink-700 bg-ink-900 text-ink-400"
                  : "border-warn-500/40 bg-warn-500/10 text-warn-300"}">
      ${chassisPresent ? "&#8505;" : "&#9888;"}
      Hardware controls require an <strong>ASUS ROG Flow Z13 (GZ302EA)</strong>
      and the <code>asus-nb-wmi</code> platform driver.
      ${chassisPresent
        ? "Detected — controls below act on real hardware."
        : "Not detected: these controls are unavailable on this machine. Telemetry, snapshots and requirements are unaffected."}
    </div>

    <div class="grid gap-4 items-start"
         style="grid-template-columns:repeat(auto-fit,minmax(400px,1fr))">
      <div>
        ${controlShell("platform-profile", "Performance profile",
            "ACPI platform profile — affects the whole system's power/thermal balance.",
            choiceButtons("platform-profile", c.platform_profile?.value,
                          c.platform_profile?.choices || []),
            { writable: c.platform_profile?.writable })}

        ${controlShell("npu-pmode", "NPU power mode",
            "Applied via xrt-smi through a scoped sudo rule.",
            choiceButtons("npu-pmode", c.npu_pmode?.value, c.npu_pmode?.choices || []),
            { writable: c.npu_pmode?.writable })}

        ${controlShell("tuned", "TuneD profile",
            "System tuning profiles (first 12 of " + (tuned.profiles?.length || 0) + ").",
            tunedBody,
            { writable: Boolean(tuned.daemon_active) })}
      </div>

      <div>
        ${controlShell("battery-limit", "Battery charge limit",
            "Caps charging to extend battery lifespan.",
            slider("battery-limit", c.battery_limit?.value,
                   c.battery_limit?.min, c.battery_limit?.max, "%"),
            { writable: c.battery_limit?.writable })}

        ${controlShell("kbd-backlight", "Keyboard backlight",
            c.kbd_backlight?.note,
            slider("kbd-backlight", c.kbd_backlight?.value,
                   c.kbd_backlight?.min, c.kbd_backlight?.max),
            { writable: c.kbd_backlight?.writable })}

        ${controlShell("ppt", "Power limits (TDP)",
            "Live values from the ASUS platform driver.",
            `<div class="grid grid-cols-2 gap-1 text-xs font-mono">
               ${Object.entries(c.ppt?.values || {}).map(([k, v]) =>
                 `<span class="text-ink-400">${esc(k)}</span>
                  <span class="text-ink-200 tabular">${esc(v)}</span>`).join("")}
             </div>`,
            { writable: false, reason: c.ppt?.reason })}
      </div>

      <div>
        ${controlShell("fan-curve", "Fan curves",
            "Eight points per fan. Temperatures and PWM must both be non-decreasing.",
            (fan.fans || []).map(fanCurveTable).join("") ||
              `<p class="text-xs text-ink-400">No fan-curve device.</p>`,
            { writable: fanWritable, reason: fan.reason })}
      </div>
    </div>`;

  wire();
}

function wire() {
  document.querySelectorAll("[data-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.choice;
      const value = button.dataset.value;
      const paths = {
        "platform-profile": "/controls/platform-profile",
        "npu-pmode": "/controls/npu-pmode",
        tuned: "/controls/tuned",
      };
      write(id, paths[id], { value });
    });
  });

  document.querySelectorAll("[data-slider]").forEach((input) => {
    const out = document.querySelector(`[data-out="${CSS.escape(input.dataset.slider)}"]`);
    input.addEventListener("input", () => { out.textContent = input.value; });
  });

  document.querySelectorAll("[data-apply]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.apply;
      const input = document.querySelector(`[data-slider="${CSS.escape(id)}"]`);
      write(id, `/controls/${id}`, { value: Number(input.value) });
    });
  });

  document.querySelectorAll("[data-curve-apply]").forEach((button) => {
    button.addEventListener("click", () => {
      const fan = Number(button.dataset.curveApply);
      const points = [...document.querySelectorAll(`[data-curve="${fan}"][data-field="temp"]`)]
        .map((tempInput) => {
          const point = tempInput.dataset.point;
          const pwmInput = document.querySelector(
            `[data-curve="${fan}"][data-field="pwm"][data-point="${point}"]`);
          return { temp: Number(tempInput.value), pwm: Number(pwmInput.value) };
        });
      writeWithConfirm("fan-curve", "/controls/fan-curve", "fan-curve",
                       { fan, points }, { fan, points });
    });
  });
}

async function refresh() {
  const { data } = await get("/controls");
  state = data;
}

export async function initControls() {
  const panel = $("controls-panel");
  panel.innerHTML = `<p class="text-sm text-ink-400">Reading hardware state…</p>`;
  try {
    await refresh();
    render();
  } catch (error) {
    panel.innerHTML = `<p class="text-sm text-crit-300">${esc(error.message)}</p>`;
  }
}
