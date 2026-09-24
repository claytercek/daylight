// Placeholder for the daylight curve-preview panel. Proves the registration
// pipeline (static path -> panel_custom -> sidebar) works end to end; the
// real canvas/sliders/`sample_curve`+`preview_fields` calls are a follow-up.
class DaylightCurvePreviewPanel extends HTMLElement {
  connectedCallback() {
    this.textContent = "daylight curve preview panel placeholder";
  }
}

customElements.define("daylight-curve-preview-panel", DaylightCurvePreviewPanel);
