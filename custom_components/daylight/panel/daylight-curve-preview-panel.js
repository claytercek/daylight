const SECTION_TITLES = {
  sunrise: "Sunrise",
  sunset: "Sunset",
  brightness_curve: "Brightness Curve",
  brightness: "Brightness",
  color_temp: "Color Temperature"
};

const FIELD_LABELS = {
  sunrise_time: "Sunrise Time",
  min_sunrise_time: "Minimum Sunrise Time",
  max_sunrise_time: "Maximum Sunrise Time",
  sunrise_offset_minutes: "Sunrise Offset Minutes",
  sunset_time: "Sunset Time",
  min_sunset_time: "Minimum Sunset Time",
  max_sunset_time: "Maximum Sunset Time",
  sunset_offset_minutes: "Sunset Offset Minutes",
  brightness_mode: "Brightness Mode",
  brightness_mode_time_dark_minutes: "Brightness Ramp Time When Dark",
  brightness_mode_time_light_minutes: "Brightness Ramp Time When Light",
  min_brightness_pct: "Minimum Brightness Percent",
  max_brightness_pct: "Maximum Brightness Percent",
  min_color_temp_kelvin: "Minimum Color Temperature Kelvin",
  max_color_temp_kelvin: "Maximum Color Temperature Kelvin"
};

class DaylightCurvePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._fieldsFetched = false;
    this._formElement = null;
    this._formContainer = null;
    this._schema = null;
    this._initialized = false;
    this._chartContainer = null;
    this._chartCanvas = null;
    this._chartErrorMessage = null;
    this._points = null;
    this._chartDebounceTimer = null;
    this._chartRequestId = 0;
    this._data = {
      brightness: { min_brightness_pct: 1, max_brightness_pct: 100 },
      color_temp: { min_color_temp_kelvin: 2000, max_color_temp_kelvin: 5500 }
    };
    this._sunrise = null;
    this._sunset = null;
  }

  // Builds an ISO-8601 string for local midnight today with an explicit
  // numeric UTC offset (never "Z"), because the sample_curve endpoint
  // rejects bare/offsetless timestamps.
  static _buildLocalMidnightISOString(date = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");

    const year = date.getFullYear();
    const month = date.getMonth();
    const day = date.getDate();
    const midnight = new Date(year, month, day, 0, 0, 0, 0);

    // getTimezoneOffset() is minutes *behind* UTC, with the sign inverted
    // relative to a conventional offset string (e.g. UTC-5 => +300).
    const offsetMinutes = midnight.getTimezoneOffset();
    const sign = offsetMinutes > 0 ? "-" : "+";
    const absMinutes = Math.abs(offsetMinutes);
    const offsetHours = Math.floor(absMinutes / 60);
    const offsetMins = absMinutes % 60;
    const offset = `${sign}${pad(offsetHours)}:${pad(offsetMins)}`;

    return `${year}-${pad(month + 1)}-${pad(day)}T00:00:00${offset}`;
  }

  connectedCallback() {
    if (this._initialized) return;
    this._initialized = true;

    const style = document.createElement("style");
    style.textContent = `
      daylight-curve-preview-panel .panel-wrapper {
        padding: 24px;
        max-width: 1400px;
        margin: 0 auto;
        box-sizing: border-box;
      }
      daylight-curve-preview-panel .panel-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 24px;
        align-items: start;
      }
      daylight-curve-preview-panel #chart-container {
        margin-top: 24px;
      }
      daylight-curve-preview-panel canvas {
        display: block;
        width: 100%;
        height: 400px;
      }
      @media (min-width: 1100px) {
        daylight-curve-preview-panel .panel-grid {
          grid-template-columns: minmax(320px, 480px) 1fr;
        }
        daylight-curve-preview-panel #chart-container {
          position: sticky;
          top: 24px;
        }
      }
    `;
    this.appendChild(style);

    const wrapper = document.createElement("div");
    wrapper.className = "panel-wrapper";

    const grid = document.createElement("div");
    grid.className = "panel-grid";

    this._formContainer = document.createElement("div");
    this._formContainer.textContent = "Loading…";
    grid.appendChild(this._formContainer);

    const chartContainer = document.createElement("div");
    chartContainer.id = "chart-container";
    this._chartContainer = chartContainer;

    const legend = document.createElement("div");
    legend.className = "chart-legend";
    legend.innerHTML =
      '<span style="color:#e69500;">■</span> Brightness (%)' +
      "&nbsp;&nbsp;" +
      '<span style="color:#1e88e5;">■</span> Color temp (K)';
    chartContainer.appendChild(legend);

    const canvas = document.createElement("canvas");
    this._chartCanvas = canvas;
    chartContainer.appendChild(canvas);

    const chartErrorMessage = document.createElement("div");
    chartErrorMessage.className = "chart-error";
    chartErrorMessage.style.display = "none";
    this._chartErrorMessage = chartErrorMessage;
    chartContainer.appendChild(chartErrorMessage);

    grid.appendChild(chartContainer);
    wrapper.appendChild(grid);

    this.appendChild(wrapper);

    this._resizeObserver = new ResizeObserver(() => this._resizeCanvas());
    this._resizeObserver.observe(canvas);

    this._resizeCanvas();
  }

  set hass(hass) {
    this._hass = hass;

    if (!this._fieldsFetched && hass) {
      this._fetchFields();
    } else if (this._formElement && hass) {
      this._formElement.hass = hass;
    }
  }

  async _fetchFields() {
    this._fieldsFetched = true;

    try {
      const schema = await this._hass.callApi("GET", "daylight/preview_fields");
      this._schema = schema;

      // Seed initial data with defaults from schema. Each top-level entry is
      // an expandable section; recurse into its nested schema to seed
      // defaults for the leaf fields, without clobbering existing values.
      for (const section of schema) {
        if (section.type !== "expandable") continue;

        section.title = SECTION_TITLES[section.name] || section.name;

        if (!(section.name in this._data)) {
          this._data[section.name] = {};
        }
        const sectionData = this._data[section.name];

        for (const field of section.schema) {
          if (field.default !== undefined && !(field.name in sectionData)) {
            sectionData[field.name] = field.default;
          }
        }
      }

      this._renderForm();
      await this._fetchAndDrawChart();
    } catch (error) {
      this._formContainer.textContent = "Error loading form";
      console.error("Failed to fetch preview fields:", error);
    }
  }

  // Fetches a fresh curve sample from the server and redraws the chart.
  // Safe to call multiple times: each call replaces the previous chart
  // contents rather than appending to them. Called once from the initial
  // fields fetch, and again from the debounced value-changed handler on
  // every field edit. Captures this._chartRequestId on entry so that if a
  // newer call starts before this one's response arrives, the stale
  // response bails out silently instead of drawing or showing an error.
  async _fetchAndDrawChart() {
    const requestId = ++this._chartRequestId;

    // Drop null/undefined leaf values from each section: optional
    // time-selector fields the user never touched may end up null, and the
    // backend's TimeSelector() will 400 on an explicit null, but an absent
    // key is fine.
    const cleanedData = {};
    for (const [sectionName, sectionValue] of Object.entries(this._data)) {
      const cleanedSection = {};
      if (sectionValue && typeof sectionValue === "object") {
        for (const [key, value] of Object.entries(sectionValue)) {
          if (value !== null && value !== undefined) {
            cleanedSection[key] = value;
          }
        }
      }
      cleanedData[sectionName] = cleanedSection;
    }

    const payload = {
      ...cleanedData,
      num_points: 96,
      start: DaylightCurvePreviewPanel._buildLocalMidnightISOString()
    };

    try {
      const response = await this._hass.callApi("POST", "daylight/sample_curve", payload);
      if (requestId !== this._chartRequestId) return;
      this._points = response.points;
      this._sunrise = response.sunrise;
      this._sunset = response.sunset;
      this._chartErrorMessage.style.display = "none";
      this._chartErrorMessage.textContent = "";
      const colorTempBounds = payload.color_temp || {};
      this._drawChart(
        this._points,
        colorTempBounds.min_color_temp_kelvin,
        colorTempBounds.max_color_temp_kelvin,
        response.sunrise,
        response.sunset
      );
    } catch (error) {
      if (requestId !== this._chartRequestId) return;
      this._points = null;
      console.error("Failed to fetch curve sample:", error);
      this._clearChart();
      this._chartErrorMessage.textContent = "Error loading curve";
      this._chartErrorMessage.style.display = "";
    }
  }

  // Reads the CSS-determined display size of the canvas and syncs its
  // device-pixel backing store to match, accounting for devicePixelRatio so
  // strokes stay crisp on high-DPI screens. Called from the ResizeObserver
  // whenever the canvas's layout size changes, and once up front from
  // connectedCallback. Bails out before layout has settled (size still 0).
  _resizeCanvas() {
    const canvas = this._chartCanvas;
    const displayWidth = canvas.clientWidth;
    const displayHeight = canvas.clientHeight;
    if (displayWidth === 0 || displayHeight === 0) return;

    const dpr = window.devicePixelRatio || 1;
    const targetWidth = Math.round(displayWidth * dpr);
    const targetHeight = Math.round(displayHeight * dpr);
    if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
      canvas.width = targetWidth;
      canvas.height = targetHeight;
    }

    this._chartDisplayWidth = displayWidth;
    this._chartDisplayHeight = displayHeight;

    this._redraw();
  }

  // Redraws using the most recent successful _drawChart args, without
  // re-fetching from the server. Used by the ResizeObserver so a viewport
  // resize doesn't trigger a network round-trip.
  _redraw() {
    if (this._lastDrawArgs) {
      this._drawChart(...this._lastDrawArgs);
    } else {
      this._clearChart();
    }
  }

  _clearChart() {
    const canvas = this._chartCanvas;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  // Draws both series (brightness and color temp) over the same x-axis.
  // Replaces the previous chart contents; does not append.
  _drawChart(points, colorTempMin, colorTempMax, sunrise, sunset) {
    this._lastDrawArgs = [points, colorTempMin, colorTempMax, sunrise, sunset];

    this._clearChart();

    if (!points || points.length === 0) {
      console.warn("Curve sample returned no points:", points);
      this._chartErrorMessage.textContent = "No curve data";
      this._chartErrorMessage.style.display = "";
      return;
    }

    const canvas = this._chartCanvas;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const width = this._chartDisplayWidth || canvas.clientWidth;
    const height = this._chartDisplayHeight || canvas.clientHeight;
    const padding = 8; // per-side margins come in slice B, leave as-is for now
    const plotWidth = width - padding * 2;
    const plotHeight = height - padding * 2;

    const xForIndex = (index) =>
      padding + (points.length > 1 ? (index / (points.length - 1)) * plotWidth : 0);

    const yForBrightness = (value) => padding + plotHeight - (value / 100) * plotHeight;

    const tempRange = colorTempMax - colorTempMin || 1;
    const yForColorTemp = (value) =>
      padding + plotHeight - ((value - colorTempMin) / tempRange) * plotHeight;

    const drawSeries = (valueKey, yFor, strokeStyle) => {
      ctx.beginPath();
      ctx.strokeStyle = strokeStyle;
      ctx.lineWidth = 2;
      points.forEach((point, index) => {
        const x = xForIndex(index);
        const y = yFor(point[valueKey]);
        if (index === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
    };

    drawSeries("brightness_pct", yForBrightness, "#e69500");
    drawSeries("color_temp_kelvin", yForColorTemp, "#1e88e5");

    const firstTime = new Date(points[0].utc_time).getTime();
    const lastTime = new Date(points[points.length - 1].utc_time).getTime();

    const drawSunMarker = (isoString, label, labelY) => {
      if (!isoString) return;
      const targetTime = new Date(isoString).getTime();
      const fraction = (targetTime - firstTime) / (lastTime - firstTime);
      if (!Number.isFinite(fraction) || fraction < 0 || fraction > 1) return;

      const x = padding + fraction * plotWidth;

      ctx.save();
      ctx.strokeStyle = "#888";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(x, padding);
      ctx.lineTo(x, padding + plotHeight);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = "#888";
      ctx.font = "10px sans-serif";
      ctx.fillText(label, x + 2, labelY);
      ctx.restore();
    };

    drawSunMarker(sunrise, "Sunrise", padding + 10);
    drawSunMarker(sunset, "Sunset", padding + 22);
  }

  _renderForm() {
    this._formContainer.innerHTML = "";

    this._formElement = document.createElement("ha-form");
    this._formElement.hass = this._hass;
    this._formElement.schema = this._schema;
    this._formElement.data = this._data;
    this._formElement.computeLabel = (schema) => FIELD_LABELS[schema.name] || schema.name;

    this._formElement.addEventListener("value-changed", (event) => {
      this._data = event.detail.value;
      this._formElement.data = this._data;

      clearTimeout(this._chartDebounceTimer);
      this._chartDebounceTimer = setTimeout(() => {
        this._fetchAndDrawChart();
      }, 300);
    });

    this._formContainer.appendChild(this._formElement);
  }
}

customElements.define("daylight-curve-preview-panel", DaylightCurvePreviewPanel);
