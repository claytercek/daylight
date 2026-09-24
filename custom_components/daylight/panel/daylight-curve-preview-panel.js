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
    this._data = {
      min_brightness_pct: 1,
      max_brightness_pct: 100,
      min_color_temp_kelvin: 2000,
      max_color_temp_kelvin: 5500
    };
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

    const wrapper = document.createElement("div");

    this._formContainer = document.createElement("div");
    this._formContainer.textContent = "Loading…";
    wrapper.appendChild(this._formContainer);

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
    canvas.width = 600;
    canvas.height = 300;
    canvas.style.width = "100%";
    canvas.style.maxWidth = "600px";
    canvas.style.height = "auto";
    this._chartCanvas = canvas;
    chartContainer.appendChild(canvas);

    const chartErrorMessage = document.createElement("div");
    chartErrorMessage.className = "chart-error";
    chartErrorMessage.style.display = "none";
    this._chartErrorMessage = chartErrorMessage;
    chartContainer.appendChild(chartErrorMessage);

    wrapper.appendChild(chartContainer);

    this.appendChild(wrapper);
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

      // Seed initial data with defaults from schema
      for (const field of schema) {
        if (field.default !== undefined && !(field.name in this._data)) {
          this._data[field.name] = field.default;
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
  // contents rather than appending to them. Currently only called once,
  // from the initial fields fetch, but is written as a reusable unit
  // because a future slice will call it again on field edits.
  async _fetchAndDrawChart() {
    const payload = {
      min_brightness_pct: this._data.min_brightness_pct,
      max_brightness_pct: this._data.max_brightness_pct,
      min_color_temp_kelvin: this._data.min_color_temp_kelvin,
      max_color_temp_kelvin: this._data.max_color_temp_kelvin,
      num_points: 96,
      start: DaylightCurvePreviewPanel._buildLocalMidnightISOString()
    };

    try {
      const response = await this._hass.callApi("POST", "daylight/sample_curve", payload);
      this._points = response.points;
      this._chartErrorMessage.style.display = "none";
      this._chartErrorMessage.textContent = "";
      this._drawChart(this._points, payload.min_color_temp_kelvin, payload.max_color_temp_kelvin);
    } catch (error) {
      this._points = null;
      console.error("Failed to fetch curve sample:", error);
      this._clearChart();
      this._chartErrorMessage.textContent = "Error loading curve";
      this._chartErrorMessage.style.display = "";
    }
  }

  _clearChart() {
    const ctx = this._chartCanvas.getContext("2d");
    ctx.clearRect(0, 0, this._chartCanvas.width, this._chartCanvas.height);
  }

  // Draws both series (brightness and color temp) over the same x-axis.
  // Replaces the previous chart contents; does not append.
  _drawChart(points, colorTempMin, colorTempMax) {
    this._clearChart();

    if (!points || points.length === 0) {
      console.warn("Curve sample returned no points:", points);
      this._chartErrorMessage.textContent = "No curve data";
      this._chartErrorMessage.style.display = "";
      return;
    }

    const canvas = this._chartCanvas;
    const ctx = canvas.getContext("2d");
    const padding = 8;
    const plotWidth = canvas.width - padding * 2;
    const plotHeight = canvas.height - padding * 2;

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
  }

  _renderForm() {
    this._formContainer.innerHTML = "";

    this._formElement = document.createElement("ha-form");
    this._formElement.hass = this._hass;
    this._formElement.schema = this._schema;
    this._formElement.data = this._data;

    this._formElement.addEventListener("value-changed", (event) => {
      this._data = event.detail.value;
      this._formElement.data = this._data;
    });

    this._formContainer.appendChild(this._formElement);
  }
}

customElements.define("daylight-curve-preview-panel", DaylightCurvePreviewPanel);
