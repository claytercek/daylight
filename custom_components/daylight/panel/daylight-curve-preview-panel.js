// Read-only: requests contain saved hub/target IDs and a date, never settings.
const SVG_NS = "http://www.w3.org/2000/svg";
function svgElement(tag, attributes, text) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text !== undefined) element.textContent = text;
  return element;
}

class DaylightCurvePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._initialized = false;
    this._connected = false;
    this._catalogId = 0;
    this._requestId = 0;
    this._targets = [];
    this._data = null;
  }

  connectedCallback() {
    this._connected = true;
    if (!this._initialized) {
      this.innerHTML = `
        <style>
          daylight-curve-preview-panel { display:block; color:var(--primary-text-color); }
          daylight-curve-preview-panel main { max-width:1100px; margin:auto; padding:24px; }
          daylight-curve-preview-panel .controls { display:flex; flex-wrap:wrap; align-items:end; gap:16px; }
          daylight-curve-preview-panel label { display:flex; flex-direction:column; gap:6px; }
          daylight-curve-preview-panel input, daylight-curve-preview-panel select,
          daylight-curve-preview-panel button { font:inherit; padding:8px; color:inherit;
            background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:6px; }
          daylight-curve-preview-panel a { color:var(--primary-color); }
          daylight-curve-preview-panel svg { display:block; width:100%; height:auto; margin-top:24px; }
          daylight-curve-preview-panel .legend { display:flex; flex-wrap:wrap; gap:24px; }
          daylight-curve-preview-panel .brightness { color:#c58200; }
          daylight-curve-preview-panel .color { color:#248bd2; }
          daylight-curve-preview-panel #inspect { width:100%; box-sizing:border-box; }
          daylight-curve-preview-panel #notes { white-space:pre-line; }
          daylight-curve-preview-panel #status { min-height:1.5em; }
          daylight-curve-preview-panel table { border-collapse:collapse; width:100%; }
          daylight-curve-preview-panel th, daylight-curve-preview-panel td { padding:8px; text-align:left;
            border-bottom:1px solid var(--divider-color); }
          daylight-curve-preview-panel details { margin-top:24px; }
          daylight-curve-preview-panel summary { cursor:pointer; padding:8px 0; }
          @media(max-width:600px) { daylight-curve-preview-panel main { padding:12px; } }
        </style>
        <main>
          <h1>Daylight preview</h1>
          <p>Your saved schedule. Make changes in <a href="/config/integrations">Home Assistant settings</a>.</p>
          <div class="controls">
            <label>Target<select id="target" disabled></select></label>
            <label>Date<input id="date" type="date"></label>
            <button id="refresh" type="button">Refresh saved settings</button>
          </div>
          <p id="status" role="status" aria-live="polite"></p>
          <p id="zone"></p>
          <div class="legend"><span class="brightness">Brightness (%)</span><span class="color">Color temperature (K)</span></div>
          <svg id="chart" viewBox="0 0 1000 360" role="img" aria-label="Saved brightness and color temperature curves"></svg>
          <label for="inspect">Inspect time (does not change lights)</label>
          <input id="inspect" type="range" min="0" max="96" value="0" step="1" disabled>
          <output id="readout" for="inspect"></output>
          <p id="notes"></p>
          <p>Levels show this target’s configured ranges. Individual bulbs may clamp color temperature to their supported range.</p>
          <details><summary>Transition times</summary><div id="timing"></div></details>
          <details><summary>Values as a table</summary>
            <table><thead><tr><th>Local time</th><th>Brightness</th><th>Temperature</th></tr></thead><tbody id="values"></tbody></table>
          </details>
        </main>`;
      this._target = this.querySelector("#target");
      this._date = this.querySelector("#date");
      this._status = this.querySelector("#status");
      this._zone = this.querySelector("#zone");
      this._chart = this.querySelector("#chart");
      this._inspect = this.querySelector("#inspect");
      this._readout = this.querySelector("#readout");
      this._notes = this.querySelector("#notes");
      this._timing = this.querySelector("#timing");
      this._values = this.querySelector("#values");
      this._target.addEventListener("change", () => this._loadCurve());
      this._date.addEventListener("change", () => this._loadCurve());
      this._inspect.addEventListener("input", () => this._draw());
      this.querySelector("#refresh").addEventListener("click", () => this._refresh());
      this._resizeObserver = new ResizeObserver(() => this._draw());
      this._initialized = true;
    }
    this._resizeObserver.observe(this._chart);
    if (this._hass) this._refresh();
  }

  disconnectedCallback() {
    this._connected = false;
    this._resizeObserver?.disconnect();
    // In-flight requests may finish, but can no longer update this view.
    this._catalogId++;
    this._requestId++;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this._connected) this._refresh();
  }

  _clear() {
    this._data = null;
    this._chart.replaceChildren();
    this._values.replaceChildren();
    this._timing.replaceChildren();
    this._notes.textContent = "";
    this._readout.textContent = "";
    this._inspect.disabled = true;
  }

  async _refresh() {
    if (!this._hass) return;
    const id = ++this._catalogId;
    ++this._requestId;
    this._clear();
    this._status.textContent = "Loading saved targets…";
    this._target.disabled = true;
    try {
      const catalog = await this._hass.callApi("GET", "daylight/preview_targets");
      if (!this._connected || id !== this._catalogId) return;
      const previous = this._target.value;
      this._targets = catalog.targets;
      this._target.replaceChildren(...catalog.targets.map((target) => {
        const option = document.createElement("option");
        option.value = `${target.entry_id}:${target.target_id}`;
        option.textContent = target.name;
        return option;
      }));
      if (catalog.targets.some((target) => `${target.entry_id}:${target.target_id}` === previous)) {
        this._target.value = previous;
      }
      if (!this._date.value) this._date.value = catalog.today;
      this._zone.textContent = `Times shown in ${catalog.timezone}.`;
      this._target.disabled = !catalog.targets.length;
      if (!catalog.targets.length) {
        this._status.textContent = "No saved Daylight targets. Add lights in Home Assistant settings first.";
        return;
      }
      await this._loadCurve();
    } catch (error) {
      if (!this._connected || id !== this._catalogId) return;
      this._status.textContent = "Could not load targets. Use Refresh to try again.";
    }
  }

  async _loadCurve() {
    const id = ++this._requestId;
    this._clear();
    const target = this._targets.find((item) => `${item.entry_id}:${item.target_id}` === this._target.value);
    if (!target || !this._date.value) {
      this._status.textContent = "Choose a target and date.";
      return;
    }
    this._status.textContent = "Loading saved curve…";
    try {
      const data = await this._hass.callApi("POST", "daylight/sample_curve", {
        entry_id: target.entry_id, target_id: target.target_id, date: this._date.value
      });
      if (!this._connected || id !== this._requestId) return;
      this._data = data;
      this._status.textContent = target.name;
      this._zone.textContent = `Times shown in ${data.timezone}.`;
      this._inspect.max = data.points.length - 1;
      this._inspect.value = 0;
      this._inspect.disabled = false;
      const notes = [];
      if (data.timing.custom) notes.push("Custom timing rules are in use.");
      if (data.timing.compressed) notes.push("Standard transitions shortened to fit the available time.");
      if (data.timing.polar_fallback) notes.push("A solar crossing is unavailable. Automatic polar lighting anchors are in use; they are not actual sunrise/sunset events.");
      this._notes.textContent = notes.join("\n");
      for (const track of ["brightness", "color"]) {
        const line = document.createElement("p");
        const times = data.timing[track];
        line.textContent = `${track === "brightness" ? "Brightness" : "Color"}: morning ${this._time(times.morning_start)} → ${this._time(times.morning_end)}; evening ${this._time(times.evening_start)} → ${this._time(times.evening_end)}.`;
        this._timing.appendChild(line);
      }
      this._values.replaceChildren(...data.points.map((point) => {
        const row = document.createElement("tr");
        for (const value of [this._time(point.utc_time), `${point.brightness_pct}%`, `${point.color_temp_kelvin} K`]) {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.appendChild(cell);
        }
        return row;
      }));
      this._draw();
    } catch (error) {
      if (!this._connected || id !== this._requestId) return;
      this._clear();
      this._status.textContent = error?.body?.message || error?.message || "Could not load the saved curve. Check settings and refresh.";
    }
  }

  _time(iso, short = false) {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: this._data.timezone, hour: "2-digit", minute: "2-digit",
      ...(short ? {} : { month: "short", day: "numeric", timeZoneName: "short" })
    }).format(new Date(iso));
  }

  _draw() {
    if (!this._data) return;
    const { points, ranges, sunrise, sunset } = this._data;
    // Match viewBox units to CSS pixels so axis labels remain readable on phones.
    const width = Math.max(320, this._chart.clientWidth || 1000);
    this._chart.setAttribute("viewBox", `0 0 ${width} 360`);
    const left = 65, right = width - 75, top = 25, bottom = 305;
    const start = Date.parse(points[0].utc_time);
    const end = Date.parse(points[points.length - 1].utc_time);
    const x = (iso) => left + (Date.parse(iso) - start) / (end - start) * (right - left);
    const yBrightness = (value) => bottom - value / 100 * (bottom - top);
    const low = ranges.min_color_temp_kelvin, high = ranges.max_color_temp_kelvin;
    const yColor = (value) => bottom - (value - low) / (high - low || 1) * (bottom - top);
    const children = [];
    for (let i = 0; i <= 4; i++) {
      const y = top + i / 4 * (bottom - top);
      const tick = new Date(start + i / 4 * (end - start)).toISOString();
      children.push(
        svgElement("line", { x1: left, x2: right, y1: y, y2: y, stroke: "var(--divider-color, #ccc)" }),
        svgElement("text", { x: left - 8, y: y + 5, "text-anchor": "end", fill: "#c58200" }, `${100 - i * 25}%`),
        svgElement("text", { x: right + 8, y: y + 5, fill: "#248bd2" }, `${Math.round(high - i / 4 * (high - low))}K`),
        svgElement("text", { x: x(tick), y: bottom + 30, "text-anchor": "middle", fill: "currentColor" }, this._time(tick, true))
      );
    }
    for (const [key, y, stroke] of [["brightness_pct", yBrightness, "#c58200"], ["color_temp_kelvin", yColor, "#248bd2"]]) {
      children.push(svgElement("polyline", {
        points: points.map((point) => `${x(point.utc_time)},${y(point[key])}`).join(" "),
        fill: "none", stroke, "stroke-width": 3, "vector-effect": "non-scaling-stroke"
      }));
    }
    for (const [iso, name] of [[sunrise, "Sunrise"], [sunset, "Sunset"]]) {
      if (!iso || Date.parse(iso) < start || Date.parse(iso) > end) continue;
      children.push(
        svgElement("line", { x1: x(iso), x2: x(iso), y1: top, y2: bottom, stroke: "currentColor", "stroke-dasharray": "4 4" }),
        svgElement("text", { x: x(iso) + 5, y: top - 8, fill: "currentColor" }, name)
      );
    }
    const point = points[Number(this._inspect.value)];
    children.push(svgElement("line", { x1: x(point.utc_time), x2: x(point.utc_time), y1: top, y2: bottom, stroke: "currentColor", opacity: 0.5 }));
    this._readout.textContent = `${this._time(point.utc_time)} · ${point.brightness_pct}% · ${point.color_temp_kelvin} K`;
    this._inspect.setAttribute("aria-valuetext", this._readout.textContent);
    this._chart.replaceChildren(...children);
  }
}

customElements.define("daylight-curve-preview-panel", DaylightCurvePreviewPanel);
