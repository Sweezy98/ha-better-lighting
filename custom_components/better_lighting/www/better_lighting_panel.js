/*
 * Better Lighting's scene editor.
 *
 * Plain JavaScript custom elements, no build step and no dependencies: HACS
 * copies files into config/custom_components and never runs a bundler, so
 * anything needing compilation would have to be committed pre-built and would
 * rot silently. The cost is doing without Lit; the gain is that what is in the
 * repository is what runs.
 *
 * The controls are built here rather than borrowed from the frontend. Home
 * Assistant's own ha-hs-color-picker and friends are internal elements with no
 * compatibility promise, and a panel that breaks on a frontend refactor is
 * worse than one that owns three hundred lines of canvas.
 */

// The entry that stands for every light in the room, matching ALL_LIGHTS on
// the Python side.
const ALL = "*";

// The fingerprint this copy was served under, taken from its own URL. The
// backend stamps the URL with a hash of the file, so comparing the two is how
// an open page learns it has been superseded.
const OWN_VERSION = new URL(import.meta.url).searchParams.get("v");

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/** Kelvin to an approximate sRGB triplet, for painting the temperature slider. */
function kelvinToRgb(kelvin) {
  const t = clamp(kelvin, 1000, 12000) / 100;
  let r;
  let g;
  let b;
  if (t <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(t) - 161.1195681661;
    b = t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  } else {
    r = 329.698727446 * (t - 60) ** -0.1332047592;
    g = 288.1221695283 * (t - 60) ** -0.0755148492;
    b = 255;
  }
  return [r, g, b].map((v) => Math.round(clamp(v, 0, 255)));
}

function hsToRgb(hue, saturation) {
  const h = (hue % 360) / 60;
  const s = clamp(saturation, 0, 100) / 100;
  const c = s;
  const x = c * (1 - Math.abs((h % 2) - 1));
  const [r, g, b] = (
    [
      [c, x, 0],
      [x, c, 0],
      [0, c, x],
      [0, x, c],
      [x, 0, c],
      [c, 0, x],
    ][Math.floor(h) % 6]
  ).map((v) => Math.round((v + (1 - c)) * 255));
  return [r, g, b];
}

function rgbToHs(rgb) {
  const [r, g, b] = rgb.map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;
  let hue = 0;
  if (delta) {
    if (max === r) hue = 60 * (((g - b) / delta) % 6);
    else if (max === g) hue = 60 * ((b - r) / delta + 2);
    else hue = 60 * ((r - g) / delta + 4);
  }
  return [(hue + 360) % 360, max ? (delta / max) * 100 : 0];
}

/* ------------------------------------------------------------------ */
/* A colour wheel, the control a configuration form cannot give you.    */
/* ------------------------------------------------------------------ */

