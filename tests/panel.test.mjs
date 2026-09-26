import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const panelPath = fileURLToPath(
  new URL("../custom_components/daylight/panel/daylight-curve-preview-panel.js", import.meta.url)
);

function createPanel(clearTimeout = () => {}) {
  let Panel;
  vm.runInNewContext(readFileSync(panelPath, "utf8"), {
    HTMLElement: class {},
    customElements: { define: (_name, component) => { Panel = component; } },
    window: { devicePixelRatio: 1 },
    clearTimeout,
    console: { error() {}, warn() {} }
  });
  return new Panel();
}

test("failed sample clears cached chart before a resize redraw", async () => {
  const panel = createPanel();
  const calls = { strokes: 0, clears: 0 };
  const context = {
    setTransform() {},
    clearRect() { calls.clears++; },
    save() {},
    restore() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    stroke() { calls.strokes++; },
    fillText() {},
    setLineDash() {},
    arc() {},
    fill() {},
    fillRect() {},
    strokeRect() {},
    measureText() { return { width: 20 }; }
  };
  panel._chartCanvas = {
    width: 600,
    height: 400,
    clientWidth: 600,
    clientHeight: 400,
    getContext: () => context
  };
  panel._chartErrorMessage = { style: {}, textContent: "" };
  panel._lampSwatch = { style: {}, title: "" };
  panel._data = { color_temp: { min_color_temp_kelvin: 2000, max_color_temp_kelvin: 5500 } };

  const payloads = [];
  let fail = false;
  panel._hass = {
    callApi: async (_method, _path, payload) => {
      payloads.push(payload);
      if (fail) throw new Error("sample failed");
      return {
        points: [
          { utc_time: "2026-09-25T00:00:00Z", brightness_pct: 25, color_temp_kelvin: 2000 },
          { utc_time: "2026-09-26T00:00:00Z", brightness_pct: 75, color_temp_kelvin: 5500 }
        ],
        sunrise: null,
        sunset: null
      };
    }
  };

  await panel._fetchAndDrawChart();
  assert.ok(calls.strokes > 0);
  assert.equal(Object.hasOwn(payloads[0], "start"), false);

  fail = true;
  await panel._fetchAndDrawChart();
  const strokesAfterFailure = calls.strokes;
  const clearsAfterFailure = calls.clears;
  panel._resizeCanvas();

  assert.equal(calls.strokes, strokesAfterFailure);
  assert.ok(calls.clears > clearsAfterFailure);
  assert.equal(panel._lastDrawArgs, null);
  assert.equal(panel._points, null);
  assert.equal(panel._lampSwatch.style.backgroundColor, "#ddd");
  assert.equal(panel._chartErrorMessage.textContent, "Error loading curve");
});

test("disconnect stops resize observation and a pending chart refresh", () => {
  const cleared = [];
  const panel = createPanel((timer) => cleared.push(timer));
  const observed = [];
  let disconnects = 0;
  let resizes = 0;
  panel._initialized = true;
  panel._chartCanvas = {};
  panel._chartDebounceTimer = 42;
  panel._resizeObserver = {
    observe: (canvas) => observed.push(canvas),
    disconnect: () => { disconnects++; }
  };
  panel._resizeCanvas = () => { resizes++; };

  panel.disconnectedCallback();
  assert.deepEqual(cleared, [42]);
  assert.equal(disconnects, 1);

  panel.connectedCallback();
  assert.deepEqual(observed, [panel._chartCanvas]);
  assert.equal(resizes, 1);
});
