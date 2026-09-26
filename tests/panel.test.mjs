import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const panelPath = fileURLToPath(new URL("../custom_components/daylight/panel/daylight-curve-preview-panel.js", import.meta.url));

class Element {
  constructor(tag = "element") {
    this.tag = tag;
    this.children = [];
    this.value = "";
    this.textContent = "";
    this.attributes = {};
    this.listeners = {};
    this.nodes = {};
  }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) {
    this.children = children;
    if (this.tag === "select") this.value = children[0]?.value || "";
  }
  querySelector(selector) {
    return this.nodes[selector] ||= new Element(selector === "#target" ? "select" : selector);
  }
}

function createPanel() {
  let Panel;
  vm.runInNewContext(readFileSync(panelPath, "utf8"), {
    HTMLElement: Element,
    ResizeObserver: class { observe() {} disconnect() {} },
    customElements: { define: (_name, component) => { Panel = component; } },
    document: { createElement: (tag) => new Element(tag), createElementNS: (_ns, tag) => new Element(tag) },
  });
  const panel = new Panel();
  panel.connectedCallback();
  panel._targets = [{ entry_id: "hub", target_id: "target", name: "Kitchen" }];
  panel._target.value = "hub:target";
  panel._date.value = "2026-06-21";
  return panel;
}

function sample() {
  const times = {
    morning_start: "2026-06-21T06:00:00Z", morning_end: "2026-06-21T08:00:00Z",
    evening_start: "2026-06-21T16:00:00Z", evening_end: "2026-06-21T18:00:00Z"
  };
  return {
    points: [
      { utc_time: "2026-06-21T00:00:00Z", brightness_pct: 10, color_temp_kelvin: 2500 },
      { utc_time: "2026-06-22T00:00:00Z", brightness_pct: 10, color_temp_kelvin: 2500 }
    ],
    sunrise: null, sunset: null, timezone: "America/New_York",
    ranges: { min_color_temp_kelvin: 2500, max_color_temp_kelvin: 4000 },
    timing: { brightness: times, color: times, compressed: false, polar_fallback: false, custom: false }
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("sampling sends only saved IDs and the selected date; inspection never sends commands", async () => {
  const panel = createPanel();
  const calls = [];
  panel._hass = { callApi: async (...args) => { calls.push(args); return sample(); } };
  await panel._loadCurve();
  assert.equal(calls[0][0], "POST");
  assert.equal(calls[0][1], "daylight/sample_curve");
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][2])), { entry_id: "hub", target_id: "target", date: "2026-06-21" });
  assert.ok(panel._chart.children.length);
  assert.equal(panel._values.children.length, 2);
  assert.match(panel._readout.textContent, /10%/);
  panel._inspect.value = 1;
  panel._inspect.listeners.input();
  assert.equal(calls.length, 1);
});

test("chart coordinates resize without scaling phone labels to unreadable sizes", async () => {
  const panel = createPanel();
  panel._chart.clientWidth = 360;
  panel._hass = { callApi: async () => sample() };
  await panel._loadCurve();
  assert.equal(panel._chart.attributes.viewBox, "0 0 360 360");
  panel._chart.clientWidth = 900;
  panel._draw();
  assert.equal(panel._chart.attributes.viewBox, "0 0 900 360");
});

test("failed sampling clears the previous chart, table and readout", async () => {
  const panel = createPanel();
  let fail = false;
  panel._hass = { callApi: async () => { if (fail) throw new Error("Schedule conflict"); return sample(); } };
  await panel._loadCurve();
  fail = true;
  await panel._loadCurve();
  panel._draw();
  assert.equal(panel._data, null);
  assert.equal(panel._chart.children.length, 0);
  assert.equal(panel._values.children.length, 0);
  assert.equal(panel._readout.textContent, "");
  assert.equal(panel._status.textContent, "Schedule conflict");
  assert.equal(panel._inspect.disabled, true);
});

test("stale responses cannot replace the latest target/date", async () => {
  const panel = createPanel();
  const old = deferred();
  let count = 0;
  panel._hass = { callApi: () => ++count === 1 ? old.promise : Promise.resolve(sample()) };
  const pending = panel._loadCurve();
  panel._date.value = "2026-06-22";
  await panel._loadCurve();
  const current = panel._data;
  old.resolve({ ...sample(), timezone: "UTC" });
  await pending;
  assert.equal(panel._data, current);
});

test("disconnect invalidates pending responses and reconnect refreshes saved state", async () => {
  const panel = createPanel();
  const request = deferred();
  panel._hass = { callApi: () => request.promise };
  const pending = panel._loadCurve();
  panel.disconnectedCallback();
  request.resolve(sample());
  await pending;
  assert.equal(panel._data, null);
  let refreshes = 0;
  panel._refresh = () => refreshes++;
  panel.connectedCallback();
  assert.equal(refreshes, 1);
});

test("empty catalog gives setup guidance without requesting a curve", async () => {
  const panel = createPanel();
  const calls = [];
  panel._hass = { callApi: async (...args) => { calls.push(args); return { targets: [], timezone: "UTC", today: "2026-06-21" }; } };
  await panel._refresh();
  assert.equal(calls.length, 1);
  assert.match(panel._status.textContent, /No saved Daylight targets/);
  assert.equal(panel._target.disabled, true);
});

test("refresh loads saved targets and uses the HA date and timezone", async () => {
  const panel = createPanel();
  panel._date.value = "";
  const calls = [];
  panel._hass = { callApi: async (method, path, payload) => {
    calls.push({ method, path, payload });
    return method === "GET" ? { targets: panel._targets, today: "2026-06-20", timezone: "America/New_York" } : sample();
  } };
  await panel._refresh();
  assert.equal(panel._date.value, "2026-06-20");
  assert.equal(calls[1].payload.date, "2026-06-20");
  assert.match(panel._zone.textContent, /America\/New_York/);
  // UTC midnight on June 21 is 8 p.m. June 20 in New York, not browser time.
  assert.match(panel._time("2026-06-21T00:00:00Z", true), /08:00|20:00/);
});

test("polar fallback and automatic fitting are explained without fake sun markers", async () => {
  const panel = createPanel();
  const response = sample();
  response.timing.polar_fallback = true;
  response.timing.compressed = true;
  panel._hass = { callApi: async () => response };
  await panel._loadCurve();
  assert.match(panel._notes.textContent, /not actual sunrise\/sunset/);
  assert.match(panel._notes.textContent, /shortened/);
  assert.ok(!panel._chart.children.some((node) => node.textContent === "Sunrise"));
});