class BlColorWheel extends HTMLElement {
  static observedAttributes = ["hue", "saturation"];

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hue = 30;
    this._saturation = 80;
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; touch-action:none; }
        .wrap { position:relative; width:100%; aspect-ratio:1; max-width:260px; margin:0 auto; }
        canvas { width:100%; height:100%; border-radius:50%; display:block;
                 box-shadow:0 2px 10px rgba(0,0,0,.25); cursor:crosshair; }
        .knob { position:absolute; width:22px; height:22px; margin:-11px 0 0 -11px;
                border-radius:50%; border:3px solid #fff; pointer-events:none;
                box-shadow:0 1px 4px rgba(0,0,0,.5); }
      </style>
      <div class="wrap"><canvas width="260" height="260"></canvas><div class="knob"></div></div>`;
    this._canvas = this.shadowRoot.querySelector("canvas");
    this._knob = this.shadowRoot.querySelector(".knob");
    this._paint();
    this._placeKnob();

    const pick = (event) => {
      const rect = this._canvas.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width - 0.5;
      const y = (event.clientY - rect.top) / rect.height - 0.5;
      const radius = Math.min(1, Math.hypot(x, y) * 2);
      this._hue = (Math.atan2(y, x) * 180) / Math.PI + 90;
      if (this._hue < 0) this._hue += 360;
      this._saturation = Math.round(radius * 100);
      this._placeKnob();
      this.dispatchEvent(
        new CustomEvent("value-changed", {
          detail: { hue: this._hue, saturation: this._saturation },
        })
      );
    };

    this._canvas.addEventListener("pointerdown", (event) => {
      this._canvas.setPointerCapture(event.pointerId);
      this._dragging = true;
      pick(event);
    });
    this._canvas.addEventListener("pointermove", (event) => {
      if (this._dragging) pick(event);
    });
    this._canvas.addEventListener("pointerup", () => {
      this._dragging = false;
      this.dispatchEvent(new CustomEvent("value-settled"));
    });
  }

  attributeChangedCallback(name, _old, value) {
    if (value === null) return;
    if (name === "hue") this._hue = Number(value);
    if (name === "saturation") this._saturation = Number(value);
    this._placeKnob();
  }

  _paint() {
    const ctx = this._canvas.getContext("2d");
    const size = this._canvas.width;
    const image = ctx.createImageData(size, size);
    const half = size / 2;
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const dx = x - half;
        const dy = y - half;
        const distance = Math.hypot(dx, dy) / half;
        const index = (y * size + x) * 4;
        if (distance > 1) {
          image.data[index + 3] = 0;
          continue;
        }
        let hue = (Math.atan2(dy, dx) * 180) / Math.PI + 90;
        if (hue < 0) hue += 360;
        const [r, g, b] = hsToRgb(hue, distance * 100);
        image.data[index] = r;
        image.data[index + 1] = g;
        image.data[index + 2] = b;
        image.data[index + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
  }

  _placeKnob() {
    if (!this._knob) return;
    const angle = ((this._hue - 90) * Math.PI) / 180;
    const radius = (this._saturation / 100) * 50;
    this._knob.style.left = `${50 + Math.cos(angle) * radius}%`;
    this._knob.style.top = `${50 + Math.sin(angle) * radius}%`;
    this._knob.style.background = `rgb(${hsToRgb(this._hue, this._saturation).join(",")})`;
  }
}

/* ------------------------------------------------------------------ */
/* A fat slider, in the shape Home Assistant uses for brightness.       */
/* ------------------------------------------------------------------ */

class BlSlider extends HTMLElement {
  static observedAttributes = ["value", "min", "max", "gradient", "unit"];

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._value = 50;
    this._min = 1;
    this._max = 100;
  }

  connectedCallback() {
    this.shadowRoot.innerHTML = `
      <style>
        /* width:100% because the host is often a flex item: without it the
           track gets no width at all, and all that shows is the number box --
           which is exactly how this looked. */
        :host { display:block; width:100%; touch-action:none; }
        .row { display:flex; align-items:center; gap:10px; }
        .track { position:relative; flex:1 1 auto; min-width:80px; height:44px;
                 border-radius:12px; overflow:hidden; cursor:pointer;
                 background:var(--bl-track, var(--secondary-background-color,#e0e0e0)); }
        .fill { position:absolute; inset:0 auto 0 0;
                background:var(--bl-fill, var(--primary-color,#f5c518)); }
        /* Matching the plain number fields beside it, so one form does not
           look like two. */
        input { font:inherit; width:96px; flex:0 0 auto; padding:9px 10px;
                border-radius:8px; box-sizing:border-box;
                border:1px solid var(--divider-color,#ccc);
                background:var(--card-background-color); color:inherit; }
        .unit { color:var(--secondary-text-color); }
      </style>
      <div class="row">
        <div class="track"><div class="fill"></div></div>
        <input type="number" part="box">
        <span class="unit"></span>
      </div>`;
    this._track = this.shadowRoot.querySelector(".track");
    this._fill = this.shadowRoot.querySelector(".fill");
    this._box = this.shadowRoot.querySelector("input");
    this._unit = this.shadowRoot.querySelector(".unit");
    this._render();

    const pick = (event) => {
      const rect = this._track.getBoundingClientRect();
      const fraction = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      this._value = Math.round(this._min + fraction * (this._max - this._min));
      this._render();
      this._emit();
    };
    this._track.addEventListener("pointerdown", (event) => {
      this._track.setPointerCapture(event.pointerId);
      this._dragging = true;
      pick(event);
    });
    this._track.addEventListener("pointermove", (event) => {
      if (this._dragging) pick(event);
    });
    this._track.addEventListener("pointerup", () => {
      this._dragging = false;
      this.dispatchEvent(new CustomEvent("value-settled"));
    });
    // A box beside the slider, because a slider cannot hit 2700 K on purpose.
    this._box.addEventListener("change", () => {
      this._value = clamp(Number(this._box.value), this._min, this._max);
      this._render();
      this._emit();
      this.dispatchEvent(new CustomEvent("value-settled"));
    });
  }

  attributeChangedCallback(name, _old, value) {
    if (value === null) return;
    if (name === "value") this._value = Number(value);
    if (name === "min") this._min = Number(value);
    if (name === "max") this._max = Number(value);
    if (name === "gradient") this._gradient = value;
    if (name === "unit") this._unitText = value;
    this._render();
  }

  get value() {
    return this._value;
  }

  _emit() {
    this.dispatchEvent(
      new CustomEvent("value-changed", { detail: { value: this._value } })
    );
  }

  _render() {
    if (!this._track) return;
    const fraction = (this._value - this._min) / (this._max - this._min || 1);
    this._fill.style.width = `${fraction * 100}%`;
    if (this._unit) this._unit.textContent = this._unitText || "";
    this._box.min = this._min;
    this._box.max = this._max;
    this._box.value = this._value;
    if (this._gradient === "temperature") {
      const stops = [2000, 3000, 4000, 5000, 6500].map(
        (k) =>
          `rgb(${kelvinToRgb(k).join(",")}) ${
            ((k - this._min) / (this._max - this._min)) * 100
          }%`
      );
      this._track.style.background = `linear-gradient(to right, ${stops.join(",")})`;
      this._fill.style.display = "none";
    }
  }
}

customElements.define("bl-color-wheel", BlColorWheel);
customElements.define("bl-slider", BlSlider);



/**
 * Coax Home Assistant's own form controls into being defined.
 *
 * Its entity picker and its toggle are registered lazily, with the config
 * editors, so a custom panel loaded on its own never has them. Asking the
 * card helpers to build an entities-card editor pulls them in -- the
 * long-standing recipe custom cards use, and the only one available from out
 * here.
 *
 * Progressive: if any of it fails the caller keeps a plain dropdown and a
 * plain checkbox, which work and are merely plainer. Nothing depends on this
 * succeeding, which is the point -- it leans on an arrangement Home Assistant
 * never promised to keep.
 */
let controlsReady;
function ensureHaControls() {
  if (controlsReady) return controlsReady;
  controlsReady = (async () => {
    // ha-selector renders every field the way Home Assistant renders it
    // everywhere else -- chips for a multi-select, its own pickers, its own
    // sliders. The rest are what the fallbacks reach for when it is absent.
    const wanted = [
      "ha-selector",
      "ha-entity-picker",
      "ha-switch",
      "ha-icon-picker",
      "ha-area-picker",
    ];
    if (wanted.every((tag) => customElements.get(tag))) return true;
    try {
      const helpers = await window.loadCardHelpers?.();
      if (!helpers) return false;
      // Two editors rather than one: the entities card brings the picker and
      // the toggle, the button card brings the icon picker. Either may fail
      // without costing the other.
      for (const config of [
        { type: "entities", entities: [] },
        { type: "button" },
      ]) {
        try {
          const card = await helpers.createCardElement(config);
          await card.constructor.getConfigElement();
        } catch {
          // This one is unavailable; the next may not be.
        }
      }
      return wanted.some((tag) => customElements.get(tag));
    } catch {
      return false;
    }
  })();
  return controlsReady;
}

/* ------------------------------------------------------------------ */
/* A form, rendered from the same field tables the settings screens use.*/
/* ------------------------------------------------------------------ */

/**
 * Draws one group of fields and reports every change.
 *
 * Generic on purpose: the backend describes each field (kind, bounds, units,
 * options) from the FieldSpec table the config flow renders, so a setting
 * added there appears here with no work, and the two can never disagree about
 * what a field is.
 */
class BlForm extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._fields = [];
    this._values = {};
    this._labels = { data: {}, descriptions: {}, options: {} };
    this._choices = {};
  }

  configure({ fields, values, labels, choices, states, hass }) {
    this._fields = fields || [];
    this._values = values || {};
    this._labels = labels || this._labels;
    this._choices = choices || {};
    this._states = states || {};
    this._hass = hass;
    // Re-render once Home Assistant's picker is available, so the first paint
    // is not held up waiting for something that may never arrive.
    ensureHaControls().then((ready) => {
      if (ready && !this._picker) {
        this._picker = true;
        this._render();
      }
    });
    this._render();
  }

  get value() {
    return this._values;
  }

  _label(key) {
    return this._labels.data[key] || key.replace(/_/g, " ");
  }

  _optionLabel(field, value) {
    const table = this._labels.options[field.translation_key] || {};
    return table[value] || String(value).replace(/_/g, " ");
  }

  _choicesFor(field) {
    if (field.options_key) return this._choices[field.options_key] || [];
    if (field.options) {
      return field.options.map((value) => ({
        value,
        label: this._optionLabel(field, value),
      }));
    }
    return [];
  }

  _entities(field) {
    const domains = field.domains || [];
    return Object.keys(this._states)
      .filter((id) => !domains.length || domains.includes(id.split(".")[0]))
      .sort()
      .map((id) => ({
        value: id,
        label: this._states[id].attributes?.friendly_name
          ? `${this._states[id].attributes.friendly_name} (${id})`
          : id,
      }));
  }

  _set(key, value) {
    this._values = { ...this._values, [key]: value };
    this.dispatchEvent(
      new CustomEvent("value-changed", { detail: { key, value, values: this._values } })
    );
    // A field others depend on can reveal or hide them.
    if (this._fields.some((f) => f.depends_on?.key === key)) this._render();
  }

  _visible(field) {
    if (!field.depends_on) return true;
    const key = field.depends_on.key;
    // The other field's default when nothing is stored, since that is what
    // the form beside this one is showing.
    const other = this._fields.find((candidate) => candidate.key === key);
    const value = this._values[key] ?? other?.default;
    return field.depends_on.values.includes(value);
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        .field { margin-bottom:20px; }
        label { display:block; font-weight:500; margin-bottom:6px; }
        .hint { color:var(--secondary-text-color); font-size:13px; margin-bottom:8px; }
        input[type=text], input[type=number], select {
          font:inherit; padding:9px 10px; border-radius:8px; width:100%;
          box-sizing:border-box; border:1px solid var(--divider-color,#ccc);
          background:var(--card-background-color); color:inherit; }
        select[multiple] { min-height:120px; }
        .switch { display:flex; align-items:center; gap:10px; }
        .switch input { width:auto; }
        .row { display:flex; gap:10px; align-items:center; }
        .row input[type=number] { width:110px; flex:0 0 auto; }
        .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
        .chip { background:var(--secondary-background-color); border-radius:12px;
                padding:3px 10px; font-size:13px; display:flex; gap:6px; align-items:center; }
        .chip button { border:none; background:none; cursor:pointer; color:inherit;
                       font:inherit; padding:0; }
        .import { display:flex; align-items:center; gap:12px; flex-wrap:wrap;
                  padding:12px 0; border-top:1px solid var(--divider-color,#e0e0e0); }
        .import .chip { gap:6px; }
        #crumbs { display:flex; min-height:34px; align-items:center; gap:10px; margin-bottom:12px; }
        .crumb-back { background:var(--card-background-color); color:inherit;
                      border:1px solid var(--divider-color,#ccc); border-radius:8px;
                      width:34px; height:34px; padding:0; font-size:20px;
                      line-height:1; cursor:pointer; flex:0 0 auto; }
        .crumb-back[disabled] { opacity:.35; cursor:default; }
        .crumb-trail { color:var(--secondary-text-color); font-size:14px; }
        details.diag { border-top:1px solid var(--divider-color,#e0e0e0); }
        details.diag table { width:100%; border-collapse:collapse; margin:4px 0 12px; }
        details.diag th { text-align:left; font-weight:400; padding:4px 12px 4px 4px;
                          color:var(--secondary-text-color); white-space:nowrap;
                          vertical-align:top; width:1%; }
        details.diag td { padding:4px; font-variant-numeric:tabular-nums; }
        .log { max-height:340px; overflow:auto; font-size:13px; }
        .log .entry { padding:6px 4px; border-top:1px solid var(--divider-color,#e0e0e0);
                      display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
        .curve { width:100%; height:200px; display:block; margin:8px 0 12px;
                 border-radius:8px; overflow:hidden;
                 background:var(--secondary-background-color,#eee); }
        .curve-now th, .curve-lights th { text-align:left; font-weight:400;
                 color:var(--secondary-text-color); padding:3px 12px 3px 0; }
        .curve-lights td, .curve-now td { font-variant-numeric:tabular-nums; }
      </style>
      <div id="fields"></div>`;
    const holder = this.shadowRoot.getElementById("fields");

    for (const field of this._fields) {
      if (!this._visible(field)) continue;
      const wrap = document.createElement("div");
      wrap.className = "field";
      const value =
        this._values[field.key] !== undefined
          ? this._values[field.key]
          : field.default;

      const hint = this._labels.descriptions[field.key];
      wrap.innerHTML = `<label>${this._label(field.key)}</label>${
        hint ? `<div class="hint">${hint}</div>` : ""
      }`;
      wrap.appendChild(this._control(field, value));
      holder.appendChild(wrap);
    }
  }

  _control(field, value) {
    // Home Assistant's own renderer first: it is the control people already
    // use for this exact selector, down to the chips-with-an-add-button that
    // a multi-select with custom values gets.
    const native = this._nativeControl(field, value);
    if (native) return native;

    switch (field.kind) {
      case "boolean": {
        const box = document.createElement("div");
        box.className = "switch";
        if (customElements.get("ha-switch")) {
          const toggle = document.createElement("ha-switch");
          toggle.checked = Boolean(value);
          toggle.addEventListener("change", () =>
            this._set(field.key, toggle.checked)
          );
          box.appendChild(toggle);
          return box;
        }
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(value);
        input.addEventListener("change", () => this._set(field.key, input.checked));
        box.appendChild(input);
        return box;
      }
      case "number": {
        if (field.mode === "slider") {
          // Returned bare rather than inside a row: the slider is its own
          // row, and nesting it in another flex line collapsed its track.
          const bl = document.createElement("bl-slider");
          bl.setAttribute("min", field.min ?? 0);
          bl.setAttribute("max", field.max ?? 100);
          bl.setAttribute("value", value ?? field.default ?? field.min ?? 0);
          if (field.unit_of_measurement) {
            bl.setAttribute("unit", field.unit_of_measurement);
          }
          bl.addEventListener("value-changed", (event) =>
            this._set(field.key, event.detail.value)
          );
          return bl;
        }
        const row = document.createElement("div");
        row.className = "row";
        const input = document.createElement("input");
        input.type = "number";
        if (field.min !== undefined) input.min = field.min;
        if (field.max !== undefined) input.max = field.max;
        if (field.step !== undefined) input.step = field.step;
        input.value = value ?? "";
        input.addEventListener("change", () =>
          this._set(field.key, input.value === "" ? null : Number(input.value))
        );
        row.appendChild(input);
        if (field.unit_of_measurement) {
          const unit = document.createElement("span");
          unit.textContent = field.unit_of_measurement;
          row.appendChild(unit);
        }
        return row;
      }
      case "entity": {
        // Home Assistant's own picker when it can be had: it searches, shows
        // areas and icons, and is the control people already know.
        const tag = field.multiple ? "ha-entities-picker" : "ha-entity-picker";
        if (this._hass && customElements.get(tag)) {
          const picker = document.createElement(tag);
          picker.hass = this._hass;
          picker.allowCustomEntity = false;
          if (field.domains) picker.includeDomains = field.domains;
          if (field.multiple) picker.value = Array.isArray(value) ? value : [];
          else picker.value = value ?? "";
          picker.addEventListener("value-changed", (event) => {
            event.stopPropagation();
            this._set(field.key, event.detail.value);
          });
          return picker;
        }
        return this._plainSelect(field, value, this._entities(field));
      }
      case "select": {
        const options = this._choicesFor(field);
        return this._plainSelect(field, value, options);
      }
      case "area": {
        if (this._hass && customElements.get("ha-area-picker")) {
          const picker = document.createElement("ha-area-picker");
          picker.hass = this._hass;
          picker.value = value ?? "";
          picker.addEventListener("value-changed", (event) => {
            event.stopPropagation();
            this._set(field.key, event.detail.value);
          });
          return picker;
        }
        return this._plainText(field, value);
      }
      case "icon": {
        // Home Assistant's icon picker: it searches, previews and knows every
        // mdi name, which a text field emphatically does not.
        if (this._hass && customElements.get("ha-icon-picker")) {
          const picker = document.createElement("ha-icon-picker");
          picker.hass = this._hass;
          picker.value = value ?? "";
          picker.addEventListener("value-changed", (event) => {
            event.stopPropagation();
            this._set(field.key, event.detail.value);
          });
          return picker;
        }
        return this._plainText(field, value);
      }
      case "color": {
        const wheel = document.createElement("bl-color-wheel");
        const [hue, saturation] = rgbToHs(value || [255, 140, 40]);
        wheel.setAttribute("hue", hue);
        wheel.setAttribute("saturation", saturation);
        wheel.addEventListener("value-changed", (event) =>
          this._set(field.key, hsToRgb(event.detail.hue, event.detail.saturation))
        );
        return wheel;
      }
      default:
        return this._plainText(field, value);
    }
  }

  /**
   * The field as Home Assistant would render it, or nothing.
   *
   * Runtime choices are spliced in here rather than sent from the backend:
   * which scenes a room has is not knowable when the schema is built.
   */
  _nativeControl(field, value) {
    if (!this._hass || !field.selector || !customElements.get("ha-selector")) {
      return null;
    }
    let selector = field.selector;
    if (field.options_key) {
      const options = (this._choices[field.options_key] || []).map((choice) => ({
        value: choice.value,
        label: choice.label,
      }));
      if (!options.length) return null;
      const [[kind, config]] = Object.entries(selector);
      selector = { [kind]: { ...config, options } };
    }

    const element = document.createElement("ha-selector");
    element.hass = this._hass;
    element.selector = selector;
    element.value = value ?? undefined;
    element.addEventListener("value-changed", (event) => {
      event.stopPropagation();
      this._set(field.key, event.detail.value);
    });
    return element;
  }

  /** The fallback for anything with no better control: a text field. */
  _plainText(field, value) {
    const input = document.createElement("input");
    input.type = "text";
    input.value = value ?? "";
    input.addEventListener("change", () => this._set(field.key, input.value));
    return input;
  }

  /** The fallback control: a dropdown, or a chip list when several. */
  _plainSelect(field, value, options) {
    if (field.multiple) return this._multi(field, value, options);
    const select = document.createElement("select");
    select.innerHTML =
      `<option value="">—</option>` +
      options
        .map(
          (option) =>
            `<option value="${option.value}" ${
              option.value === value ? "selected" : ""
            }>${option.label}</option>`
        )
        .join("");
    select.addEventListener("change", () =>
      this._set(field.key, select.value || null)
    );
    return select;
  }

  /** A multi-select that keeps click order, which HA's own cannot. */
  _multi(field, value, options) {
    const chosen = Array.isArray(value) ? [...value] : [];
    const box = document.createElement("div");
    const select = document.createElement("select");
    const remaining = options.filter((option) => !chosen.includes(option.value));
    select.innerHTML =
      `<option value="">+</option>` +
      remaining
        .map((option) => `<option value="${option.value}">${option.label}</option>`)
        .join("");
    select.addEventListener("change", () => {
      if (!select.value) return;
      this._set(field.key, [...chosen, select.value]);
      this._render();
    });
    box.appendChild(select);

    const chips = document.createElement("div");
    chips.className = "chips";
    chosen.forEach((item, index) => {
      const label =
        options.find((option) => option.value === item)?.label || item;
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `<span>${label}</span>`;
      const remove = document.createElement("button");
      remove.textContent = "✕";
      remove.addEventListener("click", () => {
        const next = [...chosen];
        next.splice(index, 1);
        this._set(field.key, next);
        this._render();
      });
      chip.appendChild(remove);
      chips.appendChild(chip);
    });
    box.appendChild(chips);
    return box;
  }
}

customElements.define("bl-form", BlForm);

/* ------------------------------------------------------------------ */
/* The panel.                                                           */
/* ------------------------------------------------------------------ */

class BetterLightingPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._rooms = [];
    this._modes = [];
    this._hub = {};
    this._schema = null;
    this._roomId = null;
    this._modeId = null;
    // Which screen is open: a room's section, its scenes, the global
    // settings, or a mode. The panel is one page, not a wizard.
    this._view = { kind: "room", section: null };
    this._scene = null;
    this._selectedLight = null;
    this._previewing = false;
    this._rendered = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendered) {
      this._rendered = true;
      this._shell();
      this._load();
      this._checkVersion();
    } else if (this._scene) {
      this._paintLightList();
    }
  }

  connectedCallback() {
    // A closed tab would otherwise leave the room showing a draft until
    // somebody touched a switch. Not a disaster, but not ours to leave behind.
    this._unload = () => this._stopPreview();
    window.addEventListener("beforeunload", this._unload);
    // Coming back from Home Assistant's own pages -- having just added a room
    // there -- must show the room that was added, whether or not the frontend
    // kept this element alive while we were away.
    if (this._rendered) this._load();

    this._visibility = () => {
      if (document.visibilityState === "visible") this._checkVersion();
    };
    document.addEventListener("visibilitychange", this._visibility);
    // Slow: this is a courtesy, not a heartbeat.
    this._versionTimer = setInterval(() => this._checkVersion(), 120000);
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._unload);
    document.removeEventListener("visibilitychange", this._visibility);
    clearInterval(this._versionTimer);
    clearInterval(this._countdown);
    for (const off of this._unsubscribers || []) {
      try {
        off();
      } catch {
        // Already gone with the connection.
      }
    }
    this._unsubscribers = [];
    this._watching = false;
    this._stopPreview();
  }

  async _call(type, payload = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `better_lighting/${type}`,
      ...payload,
    });
  }

  /**
   * Notice when the script on disk is no longer the one running here.
   *
   * The same problem Home Assistant has with its own frontend, and the same
   * answer: an open tab cannot be updated in place, so it is told, and offered
   * the reload rather than left to wonder why the new settings are missing.
   */
  async _checkVersion() {
    if (!OWN_VERSION || this._stale) return;
    try {
      const { panel } = await this._call("version");
      if (panel && panel !== OWN_VERSION) {
        this._stale = true;
        this._showUpdateBanner();
      }
    } catch {
      // Offline, restarting, or an older backend. Nothing worth saying.
    }
  }

  _showUpdateBanner() {
    if (this.shadowRoot.getElementById("update")) return;
    const bar = document.createElement("div");
    bar.id = "update";
    bar.className = "update";
    bar.innerHTML = `<span class="grow">${this._t("update_available")}</span>
      <span id="countdown" class="muted"></span>
      <button id="reload">${this._t("reload_now")}</button>`;
    this.shadowRoot.insertBefore(bar, this.shadowRoot.querySelector(".body"));

    bar.querySelector("#reload").addEventListener("click", () =>
      location.reload()
    );
    // A countdown rather than a reload out of nowhere: somebody halfway
    // through a scene should get the chance to press Save first.
    let left = 30;
    const tick = () => {
      bar.querySelector("#countdown").textContent = this._t(
        "reloading_in"
      ).replace("{seconds}", String(left));
      if (left-- <= 0) {
        clearInterval(this._countdown);
        location.reload();
      }
    };
    tick();
    this._countdown = setInterval(tick, 1000);
  }

  async _load() {
    if (!this._schema) {
      this._schema = await this._call("schema", {
        language: this._hass?.language || "en",
      });
    }
    const { rooms, modes, hub } = await this._call("config");
    this._rooms = rooms;
    this._modes = modes || [];
    this._hub = hub || {};
    if (!this._roomId && rooms.length) this._roomId = rooms[0].id;
    this._paint();
  }

  get _mode() {
    return this._modes.find((mode) => mode.id === this._modeId) || null;
  }

  /**
   * A standalone choice, drawn the way Home Assistant draws one.
   *
   * The forms get this through ha-selector already; the pickers built by
   * hand -- add a light, add a scene to a switch's order, what a light does
   * in a scene -- did not, and looked it.
   */
  _choiceControl(options, value, onChange, placeholder) {
    if (this._hass && customElements.get("ha-selector")) {
      const element = document.createElement("ha-selector");
      element.hass = this._hass;
      element.selector = { select: { options, mode: "dropdown", sort: false } };
      element.value = value ?? undefined;
      if (placeholder) element.label = placeholder;
      element.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        if (event.detail.value !== undefined) onChange(event.detail.value);
      });
      return element;
    }
    const select = document.createElement("select");
    select.innerHTML =
      (placeholder ? `<option value="">${placeholder}</option>` : "") +
      options
        .map(
          (option) =>
            `<option value="${option.value}" ${
              option.value === value ? "selected" : ""
            }>${option.label}</option>`
        )
        .join("");
    select.addEventListener("change", () => {
      if (select.value) onChange(select.value);
    });
    return select;
  }

  /**
   * Home Assistant's entity picker, narrowed to the lights it may offer.
   *
   * include_entities rather than a domain filter: a scene belongs to a room,
   * and the room's lights are the only ones it could sensibly name. So this
   * is its searchable picker with the right names and icons, over the right
   * short list.
   */
  _entityControl(entityIds, onChange) {
    if (this._hass && customElements.get("ha-selector")) {
      const element = document.createElement("ha-selector");
      element.hass = this._hass;
      element.selector = { entity: { include_entities: entityIds } };
      element.label = this._t("add_light");
      element.value = undefined;
      element.addEventListener("value-changed", (event) => {
        event.stopPropagation();
        if (event.detail.value) onChange(event.detail.value);
      });
      return element;
    }
    return this._choiceControl(
      entityIds.map((id) => ({ value: id, label: this._name(id) })),
      null,
      onChange,
      this._t("add_light")
    );
  }

  /** One of the panel's own strings, in the language the browser asked for. */
  _t(key) {
    return this._schema?.ui?.[key] ?? key;
  }

  get _labels() {
    return this._schema?.labels || { data: {}, descriptions: {}, options: {}, sections: {} };
  }

  /**
   * What every room and mode currently believes, and how it got there.
   *
   * The state comes from the same dump the diagnostics download produces --
   * a page that disagreed with the file attached to a bug report would be
   * worse than no page. What it adds is the events beside it: a room's mode
   * tells you where it ended up, and the log tells you which press or which
   * film put it there.
   */
  async _paintDiagnostics() {
    const main = this.shadowRoot.getElementById("main");
    const data = await this._call("diagnostics");
    const roomName = Object.fromEntries(
      this._rooms.map((room) => [room.id, room.name])
    );

    const rows = (entries) =>
      entries
        .filter(([, value]) => value !== undefined)
        .map(
          ([label, value]) =>
            `<tr><th>${label}</th><td>${
              value === null || value === "" ? "—" : value
            }</td></tr>`
        )
        .join("");

    const rooms = Object.entries(data.zones || {})
      .map(([id, zone]) => {
        const manual = Object.entries(zone.manual || {});
        return `<details class="diag">
          <summary>${zone.name || roomName[id] || id}</summary>
          <table>${rows([
            [this._t("diag_mode"), zone.mode],
            [this._t("diag_effective"), zone.effective_mode],
            [this._t("diag_scene"), zone.active_scene_id],
            [this._t("diag_adaptive"), zone.adaptive_enabled],
            [this._t("diag_night"), zone.night_active],
            [this._t("diag_insect"), zone.insect_active],
            [this._t("diag_bias"), zone.bias_pct],
            [this._t("diag_owner"), zone.session_owner],
            [
              this._t("diag_manual"),
              manual.length
                ? manual.map(([light, axes]) => `${light}: ${axes}`).join("<br>")
                : this._t("no_manual"),
            ],
            [this._t("diag_presence"), zone.presence ? JSON.stringify(zone.presence) : undefined],
            [this._t("diag_window"), zone.window_open],
          ])}</table>
        </details>`;
      })
      .join("");

    const modes = Object.entries(data.modes || {})
      .map(
        ([, mode]) => `<details class="diag">
          <summary>${mode.name}</summary>
          <table>${rows([
            [this._t("diag_state"), mode.state],
            [this._t("diag_enabled"), mode.enabled],
            [this._t("diag_session"), mode.session_id],
            [this._t("diag_opted_out"), (mode.opted_out || []).join(", ")],
            [this._t("diag_deferred"), (mode.deferred || []).length],
          ])}</table>
        </details>`
      )
      .join("");

    main.innerHTML = `
      <div class="card">
        <h2>${this._t("diagnostics")}</h2>
        <div class="bar"><button class="flat" id="refresh">${this._t(
          "refresh"
        )}</button></div>
        ${rooms}
        ${modes}
      </div>
      <div class="card">
        <h2>${this._t("the_curve")}</h2>
        <div id="curve"></div>
      </div>
      <div class="card">
        <h2>${this._t("live_events")}</h2>
        <div id="events" class="log"><p class="muted">${this._t(
          "waiting_for_events"
        )}</p></div>
      </div>`;

    main.querySelector("#refresh").addEventListener("click", () =>
      this._paintDiagnostics()
    );
    this._paintCurve(main.querySelector("#curve"));
    this._watchEvents();
  }

  /**
   * The adaptive curve, drawn.
   *
   * Plain SVG: a line for brightness and a band behind it painted with the
   * colour temperature at each point, so the shape and the warmth are the
   * same picture. Markers for sunrise, sunset and now, because the question
   * being asked is almost always "what is it doing at this hour".
   */
  async _paintCurve(into) {
    if (!this._roomId) {
      into.innerHTML = `<p class="muted">${this._t("pick_a_room")}</p>`;
      return;
    }
    const data = await this._call("curve", { zone_id: this._roomId });
    const samples = data.samples || [];
    if (!samples.length) return;

    const width = 720;
    const height = 200;
    const at = (iso) => new Date(iso).getTime();
    const first = at(samples[0].at);
    const span = at(samples[samples.length - 1].at) - first || 1;
    const x = (iso) => ((at(iso) - first) / span) * width;
    const y = (pct) => height - (pct / 100) * height;

    // The colour band: one thin rectangle per sample, painted with what the
    // curve says the light should be at that moment.
    const band = samples
      .map((point, index) => {
        const next = samples[index + 1];
        const left = x(point.at);
        const right = next ? x(next.at) : width;
        const [r, g, b] = kelvinToRgb(point.color_temp_kelvin);
        return `<rect x="${left}" y="0" width="${Math.max(
          1,
          right - left
        )}" height="${height}" fill="rgb(${r},${g},${b})" opacity="0.35"/>`;
      })
      .join("");

    const line = samples
      .map((point, index) => `${index ? "L" : "M"}${x(point.at)},${y(point.brightness_pct)}`)
      .join(" ");

    const marker = (iso, label, dashed) => {
      const at = x(iso);
      return `<line x1="${at}" y1="0" x2="${at}" y2="${height}"
                stroke="currentColor" stroke-opacity="0.5"
                ${dashed ? 'stroke-dasharray="4 4"' : ""}/>
        <text x="${at + 4}" y="14" font-size="11" fill="currentColor"
              fill-opacity="0.7">${label}</text>`;
    };

    into.innerHTML = `
      <p class="muted">${this._t("curve_hint")}</p>
      <svg viewBox="0 0 ${width} ${height}" class="curve" preserveAspectRatio="none">
        ${band}
        <path d="${line}" fill="none" stroke="currentColor" stroke-width="2"/>
        ${marker(data.events.sunrise, this._t("sunrise"), true)}
        ${marker(data.events.sunset, this._t("sunset"), true)}
        ${marker(data.now.at, this._t("now"), false)}
      </svg>
      <table class="curve-now">
        <tr><th>${this._t("now")}</th>
            <td>${data.now.brightness_pct}% · ${data.now.color_temp_kelvin} K</td></tr>
      </table>
      <h3>${this._t("sending_now")}</h3>
      <table class="curve-lights">${(data.lights || [])
        .map(
          (light) =>
            `<tr><th>${this._name(light.entity_id)}</th><td>${
              light.brightness ?? "—"
            }${
              light.color_temp_kelvin ? ` · ${light.color_temp_kelvin} K` : ""
            }</td></tr>`
        )
        .join("")}</table>`;
  }

  /** Subscribe to the events the integration fires, and keep the last few. */
  async _watchEvents() {
    this._events = this._events || [];
    this._renderEvents();
    if (this._watching) return;
    this._watching = true;

    const kinds = [
      "better_lighting_press",
      "better_lighting_zone_mode_changed",
      "better_lighting_mode_changed",
      "better_lighting_deferred_action",
      "better_lighting_zone_opted_out",
    ];
    for (const kind of kinds) {
      try {
        const off = await this._hass.connection.subscribeEvents((event) => {
          this._events.unshift({
            at: new Date().toLocaleTimeString(),
            kind: kind.replace("better_lighting_", ""),
            data: event.data,
          });
          // A log that grows forever is a memory leak with a nice name.
          this._events = this._events.slice(0, 50);
          this._renderEvents();
        }, kind);
        (this._unsubscribers = this._unsubscribers || []).push(off);
      } catch {
        // An event nobody has fired yet is not an error.
      }
    }
  }

  _renderEvents() {
    const log = this.shadowRoot.getElementById("events");
    if (!log) return;
    if (!this._events.length) return;
    log.innerHTML = this._events
      .map(
        (event) =>
          `<div class="entry"><span class="muted">${event.at}</span>
             <strong>${event.kind}</strong>
             <span class="muted">${JSON.stringify(event.data)}</span></div>`
      )
      .join("");
  }

  /**
   * Where you are, and the one way back.
   *
   * Sub-pages used to carry their own back button, which meant several
   * different buttons doing the same thing and none of them saying what you
   * would go back *to*. One strip above the content says both.
   */
  _trail() {
    const view = this._view;
    const room = this._room;
    const mode = this._mode;
    const named = this._labels.sections;
    const to = (next) => () => {
      this._view = next;
      this._paint();
    };

    if (this._scene) {
      return {
        parts: [room?.name, named.scenes, this._scene.name],
        back: async () => {
          await this._stopPreview();
          this._scene = null;
          this._view = { kind: "scenes" };
          this._paint();
        },
      };
    }

    switch (view.kind) {
      case "hub":
        return { parts: [this._t("global_settings")] };
      case "diagnostics":
        return { parts: [this._t("diagnostics")] };
      case "presets": {
        if (view.index === undefined) return { parts: [this._t("colour_presets")] };
        const preset = (this._hub.color_presets || [])[view.index];
        return {
          parts: [this._t("colour_presets"), preset?.name],
          back: to({ kind: "presets" }),
        };
      }
      case "scenes":
        if (view.sub === "import") {
          return {
            parts: [room?.name, named.scenes, this._t("import_scenes")],
            back: to({ kind: "scenes" }),
          };
        }
        return { parts: [room?.name, named.scenes] };
      case "switches": {
        const item = (room?.data.switches || [])[view.index];
        if (view.sub === "order") {
          return {
            parts: [room?.name, named.switches, item?.name, this._t("what_it_cycles")],
            back: to({ kind: "switches", index: view.index }),
          };
        }
        if (view.index !== undefined) {
          return {
            parts: [room?.name, named.switches, item?.name],
            back: to({ kind: "switches" }),
          };
        }
        return { parts: [room?.name, named.switches] };
      }
      case "calibrations": {
        const item = (room?.data.light_profiles || [])[view.index];
        if (view.index !== undefined) {
          return {
            parts: [room?.name, named.calibrations, item?.light_entity],
            back: to({ kind: "calibrations" }),
          };
        }
        return { parts: [room?.name, named.calibrations] };
      }
      case "mode":
        if (view.creating) return { parts: [this._t("add_mode")] };
        if (view.rule !== undefined) {
          return { parts: [mode?.name, named.rules], back: to({ kind: "mode" }) };
        }
        return { parts: [mode?.name] };
      default: {
        if (view.creating) return { parts: [this._t("add_room")] };
        const groups = this._schema?.forms.zone || [];
        const group =
          groups.find((g) => g.section === view.section) || groups[0];
        return {
          parts: [room?.name, named[group?.section] || group?.section],
        };
      }
    }
  }

  _paintCrumbs() {
    const holder = this.shadowRoot.getElementById("crumbs");
    if (!holder) return;
    const trail = this._trail();
    // Always present, even at the top: a strip that comes and goes shifts
    // everything below it every time you move, which reads as a glitch.
    holder.innerHTML = `<button class="crumb-back" id="crumb-back" ${
      trail.back ? "" : "disabled"
    }>&lsaquo;</button>
      <span class="crumb-trail">${(trail.parts || [])
        .filter(Boolean)
        .join(" &rsaquo; ")}</span>`;
    if (trail.back) {
      holder.querySelector("#crumb-back").addEventListener("click", trail.back);
    }
  }

  /** The runtime choices a field may ask for, for the room in hand. */
  _choices(room) {
    return {
      scenes: (room?.scenes || []).map((scene) => ({
        value: scene.scene_id,
        label: scene.name,
      })),
      zones: this._rooms.map((r) => ({ value: r.id, label: r.name })),
      lights: (room?.lights || []).map((id) => ({ value: id, label: id })),
    };
  }

  get _room() {
    return this._rooms.find((room) => room.id === this._roomId) || null;
  }

  _shell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%;
                background:var(--primary-background-color);
                color:var(--primary-text-color);
                font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        header { display:flex; align-items:center; gap:16px; height:56px; padding:0 16px;
                 background:var(--app-header-background-color, var(--primary-color));
                 color:var(--app-header-text-color, #fff); font-size:20px; }
        .body { display:grid; grid-template-columns:240px 1fr; gap:16px; padding:16px;
                align-items:start; }
        @media (max-width:800px) { .body { grid-template-columns:1fr; } }
        .card { background:var(--card-background-color,#fff); border-radius:12px;
                padding:16px; box-shadow:var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.1)); }
        /* Stacked cards -- a mode and its rules, the diagnostics tables and
           the curve -- were flush against each other, which read as one card
           with a line through it rather than two things. */
        #main > * + *, .editor > .card + .card { margin-top:16px; }
        .card > :last-child { margin-bottom:0; }
        h2 { margin:0 0 12px; font-size:16px; font-weight:500; }
        ul { list-style:none; margin:0 0 4px; padding:0; }
        li { padding:10px 12px; border-radius:8px; cursor:pointer; }
        li:hover { background:var(--secondary-background-color); }
        li[aria-selected="true"] { background:var(--primary-color); color:#fff; }
        ul.sub { margin:2px 0 8px 12px; border-left:2px solid var(--divider-color,#ddd); }
        ul.sub li { font-size:14px; padding:7px 10px; }
        h3 { margin:18px 0 10px; font-size:15px; font-weight:500; }
        /* A boxed accordion, in the shape Home Assistant's expansion panel
           uses: the title on the left, a chevron on the right, and the
           contents inside the box rather than running under the next one. */
        details { border:1px solid var(--divider-color,#3d3d3d); border-radius:12px;
                  margin-bottom:12px; overflow:hidden;
                  background:var(--ha-card-background, var(--card-background-color)); }
        details:last-of-type { margin-bottom:0; }
        summary { cursor:pointer; padding:14px 16px; font-size:15px; font-weight:500;
                  list-style:none; display:flex; align-items:center;
                  justify-content:space-between; gap:12px; }
        summary::-webkit-details-marker { display:none; }
        summary::after { content:""; flex:0 0 auto; width:9px; height:9px;
                         margin-right:4px; border-right:2px solid currentColor;
                         border-bottom:2px solid currentColor;
                         transform:rotate(45deg) translate(-2px,-2px);
                         transition:transform .15s; }
        details[open] > summary::after { transform:rotate(225deg) translate(-3px,-3px); }
        details[open] > summary { border-bottom:1px solid var(--divider-color,#3d3d3d); }
        details > bl-form { display:block; padding:16px; }
        .light { display:flex; align-items:center; gap:12px; padding:8px 12px;
                 border-radius:8px; cursor:pointer; }
        .light[aria-selected="true"] { outline:2px solid var(--primary-color); }
        .swatch { width:22px; height:22px; border-radius:50%; flex:0 0 auto;
                  border:1px solid rgba(0,0,0,.2); }
        .grow { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
                white-space:nowrap; }
        .muted { color:var(--secondary-text-color); font-size:13px; }
        button { font:inherit; padding:8px 14px; border-radius:8px; border:none;
                 cursor:pointer; background:var(--primary-color); color:#fff; }
        button.flat { background:transparent; color:var(--primary-color); }
        button.danger { background:var(--error-color,#db4437); }
        .bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:16px; }
        .card > p.muted:first-of-type { margin-top:0; }
        input[type=text] { font:inherit; padding:8px; border-radius:8px; width:100%;
                           border:1px solid var(--divider-color,#ccc);
                           background:var(--card-background-color); color:inherit; }
        .editor { display:grid; grid-template-columns:1fr 300px; gap:16px; }
        @media (max-width:1000px) { .editor { grid-template-columns:1fr; } }
        .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px;
                background:var(--secondary-background-color); }
        .update { display:flex; align-items:center; gap:12px; padding:12px 16px;
                  background:var(--info-color,#3f9bd4); color:#fff; }
        .update button { background:#fff; color:var(--primary-text-color,#111); }
        .update .muted { color:rgba(255,255,255,.85); }
        .banner { display:flex; align-items:center; gap:16px; margin-bottom:16px;
                  border-left:4px solid var(--info-color,#3f9bd4); }
        .banner.live { border-left-color:var(--success-color,#43a047); }
        .light select { font:inherit; padding:5px 8px; border-radius:8px;
                        border:1px solid var(--divider-color,#ccc);
                        background:var(--card-background-color); color:inherit; }
        .live { background:var(--success-color,#43a047); }
      </style>
      <header><span>Better Lighting</span></header>
      <div class="body">
        <div class="card" id="rooms"></div>
        <div><div id="crumbs"></div><div id="main"></div></div>
      </div>`;
  }

  _paint() {
    const nav = this.shadowRoot.getElementById("rooms");
    const labels = this._labels;
    const sections = (this._schema?.forms.zone || []).map((group) => group.section);

    // No section chosen means the first one, which is what the content pane
    // falls back to -- so the highlight has to agree with it, or a room opens
    // showing Group behaviour with nothing in the list marked.
    const current = this._view.section || sections[0];
    const roomRows = this._rooms
      .map((room) => {
        const open = room.id === this._roomId && this._view.kind !== "hub" &&
          this._view.kind !== "mode";
        const children = open
          ? `<ul class="sub">${[
              ...sections.map(
                (section) =>
                  `<li data-section="${section}" aria-selected="${
                    this._view.kind === "room" && current === section
                  }">${labels.sections[section] || section}</li>`
              ),
              `<li data-section="scenes" aria-selected="${
                this._view.kind === "scenes"
              }">${labels.sections.scenes || "🎨 Scenes"}</li>`,
              `<li data-section="switches" aria-selected="${
                this._view.kind === "switches"
              }">${labels.sections.switches || "🎚️ Switches"}</li>`,
              `<li data-section="calibrations" aria-selected="${
                this._view.kind === "calibrations"
              }">${labels.sections.calibrations || "📐 Calibration"}</li>`,
            ].join("")}</ul>`
          : "";
        return `<li class="room" data-room="${room.id}" aria-selected="${open}">${room.name}</li>${children}`;
      })
      .join("");

    nav.innerHTML = `
      <h2>${this._t("rooms")}</h2>
      <ul>${roomRows}</ul>
      <div class="bar"><button class="flat" id="add-room">${this._t("add_room")}</button></div>
      <h2 style="margin-top:20px">${this._t("modes")}</h2>
      <ul>${this._modes
        .map(
          (mode) =>
            `<li data-mode="${mode.id}" aria-selected="${
              this._view.kind === "mode" && this._modeId === mode.id
            }">${mode.name}</li>`
        )
        .join("")}</ul>
      <div class="bar"><button class="flat" id="add-mode">${this._t("add_mode")}</button></div>
      <ul style="margin-top:20px">
        <li data-hub="1" aria-selected="${this._view.kind === "hub"}">⚙️ ${this._t("global_settings")}</li>
        <li data-presets="1" aria-selected="${
          this._view.kind === "presets"
        }">${labels.sections.presets || `🎨 ${this._t("colour_presets")}`}</li>
        <li data-diagnostics="1" aria-selected="${
          this._view.kind === "diagnostics"
        }">🩺 ${this._t("diagnostics")}</li>
      </ul>`;

    nav.querySelectorAll("li.room").forEach((item) =>
      item.addEventListener("click", () => {
        this._stopPreview();
        this._roomId = item.dataset.room;
        this._view = { kind: "room", section: null };
        this._scene = null;
        this._paint();
      })
    );
    nav.querySelectorAll("li[data-section]").forEach((item) =>
      item.addEventListener("click", (event) => {
        event.stopPropagation();
        const section = item.dataset.section;
        this._stopPreview();
        this._scene = null;
        this._view = ["scenes", "switches", "calibrations"].includes(section)
          ? { kind: section }
          : { kind: "room", section };
        this._paint();
      })
    );
    nav.querySelectorAll("li[data-mode]").forEach((item) =>
      item.addEventListener("click", () => {
        this._modeId = item.dataset.mode;
        this._view = { kind: "mode" };
        this._paint();
      })
    );
    nav.querySelector("li[data-hub]").addEventListener("click", () => {
      this._view = { kind: "hub" };
      this._paint();
    });
    nav.querySelector("li[data-presets]").addEventListener("click", () => {
      this._view = { kind: "presets" };
      this._paint();
    });
    nav.querySelector("li[data-diagnostics]").addEventListener("click", () => {
      this._view = { kind: "diagnostics" };
      this._paint();
    });
    nav.querySelector("#add-room").addEventListener("click", () => {
      this._roomId = null;
      this._view = { kind: "room", section: null, creating: true };
      this._paint();
    });
    nav.querySelector("#add-mode").addEventListener("click", () => {
      this._modeId = null;
      this._view = { kind: "mode", creating: true };
      this._paint();
    });

    this._paintMain();
  }

  _paintMain() {
    this._paintCrumbs();
    if (this._scene) return this._paintEditor();
    switch (this._view.kind) {
      case "diagnostics":
        return this._paintDiagnostics();
      case "presets":
        return this._paintPresets();
      case "hub":
        return this._paintSettings({
          title: this._t("global_settings"),
          form: this._schema?.forms.hub || [],
          values: this._hub,
          save: (values) => this._call("save_hub", { options: values }),
        });
      case "mode":
        return this._paintMode();
      case "scenes":
        if (this._view.sub === "import") return this._paintImport();
        return this._paintScenes();
      case "switches":
        if (this._view.sub === "order") return this._paintSwitchOrder();
        return this._paintCollection("switches", "switch", this._t("switches"));
      case "calibrations":
        return this._paintCollection("light_profiles", "calibration", this._t("calibration"));
      default:
        return this._paintRoomSection();
    }
  }

  /** One room section, or the form that creates a room. */
  _paintRoomSection() {
    const groups = this._schema?.forms.zone || [];
    const creating = this._view.creating || !this._room;
    const main = this.shadowRoot.getElementById("main");

    if (creating && !this._view.creating && !this._rooms.length) {
      main.innerHTML = `<div class="card"><p class="muted">${this._t(
        "no_rooms"
      )}</p><div class="bar"><button id="add">${this._t(
        "add_room"
      )}</button></div></div>`;
      main.querySelector("#add").addEventListener("click", () => {
        this._view = { kind: "room", section: null, creating: true };
        this._paint();
      });
      return;
    }

    // A new room asks only for what it needs to exist: the rest of its
    // screens are there the moment it does.
    if (creating) {
      this._paintSettings({
        title: this._t("add_room"),
        form: groups.slice(0, 1),
        values: {},
        choices: this._choices(null),
        save: async (next) => {
          const result = await this._call("save_zone", {
            zone_id: null,
            data: next,
          });
          this._view = { kind: "room", section: null };
          return result;
        },
      });
      return;
    }

    const room = this._room;
    const group = groups.find((g) => g.section === this._view.section) || groups[0];
    const values = { ...room.data };

    this._paintSettings({
      title: `${room.name} — ${
        this._labels.sections[group.section] || group.section
      }`,
      form: [group],
      values,
      choices: this._choices(room),
      save: async (next) => {
        const result = await this._call("save_zone", {
          zone_id: room.id,
          data: { ...values, ...next },
        });
        this._view = { kind: "room", section: group.section };
        return result;
      },
      // Deleting belongs on the screen that names the room, not on the one
      // about presence sensors.
      remove:
        group.section === "basic"
          ? async () => {
              await this._call("delete_zone", { zone_id: room.id });
              this._roomId = null;
              this._view = { kind: "room", section: null };
            }
          : null,
    });
  }

  _paintMode() {
    const mode = this._mode;
    // A rule being edited is still the mode's screen: sending it somewhere
    // else left the sidebar with nothing selected and no way back but the
    // browser's.
    if (mode && this._view.rule !== undefined) return this._paintRules();

    const creating = this._view.creating || !mode;
    this._paintSettings({
      title: creating ? this._t("add_mode") : mode.name,
      form: this._schema?.forms.mode || [],
      values: creating ? { states: [] } : { ...mode.data },
      choices: this._choices(this._room),
      save: async (next) => {
        const base = creating ? { states: [] } : { ...mode.data };
        const result = await this._call("save_mode", {
          mode_id: creating ? null : mode.id,
          data: { ...base, ...next },
        });
        this._view = { kind: "mode" };
        return result;
      },
      remove: creating
        ? null
        : async () => {
            await this._call("delete_mode", { mode_id: mode.id });
            this._modeId = null;
          },
    });

    if (!creating) this._appendRules();
  }

  /** The mode's rules, listed under its settings on the same screen. */
  _appendRules() {
    const mode = this._mode;
    const main = this.shadowRoot.getElementById("main");
    const rules = mode.data.rules || [];
    const roomName = Object.fromEntries(
      this._rooms.map((room) => [room.id, room.name])
    );

    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h2>${this._labels.sections.rules || this._t("rules")}</h2>
      <ul>${rules
        .map(
          (rule, index) =>
            `<li data-rule="${index}">${
              roomName[rule.zones] || rule.zones || "—"
            }: ${rule.action || "keep"} (${(rule.mode_states || []).join(", ") || "—"})</li>`
        )
        .join("")}</ul>
      ${rules.length ? "" : `<p class="muted">${this._t("none")}</p>`}
      <div class="bar"><button id="add-rule">${this._t("add")}</button></div>`;
    main.appendChild(card);

    card.querySelectorAll("li[data-rule]").forEach((row) =>
      row.addEventListener("click", () => {
        this._view = { ...this._view, rule: Number(row.dataset.rule) };
        this._paint();
      })
    );
    card.querySelector("#add-rule").addEventListener("click", () => {
      this._view = { ...this._view, rule: rules.length };
      this._paint();
    });
  }

  /** A room's switches or calibrations: a list, and a form for one of them. */
  _paintCollection(storageKey, formKey, title) {
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    if (!room) return;
    const items = room.data[storageKey] || [];
    const index = this._view.index;

    if (index === undefined) {
      main.innerHTML = `
        <div class="card">
          <h2>${room.name} — ${title}</h2>
          <ul>${items
            .map(
              (item, i) =>
                `<li data-index="${i}">${item.name || item.light_entity || "—"}</li>`
            )
            .join("")}</ul>
          <div class="bar"><button id="add">${this._t("add")}</button></div>
        </div>`;
      main.querySelectorAll("li").forEach((row) =>
        row.addEventListener("click", () => {
          this._view = { ...this._view, index: Number(row.dataset.index) };
          this._paint();
        })
      );
      main.querySelector("#add").addEventListener("click", () => {
        this._view = { ...this._view, index: items.length };
        this._paint();
      });
      return;
    }

    this._paintSettings({
      title: `${room.name} — ${title}`,
      form: this._schema?.forms[formKey] || [],
      values: { ...(items[index] || {}) },
      choices: this._choices(room),
      save: async (next) => {
        const list = [...items];
        list[index] = { ...(items[index] || {}), ...next };
        const result = await this._call("save_zone_collection", {
          zone_id: room.id,
          key: storageKey,
          items: list,
        });
        this._view = { kind: this._view.kind };
        return result;
      },
      remove: async () => {
        const list = items.filter((_, i) => i !== index);
        await this._call("save_zone_collection", {
          zone_id: room.id,
          key: storageKey,
          items: list,
        });
        this._view = { kind: this._view.kind };
      },
      extra:
        storageKey === "switches" && index < items.length
          ? {
              label: `${this._t("what_it_cycles")} (${
                (items[index].scene_order || []).length + 1
              })`,
              go: () => {
                this._view = { ...this._view, sub: "order" };
                this._paint();
              },
            }
          : null,
    });
  }

  /**
   * A list of things, each edited by a form.
   *
   * Presets, rules and a switch's running order are all this shape, so they
   * share one implementation: show the list, or show the form for the item
   * whose index the view carries.
   */
  _paintListEditor({ title, items, formKey, choices, describe, onSave, reorder }) {
    const main = this.shadowRoot.getElementById("main");
    const index = this._view.index;

    if (index === undefined) {
      main.innerHTML = `
        <div class="card">
          <h2>${title}</h2>
          <ul id="items">${items
            .map(
              (item, i) => `<li data-index="${i}">
                  <span class="grow">${describe(item, i)}</span>
                  ${
                    reorder
                      ? `<span class="moves">
                           <button class="flat" data-up="${i}" ${
                             i === 0 ? "disabled" : ""
                           }>↑</button>
                           <button class="flat" data-down="${i}" ${
                             i === items.length - 1 ? "disabled" : ""
                           }>↓</button>
                         </span>`
                      : ""
                  }
                </li>`
            )
            .join("")}</ul>
          ${items.length ? "" : `<p class="muted">${this._t("none")}</p>`}
          <div class="bar"><button id="add">${this._t("add")}</button></div>
        </div>`;

      main.querySelectorAll("li").forEach((row) =>
        row.addEventListener("click", (event) => {
          if (event.target.dataset.up || event.target.dataset.down) return;
          this._view = { ...this._view, index: Number(row.dataset.index) };
          this._paint();
        })
      );
      main.querySelectorAll("[data-up],[data-down]").forEach((button) =>
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          const from = Number(event.target.dataset.up ?? event.target.dataset.down);
          const to = event.target.dataset.up ? from - 1 : from + 1;
          const next = [...items];
          [next[from], next[to]] = [next[to], next[from]];
          await onSave(next);
          await this._load();
        })
      );
      main.querySelector("#add").addEventListener("click", () => {
        this._view = { ...this._view, index: items.length, adding: true };
        this._paint();
      });
      return;
    }

    const current = items[index] || {};
    this._paintSettings({
      title,
      form: this._schema?.forms[formKey] || [],
      values: { ...current },
      choices,
      save: async (changed) => {
        const next = [...items];
        next[index] = { ...current, ...changed };
        await onSave(next);
        this._view = { ...this._view, index: undefined, adding: false };
      },
      remove:
        index < items.length
          ? async () => {
              await onSave(items.filter((_, i) => i !== index));
              this._view = { ...this._view, index: undefined };
            }
          : null,
    });
  }

  /**
   * Bringing Home Assistant's own scenes across.
   *
   * A scene there can cover the whole house; here one belongs to exactly one
   * room. So a scene is offered per room it touches, and importing it makes
   * one scene in each, holding only that room's lights. Anything in no room
   * is named rather than dropped quietly.
   */
  async _paintImport() {
    const main = this.shadowRoot.getElementById("main");
    const { scenes } = await this._call("importable_scenes");
    const roomName = Object.fromEntries(
      this._rooms.map((room) => [room.id, room.name])
    );

    if (!scenes.length) {
      main.innerHTML = `<div class="card"><p class="muted">${this._t(
        "nothing_to_import"
      )}</p></div>`;
      return;
    }

    main.innerHTML = `
      <div class="card">
        <h2>${this._t("import_scenes")}</h2>
        <p class="muted">${this._t("import_hint")}</p>
        <div id="list"></div>

      </div>`;

    const list = main.querySelector("#list");
    for (const item of scenes) {
      const rooms = Object.keys(item.rooms);
      const card = document.createElement("div");
      card.className = "import";
      card.innerHTML = `
        <div class="grow"><strong>${item.name}</strong>
          <div class="muted">${
            rooms.length
              ? rooms
                  .map(
                    (id) => `${roomName[id] || id} (${item.rooms[id].length})`
                  )
                  .join(" · ")
              : this._t("in_no_room")
          }</div>
          ${
            item.skipped.length
              ? `<div class="muted">${this._t("skipped_note")} ${item.skipped.join(
                  ", "
                )}</div>`
              : ""
          }
        </div>`;

      if (rooms.length) {
        const picks = document.createElement("div");
        picks.className = "chips";
        const chosen = new Set(rooms);
        for (const id of rooms) {
          const label = document.createElement("label");
          label.className = "chip";
          const box = document.createElement("input");
          box.type = "checkbox";
          box.checked = true;
          box.addEventListener("change", () =>
            box.checked ? chosen.add(id) : chosen.delete(id)
          );
          label.appendChild(box);
          label.append(roomName[id] || id);
          picks.appendChild(label);
        }
        card.appendChild(picks);

        const go = document.createElement("button");
        go.textContent = this._t("import");
        go.addEventListener("click", async () => {
          go.disabled = true;
          await this._call("import_scene", {
            entity_id: item.entity_id,
            zone_ids: [...chosen],
          });
          go.textContent = this._t("imported");
          await this._load();
        });
        card.appendChild(go);
      }
      list.appendChild(card);
    }

  }

  /** The house's named colours, stored with the global settings. */
  _paintPresets() {
    const presets = this._hub.color_presets || [];
    this._paintListEditor({
      title: this._labels.sections.presets || this._t("colour_presets"),
      items: presets,
      formKey: "preset",
      choices: {},
      describe: (preset) => {
        const swatch =
          preset.color_format === "color_temp_kelvin" && preset.color_temp_kelvin
            ? `rgb(${kelvinToRgb(preset.color_temp_kelvin).join(",")})`
            : `rgb(${(preset.rgb_color || [128, 128, 128]).join(",")})`;
        return `<span class="swatch" style="background:${swatch};display:inline-block;vertical-align:-4px;margin-right:8px"></span>${
          preset.name || "—"
        }`;
      },
      onSave: (next) =>
        this._call("save_hub", { options: { ...this._hub, color_presets: next } }),
    });
  }

  /** One rule of the current mode, edited in place. */
  _paintRules() {
    const mode = this._mode;
    const rules = mode.data.rules || [];
    const index = this._view.rule;
    const current = rules[index] || {};
    const ruleRoom = this._rooms.find((room) => room.id === current.zones) || null;

    this._paintSettings({
      title: `${mode.name} — ${this._labels.sections.rules || this._t("rules")}`,
      form: this._schema?.forms.rule || [],
      values: { ...current },
      choices: {
        ...this._choices(ruleRoom),
        mode_states: (mode.data.states || []).map((state) => ({
          value: state,
          label: state,
        })),
      },
      save: async (changed) => {
        const next = [...rules];
        next[index] = { ...current, ...changed };
        const result = await this._call("save_mode", {
          mode_id: mode.id,
          data: { ...mode.data, rules: next },
        });
        this._view = { kind: "mode" };
        return result;
      },
      remove:
        index < rules.length
          ? async () => {
              await this._call("save_mode", {
                mode_id: mode.id,
                data: {
                  ...mode.data,
                  rules: rules.filter((_, i) => i !== index),
                },
              });
              this._view = { kind: "mode" };
            }
          : null,
    });
  }

  /** The scenes one switch cycles, in order. */
  _paintSwitchOrder() {
    const room = this._room;
    const switches = room.data.switches || [];
    const item = switches[this._view.index];
    if (!item) return;
    const order = item.scene_order || [];
    const names = Object.fromEntries(
      (room.scenes || []).map((scene) => [scene.scene_id, scene.name])
    );
    const unused = (room.scenes || []).filter(
      (scene) => !order.includes(scene.scene_id)
    );
    const main = this.shadowRoot.getElementById("main");

    const save = async (next) => {
      const list = [...switches];
      list[this._view.index] = { ...item, scene_order: next };
      await this._call("save_zone_collection", {
        zone_id: room.id,
        key: "switches",
        items: list,
      });
      await this._load();
    };

    main.innerHTML = `
      <div class="card">
        <h2>${item.name || this._t("switch")} — ${this._t("what_it_cycles")}</h2>
        <p class="muted">${this._t("cycle_hint")}</p>
        <ul id="order">
          <li><span class="grow">1. ☀ ${this._t("adaptive")}</span></li>
          ${order
            .map(
              (id, i) => `<li>
                <span class="grow">${i + 2}. ${names[id] || id}</span>
                <span class="moves">
                  <button class="flat" data-up="${i}" ${i === 0 ? "disabled" : ""}>↑</button>
                  <button class="flat" data-down="${i}" ${
                    i === order.length - 1 ? "disabled" : ""
                  }>↓</button>
                  <button class="flat" data-remove="${i}">✕</button>
                </span>
              </li>`
            )
            .join("")}
        </ul>
        ${
          unused.length
            ? `<div class="bar" id="add-scene"></div>`
            : `<p class="muted">${this._t("all_scenes_used")}</p>`
        }
      </div>`;

    main.querySelectorAll("[data-up],[data-down],[data-remove]").forEach((button) =>
      button.addEventListener("click", async (event) => {
        const data = event.target.dataset;
        const next = [...order];
        if (data.remove !== undefined) {
          next.splice(Number(data.remove), 1);
        } else {
          const from = Number(data.up ?? data.down);
          const to = data.up ? from - 1 : from + 1;
          [next[from], next[to]] = [next[to], next[from]];
        }
        await save(next);
      })
    );
    const sceneAdder = main.querySelector("#add-scene");
    if (sceneAdder) {
      sceneAdder.appendChild(
        this._choiceControl(
          unused.map((scene) => ({ value: scene.scene_id, label: scene.name })),
          null,
          (chosen) => save([...order, chosen]),
          this._t("add_scene")
        )
      );
    }
  }

  /** The one settings screen: a form, a Save, and sometimes a Delete. */
  _paintSettings({ title, form, values, choices, save, remove }) {
    const main = this.shadowRoot.getElementById("main");
    main.innerHTML = `
      <div class="card">
        <h2>${title}</h2>
        <div id="error" class="muted"></div>
        <div id="form"></div>
        <div class="bar">
          <button id="save">${this._t("save")}</button>
          <button class="flat" id="cancel">${this._t("cancel")}</button>
          ${remove ? `<button class="danger" id="remove">${this._t("delete")}</button>` : ""}
        </div>
      </div>`;

    const holder = main.querySelector("#form");
    let pending = {};
    for (const group of form) {
      let parent = holder;
      if (form.length > 1) {
        // Folded, as the settings screens fold them. Everything but the first
        // group starts closed: a page that opens on twelve advanced fields is
        // the thing the menu was meant to fix.
        const fold = document.createElement("details");
        fold.open = group === form[0] || group.section === "basic";
        fold.innerHTML = `<summary>${
          this._labels.sections[group.section] || group.section
        }</summary>`;
        holder.appendChild(fold);
        parent = fold;
      }
      const element = document.createElement("bl-form");
      parent.appendChild(element);
      element.configure({
        fields: group.fields,
        values,
        labels: this._labels,
        choices,
        states: this._hass.states,
        hass: this._hass,
      });
      element.addEventListener("value-changed", (event) => {
        pending = { ...pending, [event.detail.key]: event.detail.value };
      });
    }

    main.querySelector("#save").addEventListener("click", async () => {
      try {
        await save(pending);
        await this._load();
      } catch (err) {
        main.querySelector("#error").textContent =
          err?.message || this._t("save_failed");
      }
    });
    // Cancel goes wherever the crumb would, or reloads this screen when it is
    // already the top of its branch -- either way, nothing typed is kept.
    main.querySelector("#cancel").addEventListener("click", () => {
      const trail = this._trail();
      if (trail.back) trail.back();
      else this._load();
    });
    main.querySelector("#remove")?.addEventListener("click", async () => {
      await remove();
      await this._load();
    });
  }

  _paintScenes() {
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    if (!room) {
      main.innerHTML = `<div class="card"><p class="muted">No rooms yet. Add one in Settings → Devices &amp; services → Better Lighting.</p></div>`;
      return;
    }
    main.innerHTML = `
      <div class="card">
        <h2>${room.name}</h2>
        <ul>${room.scenes
          .map(
            (scene, index) =>
              `<li data-index="${index}">${scene.name}<div class="muted">${
                Object.keys(scene.lights || {}).length
              } lights</div></li>`
          )
          .join("")}</ul>
        <div class="bar">
          <button id="new">${this._t("new_scene")}</button>
          <button class="flat" id="capture">${this._t("capture_room")}</button>
          <button class="flat" id="import">${this._t("import_scenes")}</button>
        </div>
      </div>`;

    main.querySelectorAll("li").forEach((item) =>
      item.addEventListener("click", () => {
        this._scene = JSON.parse(JSON.stringify(room.scenes[Number(item.dataset.index)]));
        this._selectedLight = room.lights[0] || null;
        this._paintEditor();
      })
    );
    main.querySelector("#new").addEventListener("click", () => {
      this._scene = { name: this._t("new_scene"), lights: {} };
      this._selectedLight = room.lights[0] || null;
      this._paintEditor();
    });
    main.querySelector("#import").addEventListener("click", () => {
      this._view = { kind: "scenes", sub: "import" };
      this._paint();
    });
    main.querySelector("#capture").addEventListener("click", () => {
      this._scene = { name: this._t("captured"), lights: this._captureRoom() };
      this._selectedLight = room.lights[0] || null;
      this._paintEditor();
    });
  }

  /** One light's current state, as a scene entry. */
  _captureOne(entityId) {
    const state = this._hass.states[entityId];
    if (!state) return null;
    if (state.state !== "on") return { action: "off" };

    const entry = { action: "apply" };
    if (state.attributes.brightness != null) {
      entry.brightness_pct =
        Math.round((state.attributes.brightness / 255) * 1000) / 10;
    }
    // Whichever colour the light is actually showing. A light in colour-temp
    // mode carries an rgb_color too, derived rather than set, and storing that
    // would freeze a warm white into a slightly-wrong orange.
    if (
      state.attributes.color_mode === "color_temp" &&
      state.attributes.color_temp_kelvin
    ) {
      entry.color_format = "color_temp_kelvin";
      entry.color_temp_kelvin = state.attributes.color_temp_kelvin;
    } else if (state.attributes.rgb_color) {
      entry.color_format = "rgb_color";
      entry.rgb_color = [...state.attributes.rgb_color];
    } else {
      entry.color_format = "none";
    }
    return entry;
  }

  /** Read the whole room's current state into per-light scene entries. */
  _captureRoom() {
    const lights = {};
    for (const entityId of this._room.lights) {
      const captured = this._captureOne(entityId);
      if (captured) lights[entityId] = captured;
    }
    return lights;
  }

  _spec(entityId) {
    if (!this._scene.lights[entityId]) {
      this._scene.lights[entityId] = { action: "apply", color_format: "inherit" };
    }
    return this._scene.lights[entityId];
  }

  _swatch(entityId) {
    const spec = this._scene.lights[entityId];
    if (!spec || spec.action === "off") return "#111";
    if (spec.color_format === "rgb_color" && spec.rgb_color) {
      return `rgb(${spec.rgb_color.join(",")})`;
    }
    if (spec.color_format === "color_temp_kelvin" && spec.color_temp_kelvin) {
      return `rgb(${kelvinToRgb(spec.color_temp_kelvin).join(",")})`;
    }
    const state = this._hass.states[entityId];
    const rgb = state?.attributes?.rgb_color;
    return rgb ? `rgb(${rgb.join(",")})` : "var(--secondary-background-color)";
  }

  /**
   * The scene editor, in the shape Home Assistant's own scene editor uses.
   *
   * Two modes, and the distinction matters. In **live mode** the scene is on
   * the actual bulbs and clicking a light opens Home Assistant's own more-info
   * dialog -- its colour wheel, its brightness and temperature sliders, its
   * favourite colours -- so the controls are the ones already learned rather
   * than an imitation of them. Saving then reads back what the lights are
   * actually doing. In **review mode** nothing is touched: rows can be added
   * and removed and their treatment set, but the room is left alone.
   */
  _paintEditor() {
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    const live = this._previewing;
    const entries = Object.keys(this._scene.lights || {});
    const available = (room.lights || []).filter((id) => !entries.includes(id));

    main.innerHTML = `
      <div class="card banner ${live ? "live" : ""}">
        <div class="grow">
          <strong>${live ? this._t("live_mode") : this._t("review_mode")}</strong>
          <div class="muted">${
            live
              ? this._t("live_hint")
              : this._t("review_hint")
          }</div>
        </div>
        <button id="mode">${live ? this._t("to_review_mode") : this._t("live_mode")}</button>
      </div>

      <div class="card">
        <h2>${this._t("scene")}</h2>
        <input type="text" id="name" value="${this._scene.name || ""}">
      </div>

      <div class="card">
        <h2>${this._t("lights")}</h2>
        <div class="muted" style="margin-bottom:12px">
          ${this._t("light_treatment")}
        </div>
        <div id="rows"></div>
        <div class="bar" id="add-light"></div>
      </div>

      <div class="card">
        <div class="bar">
          <button id="save">${this._t("save")}</button>
          <button class="flat" id="cancel">${this._t("cancel")}</button>
          <button class="flat" id="recapture">${this._t("capture_room")}</button>
          ${this._scene.scene_id ? `<button class="danger" id="delete">${this._t("delete")}</button>` : ""}
        </div>
      </div>`;

    main.querySelector("#name").addEventListener("input", (event) => {
      this._scene.name = event.target.value;
    });
    main.querySelector("#mode").addEventListener("click", () =>
      live ? this._stopPreview().then(() => this._paintEditor()) : this._preview()
    );
    if (available.length) {
      main.querySelector("#add-light").appendChild(
        this._entityControl(available, (chosen) => {
          this._spec(chosen);
          this._paintEditor();
          this._pushPreview();
        })
      );
    }
    main.querySelector("#save").addEventListener("click", () => this._save());
    main.querySelector("#cancel").addEventListener("click", async () => {
      await this._stopPreview();
      this._scene = null;
      this._view = { kind: "scenes" };
      this._load();
    });
    main.querySelector("#recapture").addEventListener("click", () => {
      this._scene.lights = this._captureRoom();
      this._paintEditor();
    });
    main.querySelector("#delete")?.addEventListener("click", async () => {
      await this._call("delete_scene", {
        zone_id: room.id,
        scene_id: this._scene.scene_id,
      });
      await this._stopPreview();
      this._scene = null;
      this._load();
    });

    this._paintLightList();
  }

  _name(entityId) {
    if (entityId === ALL) return this._t("every_light");
    return this._hass.states[entityId]?.attributes?.friendly_name || entityId;
  }

  /** The area a light sits in, shown under its name as HA's editor does. */
  _area(entityId) {
    if (entityId === ALL) return "";
    const entity = this._hass.entities?.[entityId];
    const areaId =
      entity?.area_id || this._hass.devices?.[entity?.device_id]?.area_id;
    return this._hass.areas?.[areaId]?.name || "";
  }

  _paintLightList() {
    const list = this.shadowRoot.getElementById("rows");
    if (!list || !this._scene) return;
    const live = this._previewing;
    const entries = Object.keys(this._scene.lights || {});

    // Built as elements rather than as a string, so the two choices on each
    // row can be Home Assistant's dropdown like every other one on the page.
    list.innerHTML = "";
    for (const entityId of entries) {
      const spec = this._scene.lights[entityId] || {};
      const state = this._hass.states[entityId];
      const detail =
        spec.action === "off"
          ? this._t("off")
          : spec.action === "leave"
            ? this._t("left_alone")
            : live && state?.state === "on"
              ? `${Math.round(((state.attributes.brightness || 0) / 255) * 100)}%`
              : spec.brightness_pct != null
                ? `${spec.brightness_pct}%`
                : "—";

      const row = document.createElement("div");
      row.className = "light";
      row.dataset.light = entityId;
      row.innerHTML = `
        <span class="swatch" style="background:${this._swatch(entityId)}"></span>
        <span class="grow">
          ${this._name(entityId)}
          <div class="muted">${this._area(entityId) || detail}</div>
        </span>`;

      row.appendChild(
        this._choiceControl(
          [
            { value: "apply", label: this._t("set_it") },
            { value: "off", label: this._t("switch_it_off") },
            { value: "leave", label: this._t("leave_it_alone") },
          ],
          spec.action || "apply",
          (chosen) => {
            this._spec(entityId).action = chosen;
            this._paintLightList();
            this._pushPreview();
          }
        )
      );
      row.appendChild(
        this._choiceControl(
          [
            { value: "inherit", label: this._t("colour_as_set") },
            { value: "none", label: this._t("colour_follows_sun") },
          ],
          spec.color_format === "none" ? "none" : "inherit",
          (chosen) => {
            const entry = this._spec(entityId);
            if (chosen === "none") entry.color_format = "none";
            else delete entry.color_format;
            this._pushPreview();
          }
        )
      );

      const remove = document.createElement("button");
      remove.className = "flat";
      remove.title = this._t("remove");
      remove.textContent = "🗑";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        delete this._scene.lights[entityId];
        this._paintEditor();
        this._pushPreview();
      });
      row.appendChild(remove);

      row.addEventListener("click", (event) => {
        if (event.target.closest("select,button,ha-selector")) return;
        if (entityId === ALL || !live) return;
        // Home Assistant's own dialog, with the controls already learned.
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId },
            bubbles: true,
            composed: true,
          })
        );
      });
      list.appendChild(row);
    }
  }

  /**
   * Saving in live mode reads the lights back.
   *
   * What is on the wall is what gets stored, which is the point of editing
   * this way: the scene cannot disagree with what you were looking at when
   * you pressed Save.
   */
  async _save() {
    if (this._previewing) {
      for (const [entityId, spec] of Object.entries(this._scene.lights)) {
        if (spec.action && spec.action !== "apply") continue;
        if (entityId === ALL) continue;
        const captured = this._captureOne(entityId);
        if (captured) {
          this._scene.lights[entityId] = {
            ...captured,
            // A light told to follow the sun keeps doing so.
            ...(spec.color_format === "none" ? { color_format: "none" } : {}),
          };
        }
      }
    }
    const { scene_id: sceneId } = await this._call("save_scene", {
      zone_id: this._roomId,
      scene: this._scene,
    });
    this._scene.scene_id = sceneId;
    await this._stopPreview();
    this._scene = null;
    this._load();
  }

  async _preview() {
    this._previewing = true;
    await this._pushPreview();
    this._paintEditor();
  }

  async _pushPreview() {
    if (!this._previewing) return;
    await this._call("preview", { zone_id: this._roomId, scene: this._scene });
  }

  async _stopPreview() {
    if (!this._previewing) return;
    this._previewing = false;
    await this._call("stop_preview", { zone_id: this._roomId });
  }

}

customElements.define("better-lighting-panel", BetterLightingPanel);
