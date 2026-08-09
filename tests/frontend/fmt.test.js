// Frontend formatter tests.
//
// Uses node:test, which ships with Node 20 -- no dependency, no npm install.
// These cover fmt.js because it is pure: every function takes data and returns
// a string, so it can be tested without a DOM. The panel renderers touch
// document and are exercised by hand during development instead.
//
//   node --test tests/frontend/

import assert from "node:assert/strict";
import { test } from "node:test";

import { badge, bytes, duration, esc, loadTone, num, pct, text } from
  "../../frontend/src/js/fmt.js";

test("bytes scales into human units", () => {
  assert.equal(bytes(0), "0 B");
  assert.equal(bytes(512), "512 B");
  assert.equal(bytes(1024), "1.0 KB");
  assert.equal(bytes(536870912), "512.0 MB");   // the VRAM carveout
  assert.equal(bytes(115401125888), "107.5 GB"); // the GTT pool
});

test("null and undefined render as a dash, never as 0", () => {
  // A missing reading must not look like a real zero -- "Estimated Power: N/A"
  // on this NPU would otherwise display as a plausible 0 W.
  assert.equal(bytes(null), "—");
  assert.equal(pct(undefined), "—");
  assert.equal(num(null), "—");
  assert.equal(text(""), "—");
  assert.equal(duration(null), "—");
});

test("zero is preserved as a real value", () => {
  assert.equal(pct(0), "0.0%");
  assert.equal(num(0), "0");
  assert.equal(bytes(0), "0 B");
});

test("duration collapses to the two largest units", () => {
  assert.equal(duration(90), "1m");
  assert.equal(duration(3660), "1h 1m");
  assert.equal(duration(90000), "1d 1h");
});

test("esc neutralises markup from tool output", () => {
  // Panel content is interpolated into template literals, so anything a tool
  // prints must not be able to open a tag.
  assert.equal(esc("<script>alert(1)</script>"),
    "&lt;script&gt;alert(1)&lt;/script&gt;");
  assert.equal(esc('a "b" & \'c\''), "a &quot;b&quot; &amp; &#39;c&#39;");
  assert.equal(esc(null), "");
});

test("loadTone escalates only at high utilisation", () => {
  // A busy machine is not a problem; thresholds are deliberately high.
  assert.equal(loadTone(0), "ok");
  assert.equal(loadTone(74), "ok");
  assert.equal(loadTone(75), "warn");
  assert.equal(loadTone(90), "crit");
  assert.equal(loadTone(null), "idle");
});

test("badge always pairs colour with a glyph", () => {
  // Colour alone is not an accessible signal.
  for (const tone of ["ok", "warn", "crit", "idle"]) {
    const html = badge(tone, "state");
    assert.match(html, new RegExp(`badge-${tone}`));
    assert.match(html, /[✓⚠✕•]/, `${tone} badge should carry a glyph`);
  }
});

test("badge escapes its label", () => {
  assert.match(badge("ok", "<b>x</b>"), /&lt;b&gt;x&lt;\/b&gt;/);
});
