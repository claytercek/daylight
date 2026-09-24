class DaylightCurvePreviewPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._fieldsFetched = false;
    this._formElement = null;
    this._formContainer = null;
    this._schema = null;
    this._initialized = false;
    this._data = {
      min_brightness_pct: 1,
      max_brightness_pct: 100,
      min_color_temp_kelvin: 2000,
      max_color_temp_kelvin: 5500
    };
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
    } catch (error) {
      this._formContainer.textContent = "Error loading form";
      console.error("Failed to fetch preview fields:", error);
    }
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
