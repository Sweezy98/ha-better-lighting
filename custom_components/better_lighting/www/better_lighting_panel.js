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

// Adaptive's place in a switch's list, written the way a scene is. Matches
// ADAPTIVE_STEP on the Python side.
const ADAPTIVE_STEP = "__adaptive__";

// The effects that ship with the integration, matching effects.py. Named
// here rather than fetched, because they are the same in every house.
const BUILT_IN_EFFECTS = [
  {
    id: "solid",
    label: "effect_solid",
    repeat: false,
    steps: [{ level: 100, transition: 0.4, hold: 0 }],
  },
  {
    id: "flash",
    label: "effect_flash",
    repeat: true,
    steps: [
      { level: 100, transition: 0, hold: 0.25 },
      { level: 5, transition: 0, hold: 0.25 },
    ],
  },
  {
    id: "pulse",
    label: "effect_pulse",
    repeat: true,
    steps: [
      { level: 100, transition: 0.35, hold: 0.1 },
      { level: 35, transition: 0.35, hold: 0.1 },
    ],
  },
  {
    id: "breathe",
    label: "effect_breathe",
    repeat: true,
    steps: [
      { level: 100, transition: 1.1, hold: 0.15 },
      { level: 22, transition: 1.1, hold: 0.15 },
    ],
  },
  {
    id: "candle",
    label: "effect_candle",
    repeat: true,
    steps: [
      { level: 100, transition: 0.18, hold: 0.22 },
      { level: 74, transition: 0.12, hold: 0.1 },
      { level: 90, transition: 0.22, hold: 0.35 },
      { level: 60, transition: 0.1, hold: 0.08 },
      { level: 86, transition: 0.3, hold: 0.2 },
    ],
  },
];

// Where Home Assistant keeps every integration's icon. Linked rather than
// shipped, so replacing the icon is a change there rather than a release
// here -- and until this integration is listed, nothing loads and the
// panel's own icon is shown instead.
const BRAND_ICON = "https://brands.home-assistant.io/better_lighting/icon.png";
// Ours, served beside this script. Used when the registry has nothing yet --
// which is every install until the brands pull request lands, and every
// install with no way out to the internet.
const OWN_ICON = new URL("icon.png", import.meta.url).href;

// The fingerprint this copy was served under, taken from its own URL. The
// backend stamps the URL with a hash of the file, so comparing the two is how
// an open page learns it has been superseded.
const OWN_VERSION = new URL(import.meta.url).searchParams.get("v");

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

// The menu narrower than this is not a menu, so dragging past it means the
// rail instead; and never wider than a quarter, since the content is what the
// page is for.
const NAV_MIN = 260;
const NAV_RAIL = 56;
const navMax = () => Math.max(NAV_MIN, Math.round(window.innerWidth / 4));

/** A remembered number, or the default. Storage can be blocked or empty. */
function _remembered(key, fallback) {
  try {
    const stored = Number(window.localStorage.getItem(key));
    return Number.isFinite(stored) && stored ? stored : fallback;
  } catch {
    return fallback;
  }
}

function _remember(key, value) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch {
    // A private window, or storage turned off. The menu simply forgets.
  }
}

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
      "ha-icon",
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

/**
 * Home Assistant's chart component, if it can be reached.
 *
 * Separate from the controls above and loaded only by the diagnostics page:
 * it drags in ECharts, which is far too much to make every visit to the panel
 * pay for. The history-graph card is what pulls it in; when that stops working
 * the curve is drawn by hand instead, which is what happened here before.
 */
/** "HH:MM" or "HH:MM:SS" as minutes past midnight. */
function _minutes(text) {
  if (!text) return 0;
  const [hours, mins] = String(text).split(":");
  return ((Number(hours) || 0) % 24) * 60 + ((Number(mins) || 0) % 60);
}

let chartReady;
function ensureHaChart() {
  if (chartReady) return chartReady;
  chartReady = (async () => {
    if (customElements.get("ha-chart-base")) return true;
    try {
      const helpers = await window.loadCardHelpers?.();
      if (helpers) {
        await helpers.createCardElement({ type: "history-graph", entities: [] });
      }
    } catch {
      // Then the hand-drawn curve it is.
    }
    return Boolean(customElements.get("ha-chart-base"));
  })();
  return chartReady;
}

/* The icon beside each screen in the sidebar. Home Assistant's own names, so
   they match what the rest of the interface uses for the same idea. */
const SECTION_ICONS = {
  basic: "mdi:information-outline",
  group: "mdi:lightbulb-group",
  adaptive: "mdi:weather-sunny",
  night: "mdi:weather-night",
  power: "mdi:flash",
  presence: "mdi:motion-sensor",
  insect: "mdi:window-open-variant",
  advanced: "mdi:cog-outline",
  down: "mdi:arrow-down-bold-outline",
  scenes: "mdi:palette",
  switches: "mdi:light-switch",
  calibrations: "mdi:tune-variant",
  light_groups: "mdi:lightbulb-group",
  room_zones: "mdi:select-group",
  simulation: "mdi:home-account",
  rules: "mdi:lightning-bolt-outline",
  presets: "mdi:palette-swatch",
};

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
    this._disabled = new Set();
  }

  configure({ fields, values, labels, choices, states, hass, disabled }) {
    this._fields = fields || [];
    this._values = values || {};
    this._labels = labels || this._labels;
    this._choices = choices || {};
    // Fields that cannot be answered yet, because the answer they depend on
    // has not been given.
    this._disabled = new Set(disabled || []);
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
        :host { display:block; max-width:100%; }
        * { box-sizing:border-box; max-width:100%; min-width:0; }
        /* A control takes the width it is given rather than the width its
           longest option would like, and says the rest with an ellipsis. */
        ha-selector, ha-entity-picker, ha-entities-picker, ha-icon-picker,
        ha-area-picker, ha-textfield, ha-select { display:block; width:100%; }
        .field { margin-bottom:20px; min-width:0; }
        .field-text { min-width:0; }
        .switch-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
        .switch-row .field-text { flex:1 1 260px; }
        /* A toggle is as wide as a toggle. The rule above that makes a
           control take the width it is given is about the ones that would
           otherwise take more; this one would take the whole line and put
           itself underneath what it is called. */
        .switch-row .switch, .switch-row ha-selector {
          flex:0 0 auto; width:auto; display:inline-flex; }
        .switch-row .hint { margin-bottom:0; }
        label { overflow-wrap:anywhere; }
        .field.disabled { opacity:.5; pointer-events:none; }
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
      // A switch belongs beside what it is called, not underneath it. The
      // text takes a basis wide enough to be worth reading, so the row wraps
      // by itself when there is not room for both.
      if (field.kind === "boolean") wrap.classList.add("switch-row");
      wrap.innerHTML = `<div class="field-text"><label>${this._label(
        field.key
      )}</label>${hint ? `<div class="hint">${hint}</div>` : ""}</div>`;
      const control = this._control(field, value);
      if (this._disabled.has(field.key)) {
        wrap.classList.add("disabled");
        control.disabled = true;
        for (const input of control.querySelectorAll?.("input,select,button") || []) {
          input.disabled = true;
        }
      }
      wrap.appendChild(control);
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
    const [[kind, config]] = Object.entries(selector);
    if (field.options_key) {
      // Even when there is nothing to choose yet. Falling back to a plain
      // dropdown here is what left "which scene to apply" looking unlike
      // every other field in a room that has no scenes yet.
      selector = {
        [kind]: {
          ...config,
          options: (this._choices[field.options_key] || []).map((choice) => ({
            value: choice.value,
            label: choice.label,
          })),
        },
      };
    }
    // Home Assistant draws a select with few options as a column of radio
    // buttons unless it is told otherwise, which is why the scene pickers
    // looked nothing like the dropdowns beside them.
    if (kind === "select" && !config.multiple && !config.mode) {
      selector = { select: { ...selector.select, mode: "dropdown" } };
    }

    const element = document.createElement("ha-selector");
    element.hass = this._hass;
    element.selector = selector;
    element.value = value ?? undefined;
    element.addEventListener("value-changed", (event) => {
      event.stopPropagation();
      // Home Assistant's controls are controlled: they report a change and
      // wait to be given the new value back. Without this line a multi-select
      // drew the chips it started with for ever, so nothing added to one --
      // least of all a value typed in by hand -- ever appeared.
      element.value = event.detail.value;
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
    // Which screen is open: the list of rooms, one room's section, its
    // scenes, the global settings, or a mode. The panel is one page, not a
    // wizard.
    this._view = { kind: "rooms" };
    this._navOpen = false;
    // Which menu groups are folded shut. A house with a dozen rooms wants the
    // modes in reach without scrolling past all of them.
    this._collapsed = { rooms: false, modes: false };
    // The one branch of the menu that is open, by id. One at a time: going
    // somewhere else folds away what you have left, so a house with a dozen
    // rooms does not end up as a menu you have to scroll.
    //
    // Undefined means nobody has said; null means somebody shut it. Without
    // the difference, shutting the room you are standing in was immediately
    // undone by the rule that opens the room you are standing in.
    this._expanded = undefined;
    // And the one list *inside* a room that is open: its scenes, or its
    // switches. Same rule one level down.
    this._expandedSub = null;
    // How the menu is shown on a wide screen, remembered per browser. A
    // rail of icons is for somebody who knows their way around; the width
    // is for somebody whose rooms have long names.
    this._navWidth = _remembered("bl-nav-width", 260);
    this._railed = _remembered("bl-nav-rail", 0) === 1;
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
    this._pop = (event) => this._handlePop(event);
    window.addEventListener("popstate", this._pop);
    // Slow: this is a courtesy, not a heartbeat.
    this._versionTimer = setInterval(() => this._checkVersion(), 120000);
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._unload);
    if (this._pop) window.removeEventListener("popstate", this._pop);
    if (this._closeOverflow) window.removeEventListener("click", this._closeOverflow);
    document.removeEventListener("visibilitychange", this._visibility);
    clearInterval(this._versionTimer);
    clearTimeout(this._refreshTimer);
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
        this._showUpdateToast();
      }
    } catch {
      // Offline, restarting, or an older backend. Nothing worth saying.
    }
  }

  /**
   * Home Assistant's own toast, rather than a bar of our own.
   *
   * hass-notification is how everything in the frontend says this kind of
   * thing, including Home Assistant when its own new version is waiting --
   * same corner, same shape, same behaviour. Held open and undismissable,
   * because the page really is out of date until it is reloaded, and offering
   * the reload rather than taking it: somebody halfway through a scene should
   * get to press Save first.
   */
  _showUpdateToast() {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: {
          id: "better-lighting-update",
          message: this._t("update_available"),
          duration: -1,
          dismissable: false,
          action: {
            text: this._t("reload_now"),
            action: () => location.reload(),
          },
        },
        bubbles: true,
        composed: true,
      })
    );
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
    // The icons come from an element registered lazily with Home Assistant's
    // card editors, so the first paint usually happens without them.
    if (!this._iconsChecked) {
      this._iconsChecked = true;
      ensureHaControls().then(() => this._paint());
    }
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
        if (event.detail.value === undefined) return;
        element.value = event.detail.value;
        onChange(event.detail.value);
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
    this._page(
      `<div id="diag-state"></div>
       <details open>
         <summary>${this._t("the_curve")}</summary>
         <div class="fold-body" id="curve"></div>
       </details>
       <details open>
         <summary>${this._t("live_events")}</summary>
         <div class="fold-body log" id="events"><p class="muted">${this._t(
           "waiting_for_events"
         )}</p></div>
       </details>`,
      `<button class="flat" id="refresh">${this._icon(
         "mdi:refresh"
       )}<span>${this._t("refresh")}</span></button>
       <button class="flat" id="clear-log">${this._icon(
         "mdi:notification-clear-all"
       )}<span>${this._t("clear_log")}</span></button>`
    );

    main.querySelector("#refresh").addEventListener("click", () =>
      this._refreshDiagnostics()
    );
    main.querySelector("#clear-log").addEventListener("click", async () => {
      if (!(await this._confirm(this._t("clear_log")))) return;
      await this._call("activity", { clear: true });
      this._events = null;
      await this._watchEvents();
    });
    await this._refreshDiagnostics();
    this._paintCurve(main.querySelector("#curve"));
    this._watchEvents();
  }

  /**
   * Redraw what every room and mode believes, without redrawing the page.
   *
   * Which is the difference between a diagnostics page and a snapshot of
   * one: press a switch and the table beside you says what changed. Only
   * this block is replaced, so the curve is not refetched, the log is not
   * rebuilt, and an accordion somebody opened stays open.
   */
  async _refreshDiagnostics() {
    const into = this.shadowRoot.getElementById("diag-state");
    if (!into) return;
    const data = await this._call("diagnostics");
    const open = new Set(
      [...into.querySelectorAll("details[open]")].map(
        (fold) => fold.querySelector("summary")?.textContent
      )
    );
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

    const rooms = Object.entries(data.rooms || {})
      .map(([id, room]) => {
        const manual = Object.entries(room.manual || {});
        return `<details class="diag">
          <summary>${room.name || roomName[id] || id}</summary>
          <table>${rows([
            [this._t("diag_mode"), room.mode],
            [this._t("diag_effective"), room.effective_mode],
            [this._t("diag_scene"), room.active_scene_id],
            [this._t("diag_adaptive"), room.adaptive_enabled],
            [this._t("diag_night"), room.night_active],
            [this._t("diag_insect"), room.insect_active],
            [this._t("diag_bias"), room.bias_pct],
            [this._t("diag_owner"), room.session_owner],
            [
              this._t("diag_manual"),
              manual.length
                ? manual.map(([light, axes]) => `${light}: ${axes}`).join("<br>")
                : this._t("no_manual"),
            ],
            [this._t("diag_presence"), room.presence ? JSON.stringify(room.presence) : undefined],
            [this._t("diag_window"), room.window_open],
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

    // What each switch has published lately, and what we made of it. The
    // question a log cannot answer on its own: a value the switch's
    // vocabulary has no word for produces no press, and so no trace.
    const switches = Object.values(data.controllers || {})
      .map(
        (item) => `<details class="diag">
          <summary>${item.name}</summary>
          <table>${rows([
            [this._t("diag_binding"), item.binding_entity || item.binding],
            [this._t("diag_cycle"), (item.cycle || []).join(" \u2192 ")],
          ])}</table>
          ${
            (item.seen || []).length
              ? `<table>${item.seen
                  .map(
                    (seen) =>
                      `<tr><th>${new Date(
                        seen.at
                      ).toLocaleTimeString()}</th><td>${
                        seen.value || "\u2014"
                      }</td><td>${seen.read_as}</td></tr>`
                  )
                  .join("")}</table>`
              : `<p class="muted fold-body">${this._t("nothing_seen")}</p>`
          }
        </details>`
      )
      .join("");

    into.innerHTML = `${rooms}
      ${modes}
      ${
        switches
          ? `<h3>${this._labels.sections.switches || this._t("switches")}</h3>
             <p class="muted">${this._t("seen_hint")}</p>
             ${switches}`
          : ""
      }`;
    // Whatever was unfolded before stays unfolded, or watching a room would
    // mean opening it again after every press.
    for (const fold of into.querySelectorAll("details")) {
      if (open.has(fold.querySelector("summary")?.textContent)) fold.open = true;
    }
  }

  /**
   * The adaptive curve, drawn.
   *
   * Home Assistant's own chart when it can be reached, so the graph behaves
   * like every other graph in the house -- the same tooltips, the same
   * legend, the same theme. Behind it, a hand-drawn SVG: a line for
   * brightness over a band painted with the colour temperature at each
   * point. Either way, markers for sunrise, sunset and now, because the
   * question being asked is almost always "what is it doing at this hour".
   */
  async _paintCurve(into) {
    if (!this._roomId) {
      into.innerHTML = `<p class="muted">${this._t("pick_a_room")}</p>`;
      return;
    }
    const data = await this._call("curve", { room_id: this._roomId });
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
      const stroke = dashed
        ? 'stroke="currentColor" stroke-opacity="0.5" stroke-dasharray="4 4"'
        : 'stroke="var(--primary-color,#03a9f4)" stroke-width="2"';
      return `<line x1="${at}" y1="0" x2="${at}" y2="${height}" ${stroke}/>
        <text x="${at + 4}" y="14" font-size="11" fill="currentColor"
              fill-opacity="0.7">${label}</text>`;
    };

    into.innerHTML = `
      <p class="muted">${this._t("curve_hint")}</p>
      <div id="chart"></div>
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

    const chart = into.querySelector("#chart");
    if (!(await this._haChart(chart, data))) {
      chart.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" class="curve" preserveAspectRatio="none">
          ${band}
          <path d="${line}" fill="none" stroke="currentColor" stroke-width="2"/>
          ${marker(data.events.sunrise, this._t("sunrise"), true)}
          ${marker(data.events.sunset, this._t("sunset"), true)}
          ${marker(data.now.at, this._t("now"), false)}
        </svg>`;
    }
  }

  /**
   * The same curve, in Home Assistant's chart component.
   *
   * Returns whether it managed it: the component is lazily registered with
   * the history card and was never promised to a custom panel, so failing to
   * get it has to be ordinary rather than fatal.
   */
  async _haChart(into, data) {
    if (!(await ensureHaChart()) || !customElements.get("ha-chart-base")) {
      return false;
    }
    try {
      const at = (iso) => new Date(iso).getTime();
      const mark = (iso, label, dashed) => ({
        xAxis: at(iso),
        label: { formatter: label, position: "insideEndTop" },
        lineStyle: dashed
          ? { type: "dashed", opacity: 0.5 }
          : // Now is the line people are looking for, so it is the one line
            // that does not look like the others.
            { type: "solid", width: 2, color: "var(--primary-color, #03a9f4)" },
      });
      const chart = document.createElement("ha-chart-base");
      chart.hass = this._hass;
      chart.height = "260px";
      chart.data = [
        {
          id: "brightness",
          type: "line",
          name: this._t("brightness"),
          yAxisIndex: 0,
          showSymbol: false,
          smooth: true,
          data: data.samples.map((point) => [at(point.at), point.brightness_pct]),
          markLine: {
            symbol: "none",
            silent: true,
            data: [
              mark(data.events.sunrise, this._t("sunrise"), true),
              mark(data.events.sunset, this._t("sunset"), true),
              mark(data.now.at, this._t("now"), false),
            ],
          },
        },
        {
          id: "color_temp",
          type: "line",
          name: this._t("colour_temperature"),
          yAxisIndex: 1,
          showSymbol: false,
          smooth: true,
          data: data.samples.map((point) => [
            at(point.at),
            point.color_temp_kelvin,
          ]),
        },
      ];
      chart.options = {
        xAxis: { type: "time" },
        yAxis: [
          {
            type: "value",
            min: 0,
            max: 100,
            axisLabel: { formatter: "{value}%" },
            splitLine: { show: true },
          },
          {
            type: "value",
            position: "right",
            scale: true,
            axisLabel: { formatter: "{value} K" },
            splitLine: { show: false },
          },
        ],
        grid: { top: 24, bottom: 8, left: 8, right: 8, containLabel: true },
        tooltip: { trigger: "axis" },
      };
      into.appendChild(chart);
      return true;
    } catch {
      // Whatever it wanted, it is not worth the diagnostics page for.
      return false;
    }
  }

  /**
   * Something happened, so what the page says about it is out of date.
   *
   * Debounced: a burst of presses is one redraw, and the fetch behind it is
   * not run once per event.
   */
  _noteChanged() {
    if (this._view.kind !== "diagnostics") return;
    clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => this._refreshDiagnostics(), 600);
  }

  /**
   * What has happened, from before this page was opened and as it happens.
   *
   * The stored ones first: the bus remembers nothing, so a page opened after
   * the fact used to show an empty log and the impression that nothing had
   * happened at all.
   */
  async _watchEvents() {
    if (!this._events) {
      this._events = [];
      try {
        const { entries } = await this._call("activity");
        this._events = (entries || []).map((entry) => ({
          at: entry.at,
          kind: entry.kind,
          data: entry.data,
        }));
      } catch {
        // An older backend, or nothing kept. Live events still arrive.
      }
    }
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
            at: new Date().toISOString(),
            kind: kind.replace("better_lighting_", ""),
            data: event.data,
          });
          // A log that grows forever is a memory leak with a nice name.
          this._events = this._events.slice(0, 200);
          this._renderEvents();
          this._noteChanged();
        }, kind);
        (this._unsubscribers = this._unsubscribers || []).push(off);
      } catch {
        // An event nobody has fired yet is not an error.
      }
    }
  }

  /** The words and the icon for one thing that happened. */
  _describeEvent(event) {
    const data = event.data || {};
    const room = this._rooms.find(
      (candidate) => candidate.id === data.zone_id
    )?.name;
    switch (event.kind) {
      case "press":
        return {
          icon: "mdi:light-switch",
          title: `${data.controller || this._t("switch")} \u2192 ${data.kind}`,
          where: room,
        };
      case "zone_mode_changed":
        return {
          icon: "mdi:lightbulb-group",
          title: `${data.to || data.mode || "?"}${
            data.scene ? ` \u00b7 ${data.scene}` : ""
          }`,
          where: room || data.zone,
        };
      case "mode_changed":
        return {
          icon: "mdi:movie-open",
          title: `${data.mode}: ${data.from_state || "?"} \u2192 ${
            data.to_state || "?"
          }`,
          where: data.applied === false ? this._t("diag_enabled") : undefined,
        };
      case "deferred_action":
        return {
          icon: "mdi:timer-sand",
          title: `${data.action || "?"} \u00b7 ${data.status || ""} ${
            data.reason || ""
          }`.trim(),
          where: room,
        };
      case "zone_opted_out":
        return { icon: "mdi:hand-back-left", title: data.mode, where: room };
      default:
        return { icon: "mdi:information-outline", title: event.kind, where: room };
    }
  }

  /**
   * The log, in the shape Home Assistant writes its own.
   *
   * Time down the left, a ruled icon beside it, then what happened and
   * where -- rather than a line of JSON, which said everything and told you
   * nothing.
   */
  _renderEvents() {
    const log = this.shadowRoot.getElementById("events");
    if (!log) return;
    if (!this._events?.length) {
      // Said rather than skipped: leaving the last lot on screen is how
      // clearing the log looked like it had done nothing.
      log.innerHTML = `<p class="muted">${this._t("waiting_for_events")}</p>`;
      return;
    }
    log.innerHTML = this._events
      .map((event) => {
        const { icon, title, where } = this._describeEvent(event);
        const at = new Date(event.at);
        return `<div class="entry">
          <span class="when">${
            Number.isNaN(at.getTime()) ? event.at : at.toLocaleTimeString()
          }</span>
          <span class="dot">${this._icon(icon)}</span>
          <span class="what">
            <strong>${title}</strong>
            ${where ? `<div class="muted">${where}</div>` : ""}
          </span>
        </div>`;
      })
      .join("");
  }

  /**
   * Where you are, as a trail from the top.
   *
   * Every screen names its whole path -- Rooms, the room, the section -- so
   * the header says both where you are and what each step up leads to, and
   * the one back button follows the trail rather than a history of clicks.
   */
  _trail() {
    const view = this._view;
    const room = this._room;
    const mode = this._mode;
    // Leaving a screen always drops a draft: a preview left running would
    // otherwise keep the room on a scene nobody saved.
    const to = (next) => async () => {
      await this._stopPreview();
      this._scene = null;
      this._view = next;
      this._paint();
    };
    const rooms = { label: this._t("rooms"), go: to({ kind: "rooms" }) };
    const modes = { label: this._t("modes"), go: to({ kind: "modes" }) };
    const theRoom = room
      ? { label: room.name, go: to({ kind: "room", section: null }) }
      : null;
    const named = (key) => this._sectionName(key);

    switch (view.kind) {
      case "rooms":
        return [rooms];
      case "modes":
        return [modes];
      case "hub":
        return [{ label: this._t("global_settings") }];
      case "control":
        return [{ label: this._t("control") }];
      case "diagnostics":
        return [{ label: this._t("diagnostics") }];
      case "import":
        return [{ label: this._t("import_scenes") }];
      case "presets": {
        const top = { label: named("presets"), go: to({ kind: "presets" }) };
        if (view.index === undefined) return [top];
        const preset = (this._hub.color_presets || [])[view.index];
        return [top, { label: preset?.name || this._t("add") }];
      }
      case "effects": {
        const top = { label: this._t("effects"), go: to({ kind: "effects" }) };
        if (view.index === undefined) return [top];
        const effect = (this._hub.effects || [])[view.index];
        return [top, { label: effect?.name || this._t("add") }];
      }
      case "scenes": {
        const top = {
          label: named("scenes"),
          go: to({ kind: "scenes" }),
        };
        if (this._scene) {
          return [rooms, theRoom, top, { label: this._scene.name }];
        }
        return [rooms, theRoom, top];
      }
      case "switches": {
        const top = { label: named("switches"), go: to({ kind: "switches" }) };
        const item = (room?.data.switches || [])[view.index];
        if (view.index === undefined) return [rooms, theRoom, top];
        return [
          rooms,
          theRoom,
          top,
          { label: item?.name || this._t("switch") },
        ];
      }
      case "room_zones": {
        const top = {
          label: named("room_zones"),
          go: to({ kind: "room_zones" }),
        };
        const item = (room?.data.zones || [])[view.index];
        if (view.index === undefined) return [rooms, theRoom, top];
        return [rooms, theRoom, top, { label: item?.name || this._t("room_zone") }];
      }
      case "light_groups": {
        const top = {
          label: named("light_groups"),
          go: to({ kind: "light_groups" }),
        };
        const item = (room?.data.light_groups || [])[view.index];
        if (view.index === undefined) return [rooms, theRoom, top];
        return [
          rooms,
          theRoom,
          top,
          { label: item?.name || this._t("light_group") },
        ];
      }
      case "calibrations": {
        const top = {
          label: named("calibrations"),
          go: to({ kind: "calibrations" }),
        };
        const item = (room?.data.light_profiles || [])[view.index];
        if (view.index === undefined) return [rooms, theRoom, top];
        return [
          rooms,
          theRoom,
          top,
          { label: item?.light_entity || this._t("add") },
        ];
      }
      case "mode": {
        if (view.creating) return [modes, { label: this._t("add_mode") }];
        const one = {
          label: mode?.name,
          go: to({ kind: "mode", section: "settings" }),
        };
        if (view.section === "rules") {
          const list = {
            label: named("rules"),
            go: to({ kind: "mode", section: "rules" }),
          };
          if (view.rule !== undefined) {
            const rule = (mode?.data.rules || [])[view.rule];
            const room = this._rooms.find((r) => r.id === rule?.zones);
            return [modes, one, list, { label: room?.name || this._t("add") }];
          }
          return [modes, one, { ...list, go: undefined }];
        }
        return [modes, one, { label: this._t("basics") }];
      }
      default: {
        if (view.creating) return [rooms, { label: this._t("add_room") }];
        if (!room) return [rooms];
        const groups = this._schema?.forms.zone || [];
        const group =
          groups.find((g) => g.section === view.section) || groups[0];
        return [rooms, theRoom, { label: named(group?.section) }];
      }
    }
  }

  /**
   * The trail, and the button that walks back up it.
   *
   * Both live in the page header beside the title rather than above the
   * content: a strip that appears and disappears between screens shifted
   * everything below it, and the header is the one thing on the page that
   * never moves.
   */
  _paintCrumbs() {
    const holder = this.shadowRoot.getElementById("crumbs");
    const back = this.shadowRoot.getElementById("crumb-back");
    const menu = this.shadowRoot.getElementById("menu");
    if (!holder) return;
    const crumbs = this._trail().filter(Boolean);

    back.title = this._t("back");
    back.innerHTML = this._icon("mdi:arrow-left") || "\u2039";
    const drawer = this.shadowRoot.getElementById("drawer");
    drawer.title = this._t("menu");
    drawer.innerHTML = this._icon("mdi:format-list-bulleted") || "\u2261";
    menu.title = this._t("ha_sidebar");
    menu.innerHTML = this._icon("mdi:menu") || "\u2630";
    // The header is built before the strings have arrived, so its buttons are
    // labelled here rather than there.
    const more = this.shadowRoot.getElementById("more");
    more.title = this._t("more");
    more.innerHTML = this._icon("mdi:dots-vertical") || "\u22ee";
    this.shadowRoot.getElementById("go-import").innerHTML = `${this._icon(
      "mdi:download"
    )}<span class="grow">${this._t("import_scenes")}</span>`;
    this.shadowRoot.getElementById("go-about").innerHTML = `${this._icon(
      "mdi:information-outline"
    )}<span class="grow">${this._t("about")}</span>`;

    holder.innerHTML = crumbs
      .map((crumb, index) => {
        const last = index === crumbs.length - 1;
        const step = `<span class="crumb"${
          last || !crumb.go ? "" : ` data-crumb="${index}"`
        }>${crumb.label ?? ""}</span>`;
        return index ? `<span class="crumb sep">\u203a</span>${step}` : step;
      })
      .join("");
    holder.querySelectorAll("[data-crumb]").forEach((step) =>
      step.addEventListener("click", () =>
        this._leave(crumbs[Number(step.dataset.crumb)].go)
      )
    );

    // The button is always there and greys out at the top, rather than coming
    // and going: a control that vanishes is one people stop reaching for.
    const parent = crumbs[crumbs.length - 2];
    back.disabled = !parent?.go;
    back.onclick = parent?.go ? () => this._leave(parent.go) : null;
  }

  /** The runtime choices a field may ask for, for the room in hand. */
  _choices(room) {
    return {
      // Ours and theirs together, since a field asking for an effect does
      // not care which of the two wrote it.
      effects: [
        ...BUILT_IN_EFFECTS.map((effect) => ({
          value: effect.id,
          label: this._t(effect.label),
        })),
        ...(this._hub.effects || []).map((effect) => ({
          value: effect.effect_id,
          label: effect.name,
        })),
      ],
      scenes: (room?.scenes || []).map((scene) => ({
        value: scene.scene_id,
        label: scene.name,
      })),
      zones: this._rooms.map((r) => ({ value: r.id, label: r.name })),
      lights: (room?.lights || []).map((id) => ({ value: id, label: id })),
      // A group may hold other groups of the same room. The one being edited
      // is left in: dropping it is the flow's trick, and here the index is
      // not in hand -- the save refuses a loop either way, with the loop
      // named.
      light_groups: (room?.data.light_groups || []).map((group) => ({
        value: group.group_id,
        label: group.name || group.group_id,
      })),
      room_zones: (room?.data.zones || []).map((zone) => ({
        value: zone.zone_id,
        label: zone.name || zone.zone_id,
      })),
    };
  }

  get _room() {
    return this._rooms.find((room) => room.id === this._roomId) || null;
  }

  _shell() {
    this.shadowRoot.innerHTML = `
      <style>
        /* The page is exactly the height of the window and does not scroll:
           the menu and the content each scroll inside it, so the header and
           the menu stay where they are however long a form gets. dvh rather
           than vh because a phone's address bar comes and goes. */
        :host { display:flex; flex-direction:column; height:100vh; height:100dvh;
                overflow:hidden;
                background:var(--primary-background-color);
                color:var(--primary-text-color);
                font-family:var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        /* Sticky, because the way back has to be reachable from the bottom of
           a long form as well as the top of one. */
        header { flex:0 0 auto; z-index:5;
                 display:flex; align-items:center; gap:8px;
                 min-height:56px; padding:6px 12px; box-sizing:border-box;
                 background:var(--app-header-background-color, var(--primary-color));
                 color:var(--app-header-text-color, #fff);
                 /* A line rather than a shadow, which is what Home Assistant
                    draws under its own toolbar. */
                 border-bottom:1px solid var(--divider-color, rgba(0,0,0,.12)); }
        header .titles { display:flex; align-items:baseline; gap:6px 14px;
                         flex-wrap:wrap; min-width:0; }
        .app-title { font-size:20px; white-space:nowrap; }
        nav.crumbs { display:flex; align-items:baseline; gap:6px; flex-wrap:wrap;
                     font-size:14px; opacity:.85; min-width:0; }
        nav.crumbs .crumb { white-space:nowrap; }
        nav.crumbs .crumb[data-crumb] { cursor:pointer; }
        nav.crumbs .crumb[data-crumb]:hover { text-decoration:underline; }
        .icon-btn { background:transparent; color:inherit; border:none; padding:0;
                    width:40px; height:40px; min-height:0; border-radius:50%;
                    flex:0 0 auto;
                    display:inline-flex; align-items:center; justify-content:center;
                    cursor:pointer; }
        .icon-btn:hover:not([disabled]) { background:rgba(255,255,255,.12); }
        .icon-btn[disabled] { opacity:.35; cursor:default; }
        /* Home Assistant hides its own sidebar on a narrow screen and expects
           the page to offer the way out. Without this the panel is a room with
           no door. */
        #menu, #drawer { display:none; }
        @media (max-width:870px) { #menu { display:inline-flex; } }
        @media (max-width:800px) { #drawer { display:inline-flex; } }
        @media (max-width:800px) {
          /* No room for them, and the phone's own back button does the job
             now that it walks the trail rather than leaving the panel. */
          #crumb-back, nav.crumbs { display:none; }
        }
        ha-icon { --mdc-icon-size:20px; flex:0 0 auto; }
        /* Pushed to the far end, and holding what belongs to the panel as a
           whole rather than to the screen in front of you. */
        .overflow { position:relative; margin-left:auto; flex:0 0 auto; }
        .overflow .menu { position:absolute; right:0; top:44px; z-index:6;
                          min-width:220px; padding:6px 0; border-radius:10px;
                          background:var(--card-background-color,#fff);
                          color:var(--primary-text-color);
                          box-shadow:0 4px 16px rgba(0,0,0,.3); }
        .overflow .menu[hidden] { display:none; }
        .menu-item { display:flex; width:100%; gap:12px; align-items:center;
                     padding:12px 16px; background:transparent; color:inherit;
                     border-radius:0; text-align:left; justify-content:flex-start; }
        .menu-item:hover { background:var(--secondary-background-color); }
        .body { --gutter:16px; --nav:260px; --rail:56px;
                flex:1 1 auto; min-height:0; position:relative;
                display:grid; grid-template-columns:var(--nav) 1fr; gap:16px;
                padding:var(--gutter);
                /* Stretch, not start: the menu runs the height of the window
                   and the content pane is a frame with its own scrollbar. */
                align-items:stretch; overflow:hidden; }
        .nav { overflow:auto; position:relative;
               transition:width .2s ease, box-shadow .2s ease, padding .2s ease; }
        /* Where the button that folds it lives: on the menu, since it is the
           menu it folds. */
        /* Stays put while the menu scrolls: a button that folds the menu is
           no use once it has been scrolled off the top of it. */
        .nav-top { position:sticky; top:-16px; z-index:2;
                   display:flex; justify-content:flex-end;
                   margin:-16px 0 4px; padding:8px 8px 4px 0;
                   background:var(--card-background-color,#fff); }
        /* Sized and placed like one of the chevrons below it, so the
           column of them is a column. */
        .nav-top .icon-btn { width:28px; height:28px; min-height:0;
                             color:var(--secondary-text-color); }
        .nav-top .icon-btn:hover { background:var(--secondary-background-color);
                                   color:var(--primary-text-color); }
        @media (max-width:800px) { .nav-top { display:none; } }
        /* Folded to a column of icons. The labels are not hidden, they are
           simply outside a menu this narrow -- which is what lets the width
           animate rather than things blinking in and out of it. */
        .body[data-rail="1"] { grid-template-columns:var(--rail) 1fr; }
        .body[data-rail="1"] .nav { position:absolute; z-index:5;
               top:var(--gutter); bottom:var(--gutter); left:var(--gutter);
               width:var(--rail); overflow:hidden; padding:16px 10px; }
        /* Taken out of the flow to let it overlap the content, which leaves
           the content as the only item left to fill column one. */
        .body[data-rail="1"] #main { grid-column:2; }
        .body[data-rail="1"] .nav:hover { width:var(--nav); overflow:auto;
               padding:16px 20px; box-shadow:4px 0 16px rgba(0,0,0,.35); }
        .body[data-rail="1"] .nav li, .body[data-rail="1"] .nav li .grow,
        .body[data-rail="1"] .nav button { white-space:nowrap; }
        /* Folded, this is a column of icons and nothing else: no half-read
           labels, and no gaps where the rooms and their screens would be. */
        .body[data-rail="1"] .nav:not(:hover) .grow,
        .body[data-rail="1"] .nav:not(:hover) .twist,
        .body[data-rail="1"] .nav:not(:hover) ul.group,
        .body[data-rail="1"] .nav:not(:hover) ul.sub,
        .body[data-rail="1"] .nav:not(:hover) .bar { display:none; }
        .body[data-rail="1"] .nav:not(:hover) li { justify-content:center;
                                                   padding:10px 0; }
        /* Folded, the groupings are gone, so the spacing that marked them
           goes too: one column of icons, evenly spaced. */
        .body[data-rail="1"] .nav:not(:hover) ul,
        .body[data-rail="1"] .nav:not(:hover) ul.nav-group,
        .body[data-rail="1"] .nav:not(:hover) ul.nav-last { margin:0; }
        /* Folded, this button stays on the left whether the menu is hovered
           or not -- on the right it moved out from under the pointer as the
           hover widened the menu, which is a button you cannot press. */
        /* Folded, its icon lines up with the column of icons below it
           rather than sitting eight pixels to their left. */
        .body[data-rail="1"] .nav-top { justify-content:flex-start;
                                        margin:-16px 0 4px; padding:8px 0 4px 4px; }
        /* The edge you drag to make it wider, which follows whatever width
           the menu is set to rather than being told separately. */
        .nav-grip { position:absolute; top:var(--gutter); bottom:var(--gutter);
                    left:calc(var(--gutter) + var(--nav) - 4px); width:8px;
                    cursor:col-resize; z-index:6; }
        .nav-grip::after { content:""; position:absolute; top:0; bottom:0;
                           right:3px; width:2px; background:var(--primary-color);
                           opacity:0; transition:opacity .15s ease; }
        .nav-grip:hover::after, .body[data-dragging="1"] .nav-grip::after {
          opacity:.6; }
        .body[data-rail="1"] .nav-grip, .body[data-dragging="1"] .nav {
          transition:none; }
        @media (max-width:800px) { .nav-grip { display:none; } }
        /* min-width:0, or a grid item refuses to be narrower than its
           contents -- and one borrowed control that wants 400px then makes
           the whole page wider than the phone it is on. */
        #main { min-height:0; min-width:0; display:flex; flex-direction:column; }
        .scrim { display:none; }
        @media (max-width:800px) {
          /* Too narrow for two columns, so the menu slides over the content
             instead of sharing the page with it. Out of flow, which is what
             lets the content pane keep the full height of the window -- and
             so keeps its footer on the bottom of the window rather than
             somewhere the content can scroll underneath. */
          /* One column, and it may be no wider than the screen: the menu is
             out of the flow here, so the content is the only thing left to
             overflow it. */
          .body { --gutter:12px; grid-template-columns:minmax(0, 1fr); }
          /* There is no rail here, only the drawer -- and these have to say
             so at the same weight as the rules above, which win on
             specificity rather than on being later in the sheet. */
          .body[data-rail="1"] { grid-template-columns:1fr; }
          .body[data-rail="1"] #main { grid-column:auto; }
          .body[data-rail="1"] .nav, .body[data-rail="1"] .nav:hover {
            position:fixed; top:0; bottom:0; left:0; z-index:7;
            width:min(320px, 85vw); padding:16px 20px; overflow:auto;
            box-shadow:2px 0 12px rgba(0,0,0,.35); }
          .body[data-rail="1"] .nav:not(:hover) ul.sub,
          .body[data-rail="1"] .nav:not(:hover) .bar {
            max-height:none; opacity:1; overflow:visible; }
          /* Flush against the edge of the screen, so no corners. */
          .nav { position:fixed; top:0; bottom:0; left:0; z-index:7;
                 width:min(320px, 85vw); border-radius:0 !important;
                 transform:translateX(-101%); transition:transform .2s ease;
                 box-shadow:2px 0 12px rgba(0,0,0,.35); }
          .nav[data-open="1"] { transform:none; }
          .scrim { display:block; position:fixed; inset:0; z-index:6;
                   background:rgba(0,0,0,.45); opacity:0; pointer-events:none;
                   transition:opacity .2s ease; }
          .scrim[data-open="1"] { opacity:1; pointer-events:auto; }
        }
        @media (max-width:500px) { .body { --gutter:8px; gap:12px; } }
        .card { background:var(--card-background-color,#fff); border-radius:12px;
                padding:16px 20px; box-sizing:border-box;
                box-shadow:var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.1)); }
        @media (max-width:500px) { .card { padding:12px 14px; } }
        /* One card per screen, the height of the pane: what is on it scrolls
           inside, and the buttons that act on it stay where they are. */
        .page { display:flex; flex-direction:column; min-height:0; min-width:0;
                flex:1 1 auto; padding:0; overflow:hidden; }
        .page-body { flex:1 1 auto; min-height:0; min-width:0; overflow:auto;
                     overflow-wrap:anywhere; padding:16px 20px;
                     display:flex; flex-direction:column; }
        .page-body > * { flex:0 0 auto; }
        /* No rows, so no box around them either. */
        .page-body ul:empty { display:none; }
        /* The way to add one belongs in the list, as its last row, rather
           than loose underneath it. */
        .page-body li.adder { padding:6px 10px; }
        .page-body li.adder > * { flex:1 1 auto; min-width:0; }
        .page-body li.muted { color:var(--secondary-text-color); font-size:13px; }
        /* Nothing on a page is wider than the page. Borrowed controls bring
           their own widths, and a long entity id has no space to break at. */
        .page-body *, .page-body ha-selector { max-width:100%; box-sizing:border-box; }
        /* And the page itself never grows to fit one, which is what turned a
           long option name into a horizontal scrollbar. */
        .page-body { max-width:100%; }
        .page-body bl-form { display:block; width:100%; min-width:0; }
        /* Adding and deleting at one end, agreeing and backing out at the
           other, with the one that commits furthest from the one that does
           not. */
        .page-foot { flex:0 0 auto; min-width:0; display:flex; gap:8px;
                     flex-wrap:wrap;
                     align-items:center; justify-content:space-between;
                     margin:0; padding:12px 20px;
                     border-top:1px solid var(--divider-color,#e0e0e0);
                     background:var(--card-background-color,#fff); }
        .foot-end { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
        .page-body > :first-child { margin-top:0; }
        @media (max-width:500px) {
          .page-body { padding:12px 14px; }
          .page-foot { padding:10px 14px; }
        }
        /* Stacked cards -- a mode and its rules, the diagnostics tables and
           the curve -- were flush against each other, which read as one card
           with a line through it rather than two things. */
        #main > * + *, .editor > .card + .card { margin-top:16px; }
        .card > :last-child { margin-bottom:0; }
        h2 { margin:0 0 12px; font-size:16px; font-weight:500; }
        ul { list-style:none; margin:0 0 4px; padding:0; }
        ul.nav-group { margin-top:12px; }
        ul.nav-last { margin-top:20px; }
        /* That extra space separates a group's rows from the next heading.
           With the group folded away there are no rows to separate, and the
           space reads as one entry being further from its neighbour than the
           rest are from theirs. */
        ul.group[hidden] + ul.nav-group { margin-top:0; }
        /* Rows on the content pane are a list of things you can open, so they
           are ruled, they light up, and they say so with a chevron. The menu
           keeps its own quieter shape. */
        .page-body ul { border:1px solid var(--divider-color,#3d3d3d);
                        border-radius:12px; overflow:hidden; margin:0; }
        .page-body li { border-radius:0; padding:12px 16px; min-height:48px;
                        box-sizing:border-box; }
        .page-body li + li { border-top:1px solid var(--divider-color,#3d3d3d); }
        .page-body li[data-index]::after, .page-body li[data-room]::after,
        .page-body li[data-mode]::after, .page-body li[data-rule]::after {
          content:""; flex:0 0 auto; width:8px; height:8px; margin-left:4px;
          border-right:2px solid currentColor; border-bottom:2px solid currentColor;
          transform:rotate(-45deg); opacity:.4; }
        .page-body li .moves, .page-body li .row-actions {
          display:flex; gap:2px; flex:0 0 auto; }
        .row-toggle { display:flex; align-items:center; flex:0 0 auto; }
        /* Still listed, still readable, plainly not happening. */
        .page-body li[data-off="1"] .grow { opacity:.5; }
        /* Quiet until the row is under the pointer: a list of things to open
           should not read as a list of things to delete. */
        .row-actions button { padding:0 8px; min-height:34px; border-radius:999px;
                              opacity:.55; }
        li:hover .row-actions button, .row-actions button:focus-visible { opacity:1; }
        /* Filled rather than merely recoloured on hover: at this size a change
           of text colour is easy to miss, and one of these is destructive. */
        .row-actions button[data-delete]:hover,
        .row-actions button[data-delete]:focus-visible,
        li.step button[data-delete]:hover,
        li.step button[data-delete]:focus-visible {
          background:var(--error-color,#db4437); color:#fff; opacity:1; }
        li.step button[data-delete] { border-radius:999px; padding:0 8px;
                                      min-height:34px; opacity:.55; }
        li.step:hover button[data-delete] { opacity:1; }
        .row-actions button[data-duplicate]:hover,
        .row-actions button[data-duplicate]:focus-visible {
          background:var(--secondary-background-color);
          color:var(--primary-color); opacity:1; }
        .moves button:hover:not([disabled]) {
          background:var(--secondary-background-color); }
        /* Nothing here yet, said plainly and in the middle rather than as a
           dash somebody has to interpret. */
        /* In the middle of whatever space the page has, rather than tucked
           under the top of it. */
        .empty { flex:1 1 auto; display:flex; flex-direction:column;
                 align-items:center; justify-content:center; gap:10px;
                 min-height:160px; padding:40px 16px; text-align:center;
                 color:var(--primary-color); }
        .empty span { max-width:34ch; }
        .empty ha-icon { --mdc-icon-size:48px; opacity:.7; }
        /* The menu is painted the way Home Assistant paints its own sidebar:
           the accent colour for the text and the icon of the current entry,
           over a wash of that same colour rather than a solid block of it,
           and a dimmed icon everywhere else. Its variables, so a theme that
           restyles the sidebar restyles this too. */
        li { padding:10px 12px; border-radius:8px; cursor:pointer; position:relative;
             display:flex; align-items:center; gap:10px;
             color:var(--sidebar-text-color, var(--primary-text-color)); }
        li > * { position:relative; z-index:1; }
        li ha-icon { color:var(--sidebar-icon-color, var(--secondary-text-color)); }
        li:hover { background:var(--secondary-background-color); }
        li[aria-selected="true"] { color:var(--sidebar-selected-text-color,
                                              var(--primary-color)); }
        li[aria-selected="true"]::before { content:""; position:absolute; inset:0;
             z-index:0; border-radius:8px; pointer-events:none;
             background-color:var(--sidebar-selected-icon-color, var(--primary-color));
             opacity:var(--dark-divider-opacity, .12); }
        li[aria-selected="true"] ha-icon { color:var(--sidebar-selected-icon-color,
                                                     var(--primary-color)); }
        li[aria-expanded="true"] { font-weight:500; }
        /* Folded, but you are somewhere inside it: a dot on the chevron, so
           a closed branch is not the same as an unrelated one. */
        li[data-inside="1"] { font-weight:500; }
        li[data-inside="1"] .twist::after { content:""; position:absolute;
          width:6px; height:6px; margin:-10px 0 0 10px; border-radius:50%;
          background:var(--primary-color); }
        li[data-inside="1"] .twist { position:relative; }
        /* Folded to a rail, the rows underneath are not there to be lit, so
           the top-level entry says where you are instead. */
        .body[data-rail="1"] .nav:not(:hover) li[data-within="1"] {
          color:var(--sidebar-selected-text-color, var(--primary-color)); }
        .body[data-rail="1"] .nav:not(:hover) li[data-within="1"] ha-icon {
          color:var(--sidebar-selected-icon-color, var(--primary-color)); }
        .body[data-rail="1"] .nav:not(:hover) li[data-within="1"]::before {
          content:""; position:absolute; inset:0; z-index:0; border-radius:8px;
          pointer-events:none;
          background-color:var(--sidebar-selected-icon-color, var(--primary-color));
          opacity:var(--dark-divider-opacity, .12); }
        li.add ha-icon { color:inherit; }
        /* One of ours: shown so people know it exists, but not a row that
           opens onto anything, because there is nothing to change. */
        .page-body li.built-in { opacity:.75; cursor:default; }
        .page-body li.built-in:hover { background:transparent; }
        .page-body li.step { align-items:flex-start; gap:12px; }
        .page-body li.step bl-form { flex:1 1 auto; min-width:0; }
        .page-body li.step bl-form::part(fields) { display:flex; }
        /* A row's caption belongs above it, not beside it: sharing the line
           with the form squeezed both. */
        .page-body li.step { flex-wrap:wrap; }
        .day-strip { margin-top:14px; }
        .cards { display:grid; gap:16px; margin-top:16px;
                 grid-template-columns:repeat(auto-fill,minmax(340px,1fr)); }
        #house h3 { margin:0 0 8px; font-size:1rem; font-weight:600; }
        #house li { display:flex; align-items:center; gap:12px; }
        button.pill { border-radius:999px; padding:0 16px; min-height:36px;
                      border:1px solid var(--divider-color); background:none;
                      color:var(--primary-color); cursor:pointer; font:inherit; }
        button.pill:hover { background:var(--secondary-background-color); }
        #house select.mode { min-height:36px; border-radius:18px; padding:0 12px;
                             background:var(--card-background-color);
                             color:var(--primary-text-color);
                             border:1px solid var(--divider-color); font:inherit; }
        .day-strip .day-bar { display:flex; height:30px; margin-top:6px;
                              border-radius:8px; overflow:hidden; }
        .day-strip .day-bar span { display:flex; align-items:center;
                                   justify-content:center; font-size:12px;
                                   color:#fff; white-space:nowrap;
                                   overflow:hidden; }
        .day-strip .day-bar .on { background:var(--success-color,#4caf50); }
        .day-strip .day-bar .off { background:var(--error-color,#db4437); }
        .day-strip .day-ticks { display:flex; justify-content:space-between;
                                font-size:12px; opacity:.6; margin-top:4px; }
        .page-body li.step .nested-caption { flex:1 0 100%; order:-1; }
        li[draggable="true"] { cursor:grab; }
        li.dragging { opacity:.4; }
        li.drop-target { outline:2px dashed var(--primary-color); }
        .twist { display:inline-flex; align-items:center; cursor:pointer;
                 opacity:.6; margin:-4px -4px -4px 0; padding:4px;
                 transition:transform .2s ease, opacity .15s ease; }
        .twist:hover { opacity:1; }
        .twist.open { transform:rotate(90deg); }
        li.section { font-size:15px; font-weight:500; }
        ul.sub { margin:2px 0 8px 14px; padding-left:8px;
                 border-left:2px solid var(--divider-color,#ddd); }
        /* The padding is on the list rather than the rows: a highlighted row
           with none of it is glued to the line it hangs from. */
        ul.sub li { font-size:14px; padding:7px 10px; }
        /* What belongs to the entry above it: the rooms under Rooms, the
           modes under Modes, and the row that adds one at the same indent as
           its neighbours rather than floating below them. */
        ul.group { margin-left:14px; }
        li.add { color:var(--primary-color); }
        h3 { margin:18px 0 10px; font-size:15px; font-weight:500; }
        /* A boxed accordion, in the shape Home Assistant's expansion panel
           uses: the title on the left, a chevron on the right, and the
           contents inside the box rather than running under the next one. */
        details { border:1px solid var(--divider-color,#3d3d3d); border-radius:12px;
                  margin-bottom:12px; overflow:hidden;
                  background:var(--ha-card-background, var(--card-background-color)); }
        /* Not :last-of-type, which is the last accordion *among its
           siblings* -- so an accordion at the end of one box lost its margin
           and sat flush against the next box's first one. The last thing in
           a container drops its margin whatever kind of thing it is.
           Deliberately only the containers that end a screen: a block in the
           middle of one, like the diagnostics tables, has something after it
           and needs the space kept. */
        .page-body > :last-child, .fold-body > :last-child { margin-bottom:0; }
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
        details > bl-form { display:block; padding:16px 20px; }
        details > .fold-body { padding:16px 20px; }
        /* Appended after the form's own folds, and flush against the last of
           them without this. */
        #extra > details:first-child { margin-top:12px; }
        .light { display:flex; align-items:center; gap:12px; padding:8px 12px;
                 border-radius:8px; cursor:pointer; flex-wrap:wrap; }
        .light > ha-selector { flex:1 1 150px; min-width:150px; }
        .light[aria-selected="true"] { outline:2px solid var(--primary-color); }
        .swatch { width:22px; height:22px; border-radius:50%; flex:0 0 auto;
                  border:1px solid rgba(0,0,0,.2); }
        /* The light's own icon, in the colour the scene gives it. */
        .bulb { flex:0 0 auto; display:inline-flex; align-items:center;
                justify-content:center; width:30px; height:30px; }
        .bulb ha-icon { --mdc-icon-size:26px; }
        .grow { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; }
        li .grow, .light .grow { white-space:normal; }
        .muted { color:var(--secondary-text-color); font-size:13px; }
        /* A fixed height and a fixed icon size, because a button with an
           icon in it was a different size from one without. */
        /* Home Assistant's own buttons are pills, and a panel of squarer
           ones sitting inside it reads as somebody else's page. */
        button { font:inherit; padding:0 18px; min-height:40px; border-radius:999px;
                 border:none; line-height:1.25; cursor:pointer;
                 background:var(--primary-color); color:#fff;
                 display:inline-flex; align-items:center; justify-content:center;
                 gap:6px; }
        button ha-icon { --mdc-icon-size:18px; }
        button[disabled] { opacity:.4; cursor:default; }
        button.flat { background:transparent; color:var(--primary-color); }
        /* The shape Home Assistant gives a button that is the accent colour
           but not the point of the screen: filled quietly rather than
           loudly, so Save is still the thing your eye goes to. */
        button.tonal { background:var(--secondary-background-color);
                       color:var(--primary-color); }
        button.tonal:hover { filter:brightness(1.15); }
        button.danger { background:var(--error-color,#db4437); }
        .bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:16px; }
        .card > p.muted:first-of-type { margin-top:0; }
        input[type=text] { font:inherit; padding:8px; border-radius:8px; width:100%;
                           box-sizing:border-box;
                           border:1px solid var(--divider-color,#ccc);
                           background:var(--card-background-color); color:inherit; }
        .editor { display:grid; grid-template-columns:1fr 300px; gap:16px; }
        @media (max-width:1000px) { .editor { grid-template-columns:1fr; } }
        .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px;
                background:var(--secondary-background-color); }
        .banner { display:flex; align-items:center; gap:16px; margin-bottom:16px;
                  padding:12px 16px; border-radius:8px;
                  background:var(--secondary-background-color);
                  border-left:4px solid var(--info-color,#3f9bd4); flex-wrap:wrap; }
        .banner.live { border-left-color:var(--success-color,#43a047); }
        .light select { font:inherit; padding:5px 8px; border-radius:8px;
                        border:1px solid var(--divider-color,#ccc);
                        background:var(--card-background-color); color:inherit; }
        .live { background:var(--success-color,#43a047); }
        .modal { position:fixed; inset:0; z-index:9; display:flex;
                 align-items:center; justify-content:center; padding:16px;
                 background:rgba(0,0,0,.45); animation:reveal .15s ease both; }
        .modal-card { width:min(420px, 100%); border-radius:12px; overflow:hidden;
                      background:var(--card-background-color,#fff);
                      box-shadow:0 8px 32px rgba(0,0,0,.4); }
        /* The padding belongs to the card. Hanging it off the margins of
           whichever child happened to be first meant every new dialog had to
           remember to be that child. */
        .modal-card { padding:24px; }
        .modal-card > * { margin:0 0 14px; }
        .modal-card > :last-child { margin-bottom:0; }
        .modal-card > .page-foot { margin:20px -24px -24px; padding:12px 24px; }
        .about-head { display:flex; align-items:center; gap:14px; }
        .about-head h2 { margin:0; }
        .about-head ha-icon { --mdc-icon-size:40px; color:var(--primary-color); }
        .about-icon { width:40px; height:40px; border-radius:8px;
                      object-fit:contain; }
        /* Nothing to decide here, so there is nothing to confirm: the corner
           and the backdrop are both ways out and neither needs a footer. */
        .modal-card { position:relative; }
        .shut { position:absolute; top:14px; right:14px; margin:0;
                width:34px; height:34px; min-height:0; padding:0;
                border-radius:50%; font-size:22px; line-height:1;
                background:transparent; color:var(--secondary-text-color); }
        .shut:hover { background:var(--secondary-background-color);
                      color:var(--primary-text-color); }
        .about-head { padding-right:36px; }
        .modal-card a { color:var(--primary-color); }
        table.about { width:100%; }
        table.about th { text-align:left; font-weight:400; padding:2px 12px 2px 0;
                         color:var(--secondary-text-color); white-space:nowrap; }
        /* A few small movements, and none of them in anybody's way. */
        @keyframes reveal {
          from { opacity:0; transform:translateY(-4px); }
          to { opacity:1; transform:none; }
        }
        .page { animation:reveal .16s ease both; }
        li, button, summary { transition:background-color .15s ease,
                                         color .15s ease, opacity .15s ease; }
        button:active { transform:translateY(1px); }
        .light { transition:outline-color .15s ease; }
        /* Somebody who has asked for less of this gets none of it. */
        @media (prefers-reduced-motion:reduce) {
          *, .page, ul.sub { animation:none !important; transition:none !important; }
        }
        /* Inside the box now that these are boxed accordions, rather than
           running up against its edges. */
        details.diag table { width:100%; border-collapse:collapse; margin:8px 0 12px; }
        details.diag th { text-align:left; font-weight:400; padding:4px 12px 4px 16px;
                          color:var(--secondary-text-color); vertical-align:top; }
        details.diag td { padding:4px 16px 4px 0; font-variant-numeric:tabular-nums;
                          word-break:break-word; }
        /* Home Assistant's own logbook shape: the time down the left, a ruled
           line of icons beside it, and what happened to the right of that. */
        .log { max-height:420px; overflow:auto; font-size:14px; }
        .log .entry { display:flex; gap:12px; align-items:flex-start;
                      padding:10px 0; }
        .log .entry + .entry { border-top:1px solid var(--divider-color,#e0e0e0); }
        .log .when { flex:0 0 68px; padding-top:9px; font-size:12px;
                     color:var(--secondary-text-color);
                     font-variant-numeric:tabular-nums; }
        .log .dot { flex:0 0 36px; height:36px; border-radius:50%;
                    display:inline-flex; align-items:center; justify-content:center;
                    background:var(--secondary-background-color);
                    color:var(--secondary-text-color); }
        .log .what { flex:1 1 auto; min-width:0; padding-top:4px;
                     overflow-wrap:anywhere; }
        .curve { width:100%; height:200px; display:block; margin:8px 0 12px;
                 border-radius:8px; overflow:hidden;
                 background:var(--secondary-background-color,#eee); }
        .curve-now th, .curve-lights th { text-align:left; font-weight:400;
                 color:var(--secondary-text-color); padding:3px 12px 3px 0; }
        .curve-lights td, .curve-now td { font-variant-numeric:tabular-nums; }
      </style>
      <header>
        <button class="icon-btn" id="menu"></button>
        <button class="icon-btn" id="drawer"></button>
        <button class="icon-btn" id="crumb-back" disabled></button>
        <div class="titles">
          <span class="app-title">Better Lighting</span>
          <nav class="crumbs" id="crumbs"></nav>
        </div>
        <div class="overflow">
          <button class="icon-btn" id="more"></button>
          <div class="menu" id="more-menu" hidden>
            <button class="menu-item" id="go-import"></button>
            <button class="menu-item" id="go-about"></button>
          </div>
        </div>
      </header>
      <div class="body">
        <div class="scrim" id="scrim"></div>
        <div class="card nav" id="rooms"></div>
        <div class="nav-grip" id="nav-grip"></div>
        <div id="main"></div>
      </div>`;

    // Home Assistant's own sidebar, which it hides on a narrow screen.
    this.shadowRoot.getElementById("menu").addEventListener("click", () =>
      this.dispatchEvent(
        new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })
      )
    );

    // Our own menu, which slides over the content on a narrow screen. The
    // button lives in the header, so it is reachable from anywhere on a long
    // page rather than only from the top of one.
    this.shadowRoot
      .getElementById("drawer")
      .addEventListener("click", (event) => {
        event.stopPropagation();
        this._toggleNav();
      });
    this.shadowRoot
      .getElementById("scrim")
      .addEventListener("click", () => this._setDrawer(false));
    this.shadowRoot
      .getElementById("nav-grip")
      .addEventListener("pointerdown", (event) => this._startResize(event));
    this._applyNavLayout();

    // Accordions fold rather than jumping. Delegated, so it covers every
    // one of them however and whenever it was drawn, and does nothing at all
    // for somebody who has asked for less movement.
    this.shadowRoot
      .getElementById("main")
      .addEventListener("click", (event) => this._foldClick(event));

    const overflow = this.shadowRoot.getElementById("more-menu");
    this.shadowRoot.getElementById("more").addEventListener("click", (event) => {
      event.stopPropagation();
      overflow.hidden = !overflow.hidden;
    });
    this.shadowRoot.getElementById("go-about").addEventListener("click", () => {
      overflow.hidden = true;
      this._showAbout();
    });
    this.shadowRoot.getElementById("go-import").addEventListener("click", () => {
      overflow.hidden = true;
      this._leave(() => {
        this._view = { kind: "import" };
        this._paint();
      });
    });
    // Anywhere else closes it, as a menu should.
    this._closeOverflow = () => {
      overflow.hidden = true;
    };
    this.shadowRoot.addEventListener("click", this._closeOverflow);
    window.addEventListener("click", this._closeOverflow);
  }

  /**
   * The buttons at the end of a row in a list.
   *
   * Deleting from the list rather than from the screen behind it: the thing
   * you want rid of is the one you are looking at, and going into it to get
   * out of it again is a detour.
   */
  _rowActions(index, { duplicate = false, remove = true } = {}) {
    return `<span class="row-actions">${
      duplicate
        ? `<button class="flat" data-duplicate="${index}" title="${this._t(
            "duplicate"
          )}">${this._icon("mdi:content-copy")}</button>`
        : ""
    }${
      remove
        ? `<button class="flat" data-delete="${index}" title="${this._t(
            "delete"
          )}">${this._icon("mdi:delete-outline")}</button>`
        : ""
    }</span>`;
  }

  /** Wire those buttons, with nothing deleted without being asked about. */
  _wireRowActions(main, { duplicate, remove }) {
    main.querySelectorAll("[data-duplicate]").forEach((button) =>
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        await duplicate(Number(event.currentTarget.dataset.duplicate));
      })
    );
    main.querySelectorAll("[data-delete]").forEach((button) =>
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!(await this._confirm())) return;
        await remove(Number(event.currentTarget.dataset.delete));
      })
    );
  }

  /**
   * A list with nothing in it, said plainly.
   *
   * A lone em dash was the old answer, which reads as a value somebody forgot
   * to fill in rather than as an invitation to add the first one.
   */
  _empty(text) {
    return `<div class="empty">${this._icon("mdi:tray-remove")}<span>${
      text || this._t("empty_list")
    }</span></div>`;
  }

  /**
   * Going somewhere else, with whatever is unsaved taken into account.
   *
   * Every way out of a screen goes through here -- the menu, the trail, the
   * back button -- so there is one place that knows a form has been changed
   * and one question asked about it, rather than a rule each way out has to
   * remember.
   */
  async _leave(go) {
    if (this._dirty) {
      const leaving = await this._ask({
        title: this._t("discard_changes"),
        text: this._t("discard_changes_hint"),
        confirm: this._t("discard"),
        danger: true,
      });
      if (!leaving) return false;
    }
    this._dirty = false;
    go();
    return true;
  }

  /**
   * Keep the browser's history in step with where in the panel you are.
   *
   * One entry per screen, carrying the screen itself rather than a depth --
   * so back and forward both work, and both land where they say rather than
   * one step towards the top. The URL never changes; only the state does.
   */
  _snapshot() {
    return {
      view: this._view,
      roomId: this._roomId,
      modeId: this._modeId,
      // By index, so what comes back is the saved scene rather than a draft
      // somebody abandoned three screens ago.
      sceneIndex: this._scene
        ? (this._room?.scenes || []).findIndex(
            (scene) => scene.scene_id === this._scene.scene_id
          )
        : -1,
    };
  }

  _syncHistory() {
    if (this._restoring) return;
    const snapshot = this._snapshot();
    const known = history.state?.bl;
    if (JSON.stringify(known) === JSON.stringify(snapshot)) return;
    if (known === undefined) {
      // The entry we arrived on is not ours to replace with a deeper one.
      history.replaceState({ ...history.state, bl: snapshot }, "");
      return;
    }
    history.pushState({ ...history.state, bl: snapshot }, "");
  }

  /** The browser moved. Go where it went, or let it leave. */
  async _handlePop(event) {
    const snapshot = event?.state?.bl;
    // Not one of ours: the browser is leaving the panel, and is right to.
    if (!snapshot) return;
    if (this._dirty) {
      const leaving = await this._ask({
        title: this._t("discard_changes"),
        text: this._t("discard_changes_hint"),
        confirm: this._t("discard"),
        danger: true,
      });
      if (!leaving) {
        // Put back the entry the browser took, so where we are and what the
        // history believes agree again.
        history.pushState({ ...history.state, bl: this._snapshot() }, "");
        return;
      }
    }
    this._dirty = false;
    await this._stopPreview();

    this._restoring = true;
    try {
      this._roomId = snapshot.roomId;
      this._modeId = snapshot.modeId;
      this._view = snapshot.view || { kind: "rooms" };
      const scenes = this._room?.scenes || [];
      this._scene =
        snapshot.sceneIndex >= 0 && scenes[snapshot.sceneIndex]
          ? JSON.parse(JSON.stringify(scenes[snapshot.sceneIndex]))
          : null;
      this._paint();
    } finally {
      this._restoring = false;
    }
  }

  /**
   * What this is, and which version of it you are looking at.
   *
   * Two versions, because they can differ: the integration that Home
   * Assistant loaded, and the page the browser is running -- which is
   * whatever it was served, and stays that way until it is reloaded.
   */
  async _showAbout() {
    let about = {};
    try {
      about = await this._call("version");
    } catch {
      // Offline or an older backend; the dialog still says what it can.
    }
    const stale = about.panel && OWN_VERSION && about.panel !== OWN_VERSION;
    const link = (href, label) =>
      href
        ? `<a href="${href}" target="_blank" rel="noopener">${label}</a>`
        : "";

    const backdrop = document.createElement("div");
    backdrop.className = "modal";
    backdrop.innerHTML = `
      <div class="modal-card">
        <div class="about-head">
          <!-- The registry first, since a newer icon there wins without a
               release here; then the copy this integration ships, which is
               what every install has. There is no third case worth drawing a
               generic lamp for. -->
          <img class="about-icon" alt="" src="${BRAND_ICON}"
               data-fallback="${OWN_ICON}"
               onerror="if (this.dataset.fallback) {
                          this.src = this.dataset.fallback;
                          this.dataset.fallback = '';
                        }">
          <h2>${about.name || "Better Lighting"}</h2>
        </div>
        <p class="muted">${this._t("about_blurb")}</p>
        <table class="about">
          <tr><th>${this._t("about_version")}</th>
              <td>${about.version || "—"}</td></tr>
          <tr><th>${this._t("about_page")}</th>
              <td>${OWN_VERSION || "—"}${
                stale ? ` · ${this._t("update_available")}` : ""
              }</td></tr>
        </table>
        <p>${link(about.documentation, this._t("about_repo"))}${
          about.documentation && about.issues ? " · " : ""
        }${link(about.issues, this._t("about_issues"))}</p>
        <button class="shut" id="about-close" title="${this._t(
          "close"
        )}">&times;</button>
      </div>`;
    this.shadowRoot.appendChild(backdrop);
    const shut = () => backdrop.remove();
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) shut();
    });
    backdrop.querySelector("#about-close").addEventListener("click", shut);
  }

  /**
   * Ask, in a dialog of the page's own rather than the browser's.
   *
   * Home Assistant's confirmation dialog is reached through an import from
   * its own module graph, which a panel loaded on its own cannot do -- so
   * this is built from the same card, divider and button the rest of the
   * page is built from, and behaves the way its dialogs do: escape or the
   * backdrop to back out, and the destructive answer coloured as such.
   */
  _ask({ title, text, confirm, danger = false }) {
    return new Promise((resolve) => {
      const backdrop = document.createElement("div");
      backdrop.className = "modal";
      backdrop.innerHTML = `
        <div class="modal-card" role="alertdialog" aria-modal="true">
          <h2>${title || this._t("confirm_delete")}</h2>
          ${text ? `<p class="muted">${text}</p>` : ""}
          <div class="page-foot">
            <div class="foot-end"></div>
            <div class="foot-end">
              <button class="flat" id="ask-no">${this._t("cancel")}</button>
              <button class="${danger ? "danger" : ""}" id="ask-yes">${
                confirm || this._t("delete")
              }</button>
            </div>
          </div>
        </div>`;
      this.shadowRoot.appendChild(backdrop);

      const answer = (value) => {
        window.removeEventListener("keydown", onKey);
        backdrop.remove();
        resolve(value);
      };
      const onKey = (event) => {
        if (event.key === "Escape") answer(false);
        if (event.key === "Enter") answer(true);
      };
      window.addEventListener("keydown", onKey);
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) answer(false);
      });
      backdrop.querySelector("#ask-no").addEventListener("click", () => answer(false));
      backdrop.querySelector("#ask-yes").addEventListener("click", () => answer(true));
      backdrop.querySelector("#ask-yes").focus();
    });
  }

  /** Ask for a word, in the same dialog as everything else. */
  _askName(title, suggestion = "") {
    return new Promise((resolve) => {
      const backdrop = document.createElement("div");
      backdrop.className = "modal";
      backdrop.innerHTML = `
        <div class="modal-card" role="dialog" aria-modal="true">
          <h2>${title}</h2>
          <input type="text" id="ask-name" value="${suggestion}">
          <div class="page-foot">
            <div class="foot-end"></div>
            <div class="foot-end">
              <button class="flat" id="ask-no">${this._t("cancel")}</button>
              <button id="ask-yes">${this._t("save")}</button>
            </div>
          </div>
        </div>`;
      this.shadowRoot.appendChild(backdrop);
      const field = backdrop.querySelector("#ask-name");
      const answer = (value) => {
        window.removeEventListener("keydown", onKey);
        backdrop.remove();
        resolve(value);
      };
      const onKey = (event) => {
        if (event.key === "Escape") answer(null);
        if (event.key === "Enter") answer(field.value.trim() || null);
      };
      window.addEventListener("keydown", onKey);
      backdrop.addEventListener("click", (event) => {
        if (event.target === backdrop) answer(null);
      });
      backdrop.querySelector("#ask-no").addEventListener("click", () => answer(null));
      backdrop
        .querySelector("#ask-yes")
        .addEventListener("click", () => answer(field.value.trim() || null));
      field.focus();
      field.select();
    });
  }

  /** Ask before something that cannot be undone. */
  _confirm(text) {
    return this._ask({
      title: text || this._t("confirm_delete"),
      text: text ? undefined : this._t("confirm_delete_hint"),
      danger: true,
    });
  }

  /**
   * Open or close an accordion by animating its height.
   *
   * The browser gives no way to transition this on its own -- a closed
   * details has no box to animate -- beyond ::details-content, which most
   * browsers still do not have. So the open is deferred until the animation
   * has run, and the close until it has finished running.
   */
  /**
   * Animate one element's height open or shut, and say when it is done.
   *
   * The same movement the accordions make, which is the one that looks like
   * something folding rather than something being swapped for something
   * else. The height is measured rather than guessed, so a list of two and a
   * list of twenty each take the same time and neither jumps.
   */
  _foldHeight(element, opening) {
    if (!element || this._reducedMotion()) return Promise.resolve();
    const height = element.scrollHeight;
    element.style.overflow = "hidden";
    const animation = element.animate(
      {
        height: opening ? ["0px", `${height}px`] : [`${height}px`, "0px"],
        opacity: opening ? [0, 1] : [1, 0],
      },
      { duration: 180, easing: "ease" }
    );
    return animation.finished
      .catch(() => {})
      .then(() => {
        element.style.overflow = "";
      });
  }

  _reducedMotion() {
    return Boolean(
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    );
  }

  /**
   * Fold a branch of the menu, showing it happening.
   *
   * Closing has to run before the repaint that removes the rows, and opening
   * after the one that adds them -- which is the whole reason this is not
   * simply a class on a wrapper.
   */
  async _foldBranch(find, change) {
    const nav = this.shadowRoot.getElementById("rooms");
    const showing = find(nav);
    if (showing) await this._foldHeight(showing, false);
    change();
    this._paintNav();
    const opened = find(nav);
    if (opened) await this._foldHeight(opened, true);
  }

  _foldClick(event) {
    const summary = event.target.closest?.("summary");
    const fold = summary?.parentElement;
    if (!summary || fold?.tagName !== "DETAILS") return;
    if (this._reducedMotion()) return;
    const body = [...fold.children].find((child) => child !== summary);
    if (!body || fold.dataset.busy) return;

    event.preventDefault();
    fold.dataset.busy = "1";
    const opening = !fold.open;
    if (opening) fold.open = true;
    this._foldHeight(body, opening).then(() => {
      if (!opening) fold.open = false;
      delete fold.dataset.busy;
    });
  }

  /** Open or shut the menu drawer. Nothing at all on a wide screen. */
  _setDrawer(open) {
    this._navOpen = open;
    this.shadowRoot.getElementById("rooms").dataset.open = open ? "1" : "0";
    this.shadowRoot.getElementById("scrim").dataset.open = open ? "1" : "0";
  }

  /** Put the menu at whatever width and shape it was left in. */
  _applyNavLayout() {
    const body = this.shadowRoot.querySelector(".body");
    if (!body) return;
    body.style.setProperty("--nav", `${this._navWidth}px`);
    body.dataset.rail = this._railed ? "1" : "0";
  }

  /**
   * Fold the menu down to a column of icons, or back out again.
   *
   * On a narrow screen there is no room for either shape, so the same button
   * opens the drawer instead -- one button that means "the menu", whichever
   * of the two that is here.
   */
  _toggleNav() {
    if (window.innerWidth <= 800) {
      this._setDrawer(!this._navOpen);
      return;
    }
    // Where the button is now, so the one that comes back can be rolled
    // from here to there rather than appearing at the other end.
    const before = this.shadowRoot
      .getElementById("nav-fold")
      ?.getBoundingClientRect();

    this._railed = !this._railed;
    _remember("bl-nav-rail", this._railed ? 1 : 0);
    this._applyNavLayout();
    // The button carries the direction it will go next, so it is drawn again
    // now that the direction has changed.
    this._paintNav();

    const button = this.shadowRoot.getElementById("nav-fold");
    if (!button || !before || this._reducedMotion()) return;
    const shift = before.left - button.getBoundingClientRect().left;
    if (!shift) return;
    // Rolled rather than slid: a wheel going left turns anticlockwise, and
    // half a turn is what leaves the chevron facing the way it now means.
    button.animate(
      [
        { transform: `translateX(${shift}px) rotate(${shift > 0 ? 180 : -180}deg)` },
        { transform: "none" },
      ],
      { duration: 260, easing: "ease" }
    );
  }

  /**
   * Drag the menu's edge to give it more room, or less.
   *
   * Never more than a quarter of the window, because the content is what the
   * page is for; and dragged in past the point where it stops being readable,
   * letting go folds it to the rail rather than leaving a sliver.
   */
  _startResize(event) {
    const body = this.shadowRoot.querySelector(".body");
    if (!body || window.innerWidth <= 800) return;
    event.preventDefault();
    body.dataset.dragging = "1";
    const gutter = parseFloat(getComputedStyle(body).paddingLeft) || 0;
    let width = this._navWidth;
    let folding = false;

    const move = (moved) => {
      const wanted = moved.clientX - body.getBoundingClientRect().left - gutter;
      folding = wanted < NAV_MIN - 40;
      width = clamp(wanted, NAV_MIN, navMax());
      body.style.setProperty("--nav", `${folding ? NAV_MIN : width}px`);
      body.style.opacity = folding ? ".85" : "";
    };
    const done = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", done);
      delete body.dataset.dragging;
      body.style.opacity = "";
      if (folding) {
        this._railed = true;
        _remember("bl-nav-rail", 1);
      } else {
        this._navWidth = width;
        _remember("bl-nav-width", width);
      }
      this._applyNavLayout();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", done);
  }

  /** An mdi icon, when Home Assistant's element for drawing one is here. */
  _icon(name) {
    return customElements.get("ha-icon")
      ? `<ha-icon icon="${name}"></ha-icon>`
      : "";
  }

  /** What to call one screen. */
  _sectionName(key) {
    // Every form has a "basic" group and no flow menu names it -- the room's
    // is called Lights there, the hub's and the switch's nothing at all --
    // so the panel supplies one word for all of them.
    if (key === "basic") return this._t("basics");
    // Flow sections first, then the panel's own words: a screen the panel
    // adds itself -- light groups -- has no section in the flow, and the
    // slug fallback put "light groups" in the breadcrumb under a menu entry
    // reading "Light groups".
    const word = this._schema?.ui?.[key];
    return (
      this._labels.sections[key] || word || String(key).replace(/_/g, " ")
    );
  }

  _paint() {
    this._paintNav();
    this._paintMain();
  }

  _paintNav() {
    const nav = this.shadowRoot.getElementById("rooms");
    const sections = (this._schema?.forms.zone || []).map((group) => group.section);
    const icon = (name) => this._icon(name);
    // One chevron, turned. Swapping it for a different icon is a thing
    // that cannot be watched happening, and turning is the movement that
    // says what folding means.
    const twist = (key) =>
      `<span class="twist ${this._collapsed[key] ? "" : "open"}"
        data-twist="${key}">${icon("mdi:chevron-right")}</span>`;

    // No section chosen means the first one, which is what the content pane
    // falls back to -- so the highlight has to agree with it, or a room opens
    // showing Group behaviour with nothing in the list marked.
    const current = this._view.section || sections[0];
    // Only the screens that belong to a room keep that room open. Colour
    // presets and diagnostics are nobody's room, and leaving the room lit
    // while one of those was showing is what made the highlight look broken.
    const inRoom = [
      "room",
      "scenes",
      "switches",
      "calibrations",
      "light_groups",
      "room_zones",
    ].includes(this._view.kind);
    // Opening a room shows its screens; the chevron folds it away again.
    if (inRoom && this._expanded === undefined) this._expanded = this._roomId;
    const extras = [
      ["scenes", this._t("scenes")],
      ["switches", this._t("switches")],
      ["calibrations", this._t("calibration")],
      ["light_groups", this._t("light_groups")],
      ["room_zones", this._t("room_zones")],
    ];

    const roomRows = this._rooms
      .map((room) => {
        const open = this._expanded === room.id;
        // Two rooms can be unfolded at once, and only one of them is the
        // room you are in -- so which screen is current is a question about
        // this room, not about the view alone. And the room last visited is
        // not the room you are in once you have gone to a mode or the global
        // settings, which is what left a dot on it afterwards.
        const here = inRoom && room.id === this._roomId;
        const children = open
          ? `<ul class="sub">${[
              ...sections.map(
                (section) =>
                  `<li data-section="${section}" data-room="${room.id}"
                    aria-selected="${
                    here && this._view.kind === "room" && current === section
                    }">${icon(SECTION_ICONS[section] || "mdi:circle-small")}<span
                    class="grow">${this._sectionName(section)}</span></li>`
              ),
              ...extras.map(([key, fallback]) => {
                const branch = `${room.id}:${key}`;
                // Scenes and switches carry their own entries underneath, so
                // one is reachable without first opening a list of them.
                const items =
                  key === "scenes"
                    ? (room.scenes || []).map((scene, index) => [
                        index,
                        scene.name,
                        // Its own icon, which is why a scene has one.
                        scene.icon || SECTION_ICONS.scenes,
                      ])
                    : key === "switches"
                      ? (room.data?.switches || []).map((item, index) => [
                          index,
                          item.name || this._t("switch"),
                          SECTION_ICONS.switches,
                        ])
                      : [];
                const shown = here && this._view.kind === key;
                const open = this._expandedSub === branch;
                const children = open
                  ? `<div class="sub-wrap" data-branch="${branch}">
                      <ul class="sub">${items
                        .map(
                          ([index, label, name]) =>
                            `<li data-section="${key}" data-room="${room.id}"
                              data-item="${index}" aria-selected="${
                                shown && this._view.index === index
                              }">${icon(name)}<span class="grow">${label}</span></li>`
                        )
                        .join("")}
                        <li class="add" data-section="${key}" data-room="${room.id}"
                          data-item="new">${icon("mdi:plus")}<span class="grow">${this._t(
                            "add"
                          )}</span></li></ul>
                     </div>`
                  : "";
                return `<li data-section="${key}" data-room="${room.id}"
                  aria-expanded="${open}" data-inside="${
                    shown && !open ? "1" : "0"
                  }" aria-selected="${
                    shown && this._view.index === undefined
                  }">${icon(SECTION_ICONS[key])}<span class="grow">${
                    this._labels.sections[key] || fallback
                  }</span><span class="twist ${open ? "open" : ""}"
                    data-sub-twist="${branch}">${icon(
                    "mdi:chevron-right"
                  )}</span></li>${children}`;
              }),
            ].join("")}</ul>`
          : "";
        // Open, but not selected: the screen you are on is one of the rows
        // underneath, and two highlights at once say two things are current.
        return `<li class="room" data-room="${room.id}" aria-expanded="${open}"
          data-inside="${here && !open ? "1" : "0"}" aria-selected="false">${icon(
            room.data?.icon || "mdi:lightbulb-group"
          )}<span class="grow">${room.name}</span><span class="twist ${open ? "open" : ""}"
            data-room-twist="${room.id}">${icon(
            "mdi:chevron-right"
          )}</span></li>${children}`;
      })
      .join("");

    nav.innerHTML = `
      <div class="nav-top">
        <button class="icon-btn" id="nav-fold" title="${this._t("menu")}">${icon(
          this._railed ? "mdi:chevron-double-right" : "mdi:chevron-double-left"
        )}</button>
      </div>
      <div class="nav-body" id="nav-body">
        <ul>
          <li class="section" data-overview="rooms" data-inside="${
            this._collapsed.rooms && inRoom ? "1" : "0"
          }" data-within="${inRoom ? "1" : "0"}" aria-selected="${
            this._view.kind === "rooms"
          }">${icon("mdi:home-group")}<span class="grow">${this._t(
            "rooms"
          )}</span>${twist("rooms")}</li>
        </ul>
        <ul class="group"${this._collapsed.rooms ? " hidden" : ""}>
          ${roomRows}
          <li class="add" id="add-room">${icon("mdi:plus")}<span class="grow">${this._t(
            "add_room"
          )}</span></li>
        </ul>
        <ul class="nav-group">
          <li class="section" data-overview="modes" data-inside="${
            this._collapsed.modes && this._view.kind === "mode" ? "1" : "0"
          }" data-within="${this._view.kind === "mode" ? "1" : "0"}" aria-selected="${
            this._view.kind === "modes"
          }">${icon("mdi:auto-mode")}<span class="grow">${this._t(
            "modes"
          )}</span>${twist("modes")}</li>
        </ul>
        <ul class="group"${this._collapsed.modes ? " hidden" : ""}>
          ${this._modes
            .map((mode) => {
              const here = this._view.kind === "mode" && this._modeId === mode.id;
              const open = this._expanded === mode.id;
              const section = this._view.section || "settings";
              const children = open
                ? `<ul class="sub">${[
                    ["settings", "mdi:cog-outline", this._t("basics")],
                    [
                      "rules",
                      SECTION_ICONS.rules,
                      this._labels.sections.rules || this._t("rules"),
                    ],
                  ]
                    .map(
                      ([key, name, label]) =>
                        `<li data-mode-section="${key}" data-mode="${mode.id}"
                          aria-selected="${here && section === key}">${icon(
                            name
                          )}<span class="grow">${label}</span></li>`
                    )
                    .join("")}</ul>`
                : "";
              return `<li class="mode" data-mode="${mode.id}"
                aria-expanded="${open}" data-inside="${
                  here && !open ? "1" : "0"
                }" aria-selected="false">${icon(
                  mode.data?.icon || "mdi:movie-open"
                )}<span class="grow">${mode.name}</span><span class="twist ${open ? "open" : ""}"
                  data-room-twist="${mode.id}">${icon(
                  "mdi:chevron-right"
                )}</span></li>${children}`;
            })
            .join("")}
          <li class="add" id="add-mode">${icon("mdi:plus")}<span class="grow">${this._t(
            "add_mode"
          )}</span></li>
        </ul>
        <ul class="nav-group nav-last">
          <li class="section" data-hub="1" aria-selected="${
            this._view.kind === "hub"
          }">${icon("mdi:cog")}<span class="grow">${this._t(
            "global_settings"
          )}</span></li>
          <li class="section" data-presets="1" aria-selected="${
            this._view.kind === "presets"
          }">${icon(SECTION_ICONS.presets)}<span class="grow">${
            this._labels.sections.presets || this._t("colour_presets")
          }</span></li>
          <li class="section" data-effects="1" aria-selected="${
            this._view.kind === "effects"
          }">${icon("mdi:flare")}<span class="grow">${this._t(
            "effects"
          )}</span></li>
          <li class="section" data-control="1" aria-selected="${
            this._view.kind === "control"
          }">${icon("mdi:tune")}<span class="grow">${this._t(
            "control"
          )}</span></li>
          <li class="section" data-diagnostics="1" aria-selected="${
            this._view.kind === "diagnostics"
          }">${icon("mdi:stethoscope")}<span class="grow">${this._t(
            "diagnostics"
          )}</span></li>
        </ul>
      </div>`;

    // Choosing something closes the drawer -- otherwise every tap leaves you
    // looking at the menu you just used. Harmless on a wide screen, where the
    // menu is a column and the flag governs nothing.
    const chosen = () => this._setDrawer(false);
    // And every menu entry is a way out of the screen in front of you, so
    // each of them is asked about first.
    const go = (item, handler) =>
      item.addEventListener("click", (event) => {
        event.stopPropagation();
        this._leave(() => {
          chosen();
          // Going anywhere folds the branch you were in. A handler that is
          // opening one of them says so afterwards, which is what keeps the
          // rule to "the branch you are in, and only that one" -- and it is
          // told what was open, since going back to where you already are is
          // not a reason to fold it.
          const previous = this._expandedSub;
          this._expandedSub = null;
          handler(previous);
        });
      });
    nav.querySelectorAll("li[data-overview]").forEach((item) =>
      go(item, () => {
        const kind = item.dataset.overview;
        // Already looking at it, so there is nowhere to go: the click means
        // the only other thing the row can do.
        if (this._view.kind === kind) {
          this._collapsed[kind] = !this._collapsed[kind];
          this._paintNav();
          return;
        }
        this._stopPreview();
        this._scene = null;
        this._view = { kind };
        this._paint();
      })
    );
    nav.querySelectorAll("li.room").forEach((item) =>
      go(item, () => {
        const id = item.dataset.room;
        if (inRoom && this._roomId === id) {
          this._expanded = this._expanded === id ? null : id;
          this._paintNav();
          return;
        }
        this._stopPreview();
        this._roomId = id;
        // A room you have just walked into shows its screens, and whatever
        // was open before folds away behind you.
        this._expanded = item.dataset.room;
        this._view = { kind: "room", section: null };
        this._scene = null;
        this._paint();
      })
    );
    nav.querySelectorAll("li[data-section]").forEach((item) =>
      go(item, (previous) => {
        const section = item.dataset.section;
        const chosenItem = item.dataset.item;
        const branch = `${item.dataset.room || this._roomId}:${section}`;
        this._stopPreview();
        this._scene = null;
        // A screen belongs to the room it is listed under, which is not
        // always the room you were last in now that two can be unfolded.
        this._roomId = item.dataset.room || this._roomId;
        this._expanded = this._roomId;
        if (
          ![
            "scenes",
            "switches",
            "calibrations",
            "light_groups",
            "room_zones",
          ].includes(section)
        ) {
          this._view = { kind: "room", section };
          this._paint();
          return;
        }
        if (chosenItem === undefined) {
          // The entry itself leads to the list of them, which is not inside
          // the branch -- so going there does not unfold it. The chevron
          // does that, and so does arriving at one of the entries
          // underneath. And a click on the list you are already looking at
          // is a request to fold or unfold, there being nowhere to go.
          const alreadyHere =
            (item.dataset.room || this._roomId) === this._roomId &&
            this._view.kind === section &&
            this._view.index === undefined;
          if (alreadyHere) {
            this._expandedSub = previous === branch ? null : branch;
            this._paintNav();
            return;
          }
          this._view = { kind: section };
          if (previous === branch) this._expandedSub = branch;
          this._paint();
          return;
        }
        this._view = { kind: section };
        this._expandedSub = branch;
        if (section !== "scenes") {
          // Switches and calibrations are both a list with a form behind
          // each row. Only scenes open into an editor of their own -- and
          // testing for the one rather than for the other is how Add under
          // the calibrations started making scenes.
          const items =
            this._room?.data[
              {
                switches: "switches",
                calibrations: "light_profiles",
                light_groups: "light_groups",
                room_zones: "zones",
              }[section]
            ] || [];
          this._view = {
            kind: section,
            index: chosenItem === "new" ? items.length : Number(chosenItem),
          };
          this._paint();
          return;
        }
        // A scene opens in its editor rather than as a row in a list -- but
        // the menu still has to know which of them is open, or the entry
        // above it is the one that lights up.
        const scenes = this._room?.scenes || [];
        this._scene =
          chosenItem === "new"
            ? { name: this._t("new_scene"), lights: {} }
            : JSON.parse(JSON.stringify(scenes[Number(chosenItem)]));
        this._view = {
          kind: section,
          index: chosenItem === "new" ? undefined : Number(chosenItem),
        };
        this._paint();
      })
    );
    nav.querySelectorAll("li.mode").forEach((item) =>
      go(item, () => {
        const id = item.dataset.mode;
        if (this._view.kind === "mode" && this._modeId === id) {
          this._expanded = this._expanded === id ? null : id;
          this._paintNav();
          return;
        }
        this._modeId = id;
        this._expanded = id;
        this._view = { kind: "mode", section: "settings" };
        this._paint();
      })
    );
    nav.querySelectorAll("li[data-mode-section]").forEach((item) =>
      go(item, () => {
        this._modeId = item.dataset.mode;
        this._expanded = item.dataset.mode;
        this._view = { kind: "mode", section: item.dataset.modeSection };
        this._paint();
      })
    );
    go(nav.querySelector("li[data-hub]"), () => {
      this._view = { kind: "hub" };
      this._paint();
    });
    go(nav.querySelector("li[data-presets]"), () => {
      this._view = { kind: "presets" };
      this._paint();
    });
    go(nav.querySelector("li[data-effects]"), () => {
      this._view = { kind: "effects" };
      this._paint();
    });
    go(nav.querySelector("li[data-control]"), () => {
      this._view = { kind: "control" };
      this._paint();
    });
    go(nav.querySelector("li[data-diagnostics]"), () => {
      this._view = { kind: "diagnostics" };
      this._paint();
    });
    go(nav.querySelector("#add-room"), () => {
      this._roomId = null;
      this._view = { kind: "room", section: null, creating: true };
      this._paint();
    });
    go(nav.querySelector("#add-mode"), () => {
      this._modeId = null;
      this._view = { kind: "mode", creating: true };
      this._paint();
    });
    nav.querySelectorAll("[data-room-twist]").forEach((handle) =>
      handle.addEventListener("click", (event) => {
        event.stopPropagation();
        const id = handle.dataset.roomTwist;
        const opening = this._expanded !== id;
        // The list is whatever follows the row the chevron sits on, which
        // is only there while the branch is open -- so it is looked up again
        // on the far side of the repaint rather than held on to.
        this._foldBranch(
          (nav) => {
            const twist = nav.querySelector(
              `[data-room-twist="${CSS.escape(id)}"]`
            );
            const list = twist?.closest("li")?.nextElementSibling;
            return list?.matches("ul.sub") ? list : null;
          },
          () => {
            this._expanded = opening ? id : null;
          }
        );
        this._turnTwist(`[data-room-twist="${CSS.escape(id)}"]`, opening);
      })
    );
    nav.querySelector("#nav-fold").addEventListener("click", (event) => {
      event.stopPropagation();
      this._toggleNav();
    });
    nav.querySelectorAll("[data-sub-twist]").forEach((handle) =>
      handle.addEventListener("click", (event) => {
        event.stopPropagation();
        const id = handle.dataset.subTwist;
        const opening = this._expandedSub !== id;
        this._foldBranch(
          (nav) => nav.querySelector(`.sub-wrap[data-branch="${CSS.escape(id)}"]`),
          () => {
            this._expandedSub = opening ? id : null;
          }
        );
        this._turnTwist(`[data-sub-twist="${CSS.escape(id)}"]`, opening);
      })
    );
    nav.querySelectorAll("[data-twist]").forEach((handle) =>
      handle.addEventListener("click", (event) => {
        // Folding a group is not going anywhere, so the row it sits on must
        // not navigate and the content must not be repainted.
        event.stopPropagation();
        const key = handle.dataset.twist;
        this._collapsed[key] = !this._collapsed[key];
        this._paintNav();
        this._turnTwist(
          `[data-twist="${CSS.escape(key)}"]`,
          !this._collapsed[key]
        );
      })
    );
  }

  _paintMain() {
    this._paintCrumbs();
    this._syncHistory();
    if (this._scene) return this._paintEditor();
    switch (this._view.kind) {
      case "rooms":
        return this._paintRooms();
      case "modes":
        return this._paintModes();
      case "control":
        return this._paintControl();
      case "diagnostics":
        return this._paintDiagnostics();
      case "presets":
        return this._paintPresets();
      case "effects":
        return this._paintEffects();
      case "hub": {
        // Mutated in place by the nested editor below, and carried into the
        // save: simulation rules are not form fields, so the pending values
        // will never mention them.
        const hub = { ...this._hub };
        return this._paintSettings({
          form: this._schema?.forms.hub || [],
          values: hub,
          save: (values) => this._call("save_hub", { options: { ...hub, ...values } }),
          extra: (into) => {
            hub.simulation_rules = (hub.simulation_rules || []).map((rule) => ({
              ...rule,
            }));
            this._paintNestedList(into, {
              title: this._t("simulation_rules"),
              hint: this._t("simulation_rules_hint"),
              formKey: "condition",
              items: hub.simulation_rules,
              blank: {
                check: "state_is",
                required_state: "on",
                unknown_blocks: true,
              },
              addLabel: this._t("add_rule"),
              describe: (rule) => this._describeCondition(rule),
            });
          },
        });
      }
      case "mode":
        return this._paintMode();
      case "import":
        return this._paintImport();
      case "scenes":
        return this._paintScenes();
      case "switches":
        return this._paintCollection("switches", "switch");
      case "calibrations":
        return this._paintCollection("light_profiles", "calibration");
      case "light_groups":
        return this._paintCollection("light_groups", "light_group");
      case "room_zones":
        return this._paintCollection("zones", "room_zone");
      default:
        return this._paintRoomSection();
    }
  }

  /**
   * Every room at once.
   *
   * The landing screen, and what the Rooms crumb leads back to: the menu
   * lists rooms to move between them, but a house is easier to take in as a
   * page than as a column, and there was nowhere showing what a room holds
   * before you opened it.
   */
  _paintRooms() {
    const main = this._page(
      `<ul>${this._rooms
          .map(
            (room) => `<li data-room="${room.id}">${this._icon(
              room.data?.icon || "mdi:lightbulb-group"
            )}<span class="grow">${room.name}<div class="muted">${this._t(
              "n_lights"
            ).replace("{count}", String((room.lights || []).length))} \u00b7 ${this._t(
              "n_scenes"
            ).replace(
              "{count}",
              String((room.scenes || []).length)
            )}</div></span>${this._rowActions(this._rooms.indexOf(room))}</li>`
          )
          .join("")}</ul>
       ${this._rooms.length ? "" : this._empty(this._t("no_rooms"))}`,
      `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
        "add_room"
      )}</span></button>`
    );

    this._wireRowActions(main, {
      duplicate: () => {},
      remove: async (index) => {
        await this._call("delete_room", { room_id: this._rooms[index].id });
        if (this._roomId === this._rooms[index].id) this._roomId = null;
        await this._load();
      },
    });
    main.querySelectorAll("li[data-room]").forEach((row) =>
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete],[data-duplicate]")) return;
        this._roomId = row.dataset.room;
        // Same as walking into it from the menu: its screens are showing.
        this._expanded = row.dataset.room;
        this._view = { kind: "room", section: null };
        this._paint();
      })
    );
    main.querySelector("#add").addEventListener("click", () => {
      this._roomId = null;
      this._view = { kind: "room", section: null, creating: true };
      this._paint();
    });
  }

  /** Every mode at once, with what each of them does. */
  _paintModes() {
    const main = this._page(
      `<ul>${this._modes
          .map(
            (mode) => `<li data-mode="${mode.id}">${this._icon(
              mode.data?.icon || "mdi:movie-open"
            )}<span class="grow">${mode.name}<div class="muted">${(
              mode.data?.states || []
            ).join(", ") || this._t("none")} \u00b7 ${this._t("n_rules").replace(
              "{count}",
              String((mode.data?.rules || []).length)
            )}</div></span>${this._rowActions(this._modes.indexOf(mode))}</li>`
          )
          .join("")}</ul>
       ${this._modes.length ? "" : this._empty(this._t("no_modes"))}`,
      `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
        "add_mode"
      )}</span></button>`
    );

    this._wireRowActions(main, {
      duplicate: () => {},
      remove: async (index) => {
        await this._call("delete_mode", { mode_id: this._modes[index].id });
        if (this._modeId === this._modes[index].id) this._modeId = null;
        await this._load();
      },
    });
    main.querySelectorAll("li[data-mode]").forEach((row) =>
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete],[data-duplicate]")) return;
        this._modeId = row.dataset.mode;
        this._view = { kind: "mode" };
        this._paint();
      })
    );
    main.querySelector("#add").addEventListener("click", () => {
      this._modeId = null;
      this._view = { kind: "mode", creating: true };
      this._paint();
    });
  }

  /** One room section, or the form that creates a room. */
  _paintRoomSection() {
    const groups = this._schema?.forms.zone || [];
    const creating = this._view.creating || !this._room;
    const main = this.shadowRoot.getElementById("main");

    if (creating && !this._view.creating && !this._rooms.length) {
      this._page(
        `<p class="muted">${this._t("no_rooms")}</p>`,
        `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
          "add_room"
        )}</span></button>`
      );
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
        form: groups.slice(0, 1),
        values: {},
        choices: this._choices(null),
        save: async (next) => {
          const result = await this._call("save_room", {
            room_id: null,
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
      form: [group],
      values,
      choices: this._choices(room),
      save: async (next) => {
        const result = await this._call("save_room", {
          room_id: room.id,
          data: { ...values, ...next },
        });
        this._view = { kind: "room", section: group.section };
        return result;
      },
      extra: (into) => {
        if (group.section === "presence") {
          this._paintTriggerEditors(into, values);
        }
        if (group.section === "simulation") {
          // Added to the house's rules rather than replacing them, which is
          // why they are a second list rather than an override of the first.
          values.simulation_rules = (values.simulation_rules || []).map(
            (rule) => ({ ...rule })
          );
          this._paintNestedList(into, {
            title: this._t("simulation_rules"),
            hint: this._t("room_simulation_rules_hint"),
            formKey: "condition",
            items: values.simulation_rules,
            blank: {
              check: "state_is",
              required_state: "on",
              unknown_blocks: true,
            },
            addLabel: this._t("add_rule"),
            describe: (rule) => this._describeCondition(rule),
          });
        }
      },
      // Deleting belongs on the screen that names the room, not on the one
      // about presence sensors.
      remove:
        group.section === "basic"
          ? async () => {
              await this._call("delete_room", { room_id: room.id });
              this._roomId = null;
              this._view = { kind: "room", section: null };
            }
          : null,
    });
  }

  _paintMode() {
    const mode = this._mode;
    const creating = this._view.creating || !mode;

    // Rules are a screen of their own with an entry in the menu, the way a
    // room's screens are. Underneath the settings they were a second page
    // stapled to the bottom of the first.
    if (!creating && this._view.section === "rules") {
      return this._view.rule !== undefined
        ? this._paintRules()
        : this._paintRuleList();
    }

    this._paintSettings({
      form: this._schema?.forms.mode || [],
      values: creating ? { states: [] } : { ...mode.data },
      choices: this._choices(this._room),
      save: async (next) => {
        const base = creating ? { states: [] } : { ...mode.data };
        const result = await this._call("save_mode", {
          mode_id: creating ? null : mode.id,
          data: { ...base, ...next },
        });
        this._view = { kind: "mode", section: "settings" };
        return result;
      },
      remove: creating
        ? null
        : async () => {
            await this._call("delete_mode", { mode_id: mode.id });
            this._modeId = null;
            this._view = { kind: "modes" };
          },
    });
  }

  /**
   * A switch, drawn the way Home Assistant draws one where it can.
   *
   * Used in lists rather than in forms, where ha-selector already does it:
   * this is the one beside a row you are not otherwise editing.
   */
  _toggleControl(on, onChange) {
    const stop = (event) => event.stopPropagation();
    if (customElements.get("ha-switch")) {
      const toggle = document.createElement("ha-switch");
      toggle.checked = on;
      toggle.addEventListener("click", stop);
      toggle.addEventListener("change", () => onChange(toggle.checked));
      return toggle;
    }
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = on;
    box.addEventListener("click", stop);
    box.addEventListener("change", () => onChange(box.checked));
    return box;
  }

  /** Switch one action of the current mode on or off. */
  async _setRuleEnabled(index, enabled) {
    const mode = this._mode;
    const rules = (mode.data.rules || []).map((rule, at) =>
      at === index ? { ...rule, enabled } : rule
    );
    await this._call("save_mode", {
      mode_id: mode.id,
      data: { ...mode.data, rules },
    });
    await this._load();
  }

  /** The mode's rules, on their own screen. */
  _paintRuleList() {
    const mode = this._mode;
    const rules = mode.data.rules || [];
    const roomName = Object.fromEntries(
      this._rooms.map((room) => [room.id, room.name])
    );

    const main = this._page(
      `<ul>${rules
        .map(
          (rule, index) =>
            `<li data-rule="${index}" data-off="${
              rule.enabled === false ? "1" : "0"
            }"><span class="row-toggle" data-toggle="${index}"></span><span
              class="grow">${
              roomName[rule.zones] || rule.zones || this._t("whole_house")
            }<div class="muted">${rule.action || "keep"} · ${
              (rule.mode_states || []).join(", ") || "—"
            }${
              (rule.scripts || []).length
                ? ` · ${(rule.scripts || [])
                    .map((id) => this._name(id))
                    .join(", ")}`
                : ""
            }</div></span>${this._rowActions(index)}</li>`
        )
        .join("")}</ul>
       ${rules.length ? "" : this._empty(this._t("no_rules"))}`,
      `<button class="tonal" id="add-rule">${this._icon("mdi:plus")}<span>${this._t(
        "add"
      )}</span></button>`
    );

    this._wireRowActions(main, {
      duplicate: () => {},
      remove: async (index) => {
        await this._call("save_mode", {
          mode_id: mode.id,
          data: { ...mode.data, rules: rules.filter((_, i) => i !== index) },
        });
        await this._load();
      },
    });
    // Switched on or off from the list, since that is where you can see all
    // of them at once and decide which one is misbehaving.
    main.querySelectorAll("[data-toggle]").forEach((slot) => {
      const at = Number(slot.dataset.toggle);
      slot.appendChild(
        this._toggleControl(rules[at].enabled !== false, (on) =>
          this._setRuleEnabled(at, on)
        )
      );
    });
    main.querySelectorAll("li[data-rule]").forEach((row) =>
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete],[data-duplicate]")) return;
        this._view = { ...this._view, rule: Number(row.dataset.rule) };
        this._paint();
      })
    );
    main.querySelector("#add-rule").addEventListener("click", () => {
      this._view = { ...this._view, rule: rules.length };
      this._paint();
    });
  }

  /** A room's switches or calibrations: a list, and a form for one of them. */
  _paintCollection(storageKey, formKey) {
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    if (!room) return;
    const items = room.data[storageKey] || [];
    const index = this._view.index;

    if (index === undefined) {
      this._page(
        `<ul>${items
          .map(
            (item, i) =>
              `<li data-index="${i}"><span class="grow">${
                item.name || item.light_entity || "—"
              }</span>${this._rowActions(i)}</li>`
          )
          .join("")}</ul>
         ${items.length ? "" : this._empty()}`,
        `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
          "add"
        )}</span></button>`
      );
      this._wireRowActions(main, {
        duplicate: () => {},
        remove: async (index) => {
          await this._call("save_room_collection", {
            room_id: room.id,
            key: storageKey,
            items: items.filter((_, i) => i !== index),
          });
          await this._load();
        },
      });
      main.querySelectorAll("li").forEach((row) =>
        row.addEventListener("click", (event) => {
          if (event.target.closest("[data-delete],[data-duplicate]")) return;
          this._view = { ...this._view, index: Number(row.dataset.index) };
          this._expandedSub = `${room.id}:${this._view.kind}`;
          this._paint();
        })
      );
      main.querySelector("#add").addEventListener("click", () => {
        this._view = { ...this._view, index: items.length };
        this._paint();
      });
      return;
    }

    const values = { ...(items[index] || {}) };
    this._paintSettings({
      form: this._schema?.forms[formKey] || [],
      values,
      choices: this._choices(room),
      save: async (next) => {
        const list = [...items];
        list[index] = { ...values, ...next };
        let result;
        try {
          result = await this._call("save_room_collection", {
            room_id: room.id,
            key: storageKey,
            items: list,
          });
        } catch (err) {
          // The server sends the loop it found and nothing else, since it
          // does not know which language this page is in. The sentence is
          // ours; the path is the useful half.
          if (err?.code === "cycle") {
            throw new Error(`${this._t("group_cycle")} (${err.message})`);
          }
          if (err?.code === "overlap") {
            throw new Error(`${this._t("zone_overlap")} (${err.message})`);
          }
          throw err;
        }
        this._view = { kind: this._view.kind };
        return result;
      },
      remove: async () => {
        const list = items.filter((_, i) => i !== index);
        await this._call("save_room_collection", {
          room_id: room.id,
          key: storageKey,
          items: list,
        });
        this._view = { kind: this._view.kind };
      },
      extra: (into) => {
        if (storageKey === "switches" && index < items.length) {
          this._paintSwitchOrder(into, index);
        }
        if (storageKey === "zones") {
          this._paintTriggerEditors(into, values);
        }
      },
    });
  }

  /**
   * A list of things, each edited by a form.
   *
   * Presets, rules and a switch's running order are all this shape, so they
   * share one implementation: show the list, or show the form for the item
   * whose index the view carries.
   */
  _paintListEditor({ items, formKey, choices, describe, onSave, reorder }) {
    const main = this.shadowRoot.getElementById("main");
    const index = this._view.index;

    if (index === undefined) {
      this._page(
        `<ul id="items">${items
          .map(
            (item, i) => `<li data-index="${i}">
                <span class="grow">${describe(item, i)}</span>
                ${
                  reorder
                    ? `<span class="moves">
                         <button class="flat" data-up="${i}" ${
                           i === 0 ? "disabled" : ""
                         }>${this._icon("mdi:arrow-up")}</button>
                         <button class="flat" data-down="${i}" ${
                           i === items.length - 1 ? "disabled" : ""
                         }>${this._icon("mdi:arrow-down")}</button>
                       </span>`
                    : ""
                }
                ${this._rowActions(i, { duplicate: true })}
              </li>`
          )
          .join("")}</ul>
         ${items.length ? "" : this._empty()}`,
        `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
          "add"
        )}</span></button>`
      );

      this._wireRowActions(main, {
        duplicate: async (index) => {
          const copy = { ...items[index] };
          copy.name = `${copy.name || ""} ${this._t("copy_suffix")}`.trim();
          const next = [...items];
          next.splice(index + 1, 0, copy);
          await onSave(next);
          await this._load();
        },
        remove: async (index) => {
          await onSave(items.filter((_, i) => i !== index));
          await this._load();
        },
      });
      main.querySelectorAll("li").forEach((row) =>
        row.addEventListener("click", (event) => {
          if (
            event.target.closest("[data-up],[data-down],[data-delete],[data-duplicate]")
          ) {
            return;
          }
          this._view = { ...this._view, index: Number(row.dataset.index) };
          this._paint();
        })
      );
      main.querySelectorAll("[data-up],[data-down]").forEach((button) =>
        button.addEventListener("click", async (event) => {
          event.stopPropagation();
          // currentTarget, not target: the click lands on the icon inside the
          // button, which carries none of these attributes.
          const data = event.currentTarget.dataset;
          const from = Number(data.up ?? data.down);
          const to = data.up ? from - 1 : from + 1;
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
      this._page(`<p class="muted">${this._t("nothing_to_import")}</p>`);
      return;
    }

    this._page(
      `<p class="muted">${this._t("import_hint")}</p><div id="list"></div>`
    );

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
            room_ids: [...chosen],
          });
          go.textContent = this._t("imported");
          await this._load();
        });
        card.appendChild(go);
      }
      list.appendChild(card);
    }

  }

  /**
   * The shapes the house knows, beside the colours it knows.
   *
   * The built-in ones are listed too, greyed and unopenable: leaving them
   * out meant the only way to learn what "breathe" was called was to find it
   * in a dropdown somewhere else. They can be copied, which is the sensible
   * way to start writing one.
   */
  _paintEffects() {
    const mine = this._hub.effects || [];
    const save = (next) =>
      this._call("save_hub", {
        options: {
          ...this._hub,
          effects: next.map((effect) => ({
            ...effect,
            effect_id: effect.effect_id || `effect_${Date.now()}`,
          })),
        },
      });

    if (this._view.index !== undefined) return this._paintEffect(mine, save);

    const main = this._page(
      `<ul>${BUILT_IN_EFFECTS.map(
        (effect) => `<li class="built-in">${this._icon(
          "mdi:flare"
        )}<span class="grow">${this._t(effect.label)}<div class="muted">${this._t(
          "built_in"
        )}</div></span><span class="row-actions"><button class="flat"
          data-copy="${effect.id}" title="${this._t("duplicate")}">${this._icon(
          "mdi:content-copy"
        )}</button></span></li>`
      ).join("")}
      ${mine
        .map(
          (effect, index) =>
            `<li data-index="${index}">${this._icon(
              "mdi:flare"
            )}<span class="grow">${effect.name || "—"}<div class="muted">${this._t(
              "n_steps"
            ).replace(
              "{count}",
              String((effect.steps || []).length)
            )}</div></span>${this._rowActions(index, { duplicate: true })}</li>`
        )
        .join("")}</ul>`,
      `<button class="tonal" id="add">${this._icon("mdi:plus")}<span>${this._t(
        "add"
      )}</span></button>`
    );

    this._wireRowActions(main, {
      duplicate: async (index) => {
        const copy = { ...mine[index], effect_id: "" };
        copy.name = `${copy.name || ""} ${this._t("copy_suffix")}`.trim();
        await save([...mine.slice(0, index + 1), copy, ...mine.slice(index + 1)]);
        await this._load();
      },
      remove: async (index) => {
        await save(mine.filter((_, i) => i !== index));
        await this._load();
      },
    });
    main.querySelectorAll("[data-copy]").forEach((button) =>
      button.addEventListener("click", async (event) => {
        // A copy of one of ours is an ordinary effect of theirs, which is
        // the only way to get a candle you can adjust.
        const built = BUILT_IN_EFFECTS.find(
          (effect) => effect.id === event.currentTarget.dataset.copy
        );
        await save([
          ...mine,
          {
            effect_id: "",
            name: `${this._t(built.label)} ${this._t("copy_suffix")}`,
            repeat: built.repeat,
            steps: built.steps.map((step) => ({ ...step })),
          },
        ]);
        await this._load();
      })
    );
    main.querySelectorAll("li[data-index]").forEach((row) =>
      row.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete],[data-duplicate]")) return;
        this._view = { ...this._view, index: Number(row.dataset.index) };
        this._paint();
      })
    );
    main.querySelector("#add").addEventListener("click", () => {
      this._view = { ...this._view, index: mine.length };
      this._paint();
    });
  }

  /**
   * Play an effect on a room, to see what it does.
   *
   * Reading a list of steps tells you almost nothing about what it looks
   * like on a wall, which is the same reason the scene editor has a live
   * mode. Through the service, so what is tried is exactly what an
   * automation would get.
   */
  _tryEffect(into, effectId) {
    const bar = document.createElement("div");
    bar.className = "bar";
    into.appendChild(bar);
    if (!this._rooms.length) return;

    let room = this._roomId || this._rooms[0].id;
    bar.appendChild(
      this._choiceControl(
        this._rooms.map((candidate) => ({
          value: candidate.id,
          label: candidate.name,
        })),
        room,
        (chosen) => {
          room = chosen;
        },
        this._t("rooms")
      )
    );

    const play = document.createElement("button");
    play.className = "flat";
    play.innerHTML = `${this._icon("mdi:play")}<span>${this._t("try_it")}</span>`;
    play.addEventListener("click", () =>
      this._hass.callService("better_lighting", "apply_effect", {
        room: room,
        effect: effectId,
        duration: 8,
      })
    );
    bar.appendChild(play);

    const stop = document.createElement("button");
    stop.className = "flat";
    stop.innerHTML = `${this._icon("mdi:stop")}<span>${this._t("stop_it")}</span>`;
    stop.addEventListener("click", () =>
      this._hass.callService("better_lighting", "stop_effect", { room: room })
    );
    bar.appendChild(stop);
  }

  /** One effect: what it is called, and the steps it is made of. */
  _paintEffect(mine, save) {
    const index = this._view.index;
    const current = mine[index] || { name: "", repeat: true, steps: [] };
    const steps = (current.steps || []).map((step) => ({ ...step }));

    this._paintSettings({
      form: this._schema?.forms.effect || [],
      values: { ...current },
      choices: {},
      save: async (changed) => {
        const next = [...mine];
        next[index] = { ...current, ...changed, steps };
        await save(next);
        this._view = { ...this._view, index: undefined };
      },
      remove:
        index < mine.length
          ? async () => {
              await save(mine.filter((_, i) => i !== index));
              this._view = { ...this._view, index: undefined };
            }
          : null,
      extra: (into) => {
        // The same shape as a room's triggers and its rules, and said once.
        this._paintNestedList(into, {
          title: this._t("steps"),
          hint: this._t("steps_hint"),
          formKey: "effect_step",
          items: steps,
          blank: { level: 100, transition: 0.4, hold: 0.2 },
          addLabel: this._t("add_step"),
        });
        if (current.effect_id) this._tryEffect(into, current.effect_id);
      },
    });
  }

  /** A rule in one line, so the list says what each one actually checks. */
  _describeCondition(condition) {
    const where = condition.condition_entity || "—";
    switch (condition.check) {
      case "time_window":
        return `${(condition.window_start || "00:00:00").slice(0, 5)} – ${(
          condition.window_end || "00:00:00"
        ).slice(0, 5)}`;
      case "below":
        return `${where} < ${condition.threshold}`;
      case "above":
        return `${where} > ${condition.threshold}`;
      default:
        return `${where} = ${condition.required_state || "on"}`;
    }
  }

  /**
   * A list of small things edited inside a bigger form.
   *
   * An effect's steps were this already; a room's triggers and its rules are
   * the same shape, and saying it three times would be three places for the
   * add button to drift. The list is mutated in place and handed back through
   * ``onChange`` so the form that holds it saves it along with everything
   * else -- these are not lists with a page of their own.
   */
  _paintNestedList(into, { title, hint, formKey, items, blank, addLabel, describe }) {
    const fold = document.createElement("details");
    fold.open = true;
    fold.innerHTML = `<summary>${title}</summary>
      <div class="fold-body">
        ${hint ? `<p class="muted">${hint}</p>` : ""}
        <ul class="nested"></ul>
      </div>`;
    into.appendChild(fold);
    const list = fold.querySelector("ul");

    const draw = () => {
      list.innerHTML = "";
      items.forEach((item, at) => {
        const row = document.createElement("li");
        row.className = "step";
        if (describe) {
          const caption = document.createElement("div");
          caption.className = "muted nested-caption";
          caption.textContent = describe(item);
          row.appendChild(caption);
        }
        const form = document.createElement("bl-form");
        form.configure({
          fields: (this._schema?.forms[formKey] || []).flatMap((g) => g.fields),
          values: item,
          labels: this._labels,
          choices: {},
          states: this._hass.states,
          hass: this._hass,
        });
        form.addEventListener("value-changed", (event) => {
          items[at] = { ...items[at], [event.detail.key]: event.detail.value };
          this._touch();
          // Redrawn because a field can decide which other fields apply --
          // a rule between two times asks nothing about an entity.
          if (describe) draw();
        });
        row.appendChild(form);

        const drop = document.createElement("button");
        drop.className = "flat";
        drop.dataset.delete = "";
        drop.title = this._t("delete");
        drop.innerHTML = this._icon("mdi:delete-outline") || "\u2715";
        drop.addEventListener("click", () => {
          items.splice(at, 1);
          this._touch();
          draw();
        });
        row.appendChild(drop);
        list.appendChild(row);
      });

      const adder = document.createElement("li");
      adder.className = "add";
      adder.innerHTML = `${this._icon("mdi:plus")}<span class="grow">${addLabel}</span>`;
      adder.addEventListener("click", () => {
        items.push({ ...blank });
        this._touch();
        draw();
      });
      list.appendChild(adder);

      if (formKey === "condition") this._paintDayStrip(fold, items);
    };
    draw();
  }

  /**
   * The hours of the day these rules leave open.
   *
   * Rules are ANDed, so this is the intersection of every time window in the
   * list -- and a window whose end is earlier than its start runs through
   * midnight, which is what almost every outdoor light actually wants. That
   * is easy to get wrong reading two time fields, and obvious in a strip.
   *
   * Only the clock is drawn. A lux threshold or a helper cannot be plotted
   * against a day, so the strip says when the *clock* allows it and the hint
   * says the rest.
   */
  _dayMask(rules) {
    const minutes = new Array(1440).fill(true);
    let any = false;
    for (const rule of rules) {
      if (rule?.check !== "time_window") continue;
      const start = _minutes(rule.window_start);
      const end = _minutes(rule.window_end);
      any = true;
      if (start === end) continue; // The whole day.
      for (let at = 0; at < 1440; at += 1) {
        const inside = start < end ? at >= start && at < end : at >= start || at < end;
        if (!inside) minutes[at] = false;
      }
    }
    return any ? minutes : null;
  }

  /** Runs of the same answer, as [from, to, active] in minutes. */
  _daySegments(mask) {
    const runs = [];
    let from = 0;
    for (let at = 1; at <= 1440; at += 1) {
      if (at === 1440 || mask[at] !== mask[from]) {
        runs.push([from, at, mask[from]]);
        from = at;
      }
    }
    return runs;
  }

  async _paintDayStrip(fold, rules) {
    const body = fold.querySelector(".fold-body");
    body.querySelector(".day-strip")?.remove();
    const mask = this._dayMask(rules);
    if (!mask) return;

    const holder = document.createElement("div");
    holder.className = "day-strip";
    holder.innerHTML = `<div class="muted">${this._t("day_strip_hint")}</div>`;
    body.appendChild(holder);

    const segments = this._daySegments(mask);
    if (!(await this._dayChart(holder, segments))) {
      // Hand-drawn, for a frontend that will not lend us its chart. Boxes
      // rather than an SVG: a label inside a viewBox stretched one way and
      // not the other comes out squashed and enormous, and a label is the
      // whole point of the second attempt.
      holder.insertAdjacentHTML(
        "beforeend",
        `<div class="day-bar">${segments
          .map(([from, to, active]) => {
            const share = ((to - from) / 1440) * 100;
            return `<span class="${active ? "on" : "off"}"
                          style="width:${share}%"
                    >${share >= 12 ? this._t(active ? "active" : "inactive") : ""}</span>`;
          })
          .join("")}</div>
        <div class="day-ticks">${[0, 6, 12, 18, 24]
          .map((hour) => `<span>${String(hour).padStart(2, "0")}:00</span>`)
          .join("")}</div>`
      );
    }
  }

  /** A theme colour, resolved. */
  _themeColour(name, fallback) {
    const value = getComputedStyle(this).getPropertyValue(name).trim();
    return value || fallback;
  }

  /** The same strip, in Home Assistant's chart component. */
  async _dayChart(into, segments) {
    if (!(await ensureHaChart()) || !customElements.get("ha-chart-base")) {
      return false;
    }
    try {
      const chart = document.createElement("ha-chart-base");
      chart.hass = this._hass;
      chart.height = "96px";
      // Resolved here rather than handed over as `var(--success-color)`:
      // the chart draws to a canvas, where a CSS custom property is not a
      // colour at all -- so it quietly used its own palette instead, which
      // is why the strip came out in the wrong colours.
      const green = this._themeColour("--success-color", "#4caf50");
      const red = this._themeColour("--error-color", "#db4437");
      // One stacked bar across the day: each run of the same answer is a
      // segment of it, which is what makes a window through midnight read as
      // two ends of one night rather than as a gap.
      chart.data = segments.map(([from, to, active], index) => ({
        id: `seg${index}`,
        type: "bar",
        stack: "day",
        silent: true,
        barWidth: 30,
        itemStyle: { color: active ? green : red },
        // Only where it fits. A forty-minute segment cannot hold the word
        // "inactive", and half a word is worse than none.
        label: {
          show: (to - from) / 1440 >= 0.12,
          position: "inside",
          formatter: this._t(active ? "active" : "inactive"),
          color: "#fff",
          fontSize: 12,
        },
        data: [to - from],
      }));
      chart.options = {
        xAxis: {
          type: "value",
          min: 0,
          max: 1440,
          interval: 360,
          axisLabel: {
            formatter: (value) =>
              `${String(Math.floor(value / 60)).padStart(2, "0")}:00`,
          },
          splitLine: { show: false },
        },
        yAxis: { type: "category", data: [""], axisLine: { show: false } },
        grid: { top: 10, bottom: 0, left: 4, right: 8, containLabel: true },
        legend: { show: false },
        tooltip: { show: false },
      };
      into.appendChild(chart);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * The sensors that ask for these lights, and the rules that say whether
   * they may. Drawn on a room's Presence screen and inside a zone, because
   * that is where somebody thinks about them.
   */
  _paintTriggerEditors(into, values) {
    const triggers = (values.triggers || []).map((one) => ({ ...one }));
    const rules = (values.rules || []).map((one) => ({ ...one }));
    values.triggers = triggers;
    values.rules = rules;

    this._paintNestedList(into, {
      title: this._t("triggers"),
      hint: this._t("triggers_hint"),
      formKey: "trigger",
      items: triggers,
      blank: { trigger_entity: null },
      addLabel: this._t("add_trigger"),
    });
    this._paintNestedList(into, {
      title: this._t("rules"),
      hint: this._t("rules_hint"),
      formKey: "condition",
      items: rules,
      blank: { check: "state_is", required_state: "on", unknown_blocks: true },
      addLabel: this._t("add_rule"),
      describe: (rule) => this._describeCondition(rule),
    });
  }

  /**
   * The house, worked rather than configured.
   *
   * Everything here is reachable from a Home Assistant dashboard already --
   * that is the point of publishing entities rather than hiding behind an
   * API. But somebody who has just finished setting a room up should not have
   * to go and build a dashboard to see whether it does what they meant, and a
   * house that never gets a dashboard should still be usable from the page
   * that configured it.
   *
   * The room cards are the very same custom card, instantiated here. One
   * implementation, two places it is shown.
   */
  _paintControl() {
    const main = this._page(
      `<div id="house"></div>
       <div class="cards" id="cards"></div>
       ${this._rooms.length ? "" : this._empty()}`
    );
    this._paintHouseControls(main.querySelector("#house"));

    const cards = main.querySelector("#cards");
    for (const room of this._rooms) {
      const entityId = this._roomLight(room.id);
      if (!entityId) continue;
      const card = document.createElement("better-lighting-card");
      if (typeof card.setConfig !== "function") {
        // The card script did not load. Say so once rather than leaving a
        // row of empty boxes: it is served by us, so this is our problem.
        cards.innerHTML = `<div class="muted">${this._t("card_missing")}</div>`;
        return;
      }
      card.setConfig({ entity: entityId });
      card.hass = this._hass;
      cards.appendChild(card);
    }
  }

  /** The light entity of one room, as the room itself claims it. */
  _roomLight(roomId) {
    const states = this._hass?.states || {};
    return (
      Object.keys(states).find(
        (id) =>
          id.startsWith("light.") && states[id].attributes?.bl_room_id === roomId
      ) || null
    );
  }

  /**
   * The controls that belong to the house rather than to any room.
   *
   * Found by what they are rather than by name: the entity ids follow the
   * hub's own name, which somebody may well have renamed.
   */
  _paintHouseControls(into) {
    const states = this._hass?.states || {};
    const ours = (prefix, attribute) =>
      Object.keys(states).filter(
        (id) => id.startsWith(prefix) && states[id].attributes?.[attribute]
      );

    const simulation = Object.keys(states).find((id) =>
      id.startsWith("switch.") && "rooms" in (states[id].attributes || {})
    );
    const nightOff = Object.keys(states).find((id) =>
      id.endsWith("_night_lights_off")
    );
    const modes = ours("select.", "bl_rooms");

    const rows = [];
    if (simulation) {
      const on = states[simulation].state === "on";
      const running = (states[simulation].attributes.rooms || []).length;
      rows.push(`<li>
        ${this._icon("mdi:home-account")}
        <span class="grow">${this._t("presence_simulation")}
          <div class="muted">${
            on ? this._t("n_rooms").replace("{count}", String(running)) : this._t("idle")
          }</div></span>
        <span class="toggle-here"></span>
      </li>`);
    }
    if (nightOff) {
      rows.push(`<li>
        ${this._icon("mdi:weather-night")}
        <span class="grow">${this._t("night_lights_off")}
          <div class="muted">${this._t("night_lights_off_hint")}</div></span>
        <button class="pill" id="night">${this._t("ask")}</button>
      </li>`);
    }
    for (const id of modes) {
      const state = states[id];
      rows.push(`<li>
        ${this._icon("mdi:auto-mode")}
        <span class="grow">${state.attributes.friendly_name || id}</span>
        <select class="mode" data-entity="${id}">${(
          state.attributes.options || []
        )
          .map(
            (option) =>
              `<option${option === state.state ? " selected" : ""}>${option}</option>`
          )
          .join("")}</select>
      </li>`);
    }
    if (!rows.length) return;

    into.innerHTML = `<h3>${this._t("the_house")}</h3><ul>${rows.join("")}</ul>`;

    const slot = into.querySelector(".toggle-here");
    if (slot && simulation) {
      slot.replaceWith(
        this._toggleControl(states[simulation].state === "on", (wanted) =>
          this._hass.callService(
            "switch",
            wanted ? "turn_on" : "turn_off",
            { entity_id: simulation }
          )
        )
      );
    }
    into.querySelector("#night")?.addEventListener("click", () =>
      this._hass.callService("button", "press", { entity_id: nightOff })
    );
    into.querySelectorAll("select.mode").forEach((picker) =>
      picker.addEventListener("change", (event) =>
        this._hass.callService("select", "select_option", {
          entity_id: event.target.dataset.entity,
          option: event.target.value,
        })
      )
    );
  }

  _paintPresets() {
    const presets = this._hub.color_presets || [];
    this._paintListEditor({
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

  /**
   * One rule of the current mode, edited in place.
   *
   * ``draft`` carries answers given since the screen was drawn. A rule's
   * scenes are the scenes of the room it names, which is not known when the
   * form is first built -- so naming a room redraws the form, and until one
   * is named the two questions that depend on it are there but not yet
   * answerable.
   */
  _paintRules(draft = null) {
    const mode = this._mode;
    const rules = mode.data.rules || [];
    const index = this._view.rule;
    const current = { ...(rules[index] || {}), ...(draft || {}) };
    const ruleRoom = this._rooms.find((room) => room.id === current.zones) || null;

    const enabled = current.enabled !== false;
    this._paintSettings({
      // The switch is in the footer and in the list, so a third of it in the
      // middle of the form would be two controls for one answer.
      form: (this._schema?.forms.rule || []).map((group) => ({
        ...group,
        fields: group.fields.filter((field) => field.key !== "enabled"),
      })),
      values: { ...current },
      toggle:
        index < rules.length
          ? {
              on: enabled,
              set: (next) => this._setRuleEnabled(index, next),
            }
          : null,
      // Greyed out rather than hidden: they are part of the question, and a
      // form that grows as you answer it is harder to read than one that
      // waits.
      disabled: ruleRoom ? [] : ["action", "scene_id", "presence_entry_action"],
      onChange: (key, values) => {
        if (key === "zones") this._paintRules(values);
      },
      choices: {
        ...this._choices(ruleRoom),
        // "off" first: it is what the mode's own select calls the end of a
        // session, and a rule for it is how everything that is not a light
        // gets put back.
        mode_states: ["off", ...(mode.data.states || [])].map((state) => ({
          value: state,
          // The mode's own states are words the user chose and there is
          // nothing to translate; the idle one is ours.
          label: this._labels.options.mode_state?.[state] || state,
        })),
      },
      save: async (changed) => {
        const next = [...rules];
        next[index] = { ...current, ...changed };
        const result = await this._call("save_mode", {
          mode_id: mode.id,
          data: { ...mode.data, rules: next },
        });
        this._view = { kind: "mode", section: "rules" };
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
              this._view = { kind: "mode", section: "rules" };
            }
          : null,
    });
  }

  /**
   * The scenes one switch cycles, in order, folded into the switch's own page.
   *
   * A screen of its own was one click too many for something you want to see
   * while deciding what the switch is for. Dragged rather than nudged with
   * arrows, because that is what reordering a short list is; the arrows stay
   * for anyone who cannot drag.
   */
  _paintSwitchOrder(into, index) {
    const room = this._room;
    const switches = room.data.switches || [];
    const item = switches[index];
    if (!item) return;
    const order = (room.switch_orders || [])[index] || [];
    const names = {
      ...Object.fromEntries(
        (room.scenes || []).map((scene) => [scene.scene_id, scene.name])
      ),
      [ADAPTIVE_STEP]: this._t("adaptive"),
    };
    const icons = {
      ...Object.fromEntries(
        (room.scenes || []).map((scene) => [
          scene.scene_id,
          scene.icon || SECTION_ICONS.scenes,
        ])
      ),
      [ADAPTIVE_STEP]: "mdi:weather-sunny",
    };
    const unused = [
      ...(order.includes(ADAPTIVE_STEP)
        ? []
        : [{ scene_id: ADAPTIVE_STEP, name: this._t("adaptive") }]),
      ...(room.scenes || []).filter((scene) => !order.includes(scene.scene_id)),
    ];

    const save = async (next) => {
      const list = [...switches];
      list[index] = {
        ...item,
        scene_order: next,
        // Everything the room has that this switch no longer lists was taken
        // out on purpose, and has to stay out: without this, the next scene
        // added to the room would bring the removed ones back with it.
        scene_order_excluded: (room.scenes || [])
          .map((scene) => scene.scene_id)
          .filter((id) => !next.includes(id)),
      };
      await this._call("save_room_collection", {
        room_id: room.id,
        key: "switches",
        items: list,
      });
      await this._load();
    };

    const fold = document.createElement("details");
    fold.innerHTML = `
      <summary>${this._t("what_it_cycles")} (${order.length})</summary>
      <div class="fold-body">
        <p class="muted">${this._t("cycle_hint")}</p>
        <ul id="order">
          ${order
            .map(
              (id, i) => `<li draggable="true" data-step="${i}">
                ${this._icon(icons[id] || SECTION_ICONS.scenes)}
                <span class="grow">${i + 1}. ${names[id] || id}</span>
                <span class="moves">
                  <button class="flat" data-up="${i}" ${
                    i === 0 ? "disabled" : ""
                  }>${this._icon("mdi:arrow-up")}</button>
                  <button class="flat" data-down="${i}" ${
                    i === order.length - 1 ? "disabled" : ""
                  }>${this._icon("mdi:arrow-down")}</button>
                  <button class="flat" data-remove="${i}" ${
                    order.length > 1 ? "" : "disabled"
                  }>${this._icon("mdi:close")}</button>
                </span>
              </li>`
            )
            .join("")}
          ${
            unused.length
              ? `<li class="adder" id="add-scene"></li>`
              : `<li class="muted">${this._t("all_scenes_used")}</li>`
          }
        </ul>
      </div>`;
    into.appendChild(fold);

    fold.querySelectorAll("[data-up],[data-down],[data-remove]").forEach((button) =>
      button.addEventListener("click", async (event) => {
        // currentTarget, not target: the click lands on the icon inside the
        // button, which carries none of these attributes -- which is why
        // removing a scene from this list only sometimes worked.
        const data = event.currentTarget.dataset;
        const next = [...order];
        if (data.remove !== undefined) {
          next.splice(Number(data.remove), 1);
          // A switch has to do something. Taking the last scene out of one
          // leaves adaptive, which every room can always offer.
          if (!next.length) next.push(ADAPTIVE_STEP);
        } else {
          const from = Number(data.up ?? data.down);
          const to = data.up ? from - 1 : from + 1;
          [next[from], next[to]] = [next[to], next[from]];
        }
        await save(next);
      })
    );

    let dragged = null;
    fold.querySelectorAll("li[data-step]").forEach((row) => {
      row.addEventListener("dragstart", (event) => {
        dragged = Number(row.dataset.step);
        event.dataTransfer.effectAllowed = "move";
        // Firefox will not start a drag without something on the transfer.
        event.dataTransfer.setData("text/plain", String(dragged));
        row.classList.add("dragging");
      });
      row.addEventListener("dragend", () => row.classList.remove("dragging"));
      row.addEventListener("dragover", (event) => {
        event.preventDefault();
        row.classList.add("drop-target");
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
      row.addEventListener("drop", async (event) => {
        event.preventDefault();
        row.classList.remove("drop-target");
        const to = Number(row.dataset.step);
        if (dragged === null || dragged === to) return;
        const next = [...order];
        next.splice(to, 0, ...next.splice(dragged, 1));
        dragged = null;
        await save(next);
      });
    });

    const sceneAdder = fold.querySelector("#add-scene");
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

  /**
   * The one card a screen is drawn in.
   *
   * Every screen is a single container the height of the pane: what is on it
   * scrolls inside, and the buttons that act on it stay where they are
   * instead of being somewhere below the fold. Screens used to be a stack of
   * cards, which read as several things rather than one.
   */
  _page(body, left = "", right = "") {
    const main = this.shadowRoot.getElementById("main");
    main.innerHTML = `
      <div class="card page">
        <div class="page-body">${body}</div>
        ${
          left || right
            ? `<div class="page-foot">
                 <div class="foot-end">${left}</div>
                 <div class="foot-end">${right}</div>
               </div>`
            : ""
        }
      </div>`;
    return main;
  }

  /** The one settings screen: a form, a Save, and sometimes a Delete. */
  _paintSettings({
    form,
    values,
    choices,
    save,
    remove,
    extra,
    onChange,
    disabled,
    toggle,
  }) {
    const main = this._page(
      `<div id="error" class="muted"></div>
       <div id="form"></div>
       <div id="extra"></div>`,
      `${
        remove
          ? `<button class="danger" id="remove">${this._icon(
              "mdi:delete-outline"
            )}<span>${this._t("delete")}</span></button>`
          : ""
      }${
        toggle
          ? `<button class="tonal" id="toggle">${this._icon(
              toggle.on ? "mdi:pause-circle-outline" : "mdi:play-circle-outline"
            )}<span>${this._t(toggle.on ? "disable" : "enable")}</span></button>`
          : ""
      }`,
      `<button class="flat" id="cancel" disabled>${this._t("cancel")}</button>
       <button id="save" disabled>${this._t("save")}</button>`
    );

    const holder = main.querySelector("#form");
    const saveButton = main.querySelector("#save");
    const cancelButton = main.querySelector("#cancel");
    // What the screen opened showing, field by field: a stored value, or the
    // default the control is displaying in its absence. Compared against
    // rather than counting keystrokes, so typing a value and typing it back
    // leaves the buttons alone.
    const original = {};
    for (const group of form) {
      for (const field of group.fields) {
        original[field.key] = values[field.key] ?? field.default;
      }
    }
    const same = (a, b) =>
      JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

    let pending = {};
    const sync = () => {
      this._dirty = Object.entries(pending).some(
        ([key, value]) => !same(value, original[key])
      );
      saveButton.disabled = !this._dirty;
      cancelButton.disabled = !this._dirty;
    };
    this._dirty = false;
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
        disabled,
      });
      element.addEventListener("value-changed", (event) => {
        pending = { ...pending, [event.detail.key]: event.detail.value };
        sync();
        // Some screens have to redraw when an answer changes: which scenes a
        // rule may pick from is a question about the room it just named.
        onChange?.(event.detail.key, { ...values, ...pending });
      });
    }
    if (extra) extra(main.querySelector("#extra"));

    saveButton.addEventListener("click", async () => {
      try {
        await save(pending);
        this._dirty = false;
        await this._load();
      } catch (err) {
        main.querySelector("#error").textContent =
          err?.message || this._t("save_failed");
      }
    });
    // Cancel goes wherever the crumb would, or reloads this screen when it is
    // already the top of its branch -- either way, nothing typed is kept.
    cancelButton.addEventListener("click", () => {
      // Nothing to cancel unless something was changed, so this is a way of
      // putting the screen back rather than a way of leaving it -- the trail
      // is how you leave.
      this._dirty = false;
      this._paintMain();
    });
    main.querySelector("#toggle")?.addEventListener("click", async () => {
      this._dirty = false;
      await toggle.set(!toggle.on);
    });
    main.querySelector("#remove")?.addEventListener("click", async () => {
      if (!(await this._confirm())) return;
      this._dirty = false;
      await remove();
      await this._load();
    });
  }

  _paintScenes() {
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    if (!room) {
      this._page(`<p class="muted">${this._t("no_rooms")}</p>`);
      return;
    }
    this._page(
      `<ul><li data-adaptive="1">${this._icon(
        "mdi:weather-sunny"
      )}<span class="grow">${this._t("adaptive")}<div class="muted">${this._t(
        "adaptive_scene_hint"
      )}</div></span></li>${room.scenes
          .map(
            (scene, index) =>
              `<li data-index="${index}">${this._icon(
                scene.icon || SECTION_ICONS.scenes
              )}<span class="grow">${scene.name}<div class="muted">${this._t(
                "n_lights"
              ).replace(
                "{count}",
                String(Object.keys(scene.lights || {}).length)
              )}</div></span>${this._rowActions(index, { duplicate: true })}</li>`
          )
          .join("")}</ul>
       ${room.scenes.length ? "" : this._empty(this._t("no_scenes"))}`,
      `<button class="tonal" id="new">${this._icon("mdi:plus")}<span>${this._t(
        "new_scene"
      )}</span></button>`
    );

    // The room's own default, which is a scene in every way that matters
    // except that it cannot be deleted: it is what the room does when nothing
    // else is asked of it.
    main.querySelector("li[data-adaptive]").addEventListener("click", () => {
      this._view = { kind: "room", section: "adaptive" };
      this._paint();
    });
    this._wireRowActions(main, {
      duplicate: async (index) => {
        const copy = JSON.parse(JSON.stringify(room.scenes[index]));
        delete copy.scene_id;
        copy.name = `${copy.name} ${this._t("copy_suffix")}`;
        await this._call("save_scene", { room_id: room.id, scene: copy });
        await this._load();
      },
      remove: async (index) => {
        await this._call("delete_scene", {
          room_id: room.id,
          scene_id: room.scenes[index].scene_id,
        });
        await this._load();
      },
    });
    main.querySelectorAll("li[data-index]").forEach((item) =>
      item.addEventListener("click", (event) => {
        if (event.target.closest("[data-delete],[data-duplicate]")) return;
        this._scene = JSON.parse(JSON.stringify(room.scenes[Number(item.dataset.index)]));
        this._selectedLight = room.lights[0] || null;
        this._view = { ...this._view, index: Number(item.dataset.index) };
        this._expandedSub = `${room.id}:scenes`;
        this._paint();
      })
    );
    main.querySelector("#new").addEventListener("click", () => {
      this._scene = { name: this._t("new_scene"), lights: {} };
      this._selectedLight = room.lights[0] || null;
      this._paint();
    });
  }

  /**
   * Keep what one light is showing, under a name, for use anywhere.
   *
   * In live mode that is what the bulb is actually doing, which is the only
   * way to name a colour you arrived at by dragging a wheel; in review mode
   * it is what the scene says. Either way it becomes an ordinary colour
   * preset, so the next scene in another room can just pick it by name.
   */
  async _savePreset(entityId) {
    const spec = this._previewing
      ? this._captureOne(entityId)
      : this._scene.lights[entityId];
    if (!spec) return;
    const preset = {};
    if (spec.color_format === "color_temp_kelvin" && spec.color_temp_kelvin) {
      preset.color_format = "color_temp_kelvin";
      preset.color_temp_kelvin = spec.color_temp_kelvin;
    } else if (spec.rgb_color) {
      preset.color_format = "rgb_color";
      preset.rgb_color = [...spec.rgb_color];
    } else {
      await this._ask({
        title: this._t("no_colour_to_keep"),
        confirm: this._t("close"),
      });
      return;
    }
    const name = await this._askName(
      this._t("preset_name"),
      this._name(entityId)
    );
    if (!name) return;
    await this._call("save_hub", {
      options: {
        ...this._hub,
        color_presets: [...(this._hub.color_presets || []), { ...preset, name }],
      },
    });
    await this._load();
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

  /**
   * One light's entry in the scene, made if it has none yet.
   *
   * A light added to a scene is taken as it is now -- its brightness, its
   * colour, or off if that is what it is. Which is what the "take the room as
   * it is" button did to the whole room at once, and is more useful one light
   * at a time: build a scene by adding the lights that are already right.
   */
  _spec(entityId) {
    if (!this._scene.lights[entityId]) {
      this._scene.lights[entityId] = this._captureOne(entityId) || {
        action: "apply",
        color_format: "inherit",
      };
    }
    return this._scene.lights[entityId];
  }

  /**
   * The light's own icon, lit the way the scene will light it.
   *
   * A circle of colour said what the scene does to the light but nothing
   * about the light: a strip, a lamp and a ceiling fitting were three
   * identical dots. Home Assistant already knows which is which, so the icon
   * is its own and only the colour is ours.
   */
  _bulbIcon(entityId) {
    const spec = this._scene.lights[entityId] || {};
    if (spec.action === "off") return "mdi:lightbulb-off-outline";
    if (spec.action === "leave") return "mdi:lightbulb-question-outline";
    const state = this._hass.states[entityId];
    return (
      this._hass.entities?.[entityId]?.icon ||
      state?.attributes?.icon ||
      "mdi:lightbulb"
    );
  }

  /** How bright the scene leaves it, as something to dim the icon by. */
  _swatchDim(entityId) {
    const spec = this._scene.lights[entityId] || {};
    if (spec.action === "off") return 0.35;
    if (spec.action === "leave") return 0.5;
    const percent =
      spec.brightness_pct ??
      (this._previewing
        ? ((this._hass.states[entityId]?.attributes?.brightness || 0) / 255) * 100
        : null);
    if (percent === null) return 1;
    // Never invisible: a light at 1% is still a light somebody put there.
    return Math.max(0.35, Math.min(1, 0.35 + (percent / 100) * 0.65));
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
    // A new scene is unsaved by definition; one opened for reading has
    // nothing to save until something is changed.
    if (!this._scene.scene_id) this._dirty = true;
    const main = this.shadowRoot.getElementById("main");
    const live = this._previewing;
    const entries = Object.keys(this._scene.lights || {});
    const available = (room.lights || []).filter((id) => !entries.includes(id));

    this._page(
      `<div class="banner ${live ? "live" : ""}">
         <div class="grow">
           <strong>${live ? this._t("live_mode") : this._t("review_mode")}</strong>
           <div class="muted">${
             live ? this._t("live_hint") : this._t("review_hint")
           }</div>
         </div>
         <button id="mode">${
           live ? this._t("to_review_mode") : this._t("live_mode")
         }</button>
       </div>

       <h3>${this._t("scene")}</h3>
       <input type="text" id="name" value="${this._scene.name || ""}">

       <h3>${this._t("lights")}</h3>
       <div class="muted" style="margin-bottom:12px">${this._t(
         "light_treatment"
       )}</div>
       <div id="rows"></div>
       <div class="bar" id="add-light"></div>
       <details id="scene-settings">
         <summary>${this._sectionName("basic")}</summary>
         <div class="fold-body" id="scene-form"></div>
       </details>`,
      this._scene.scene_id
        ? `<button class="danger" id="delete">${this._icon(
             "mdi:delete-outline"
           )}<span>${this._t("delete")}</span></button>
           <button class="flat" id="duplicate">${this._icon(
             "mdi:content-copy"
           )}<span>${this._t("duplicate")}</span></button>`
        : "",
      `<button class="flat" id="cancel" disabled>${this._t("cancel")}</button>
       <button id="save" disabled>${this._t("save")}</button>`
    );

    // Everything a scene holds beyond its lights -- how long it takes, what
    // it does with the lights it does not name, and what else it means.
    // There was nowhere to say any of it on this page before.
    const settings = main.querySelector("#scene-form");
    const form = document.createElement("bl-form");
    settings.appendChild(form);
    form.configure({
      fields: (this._schema?.forms.scene || [])
        .flatMap((group) => group.fields)
        .filter((field) => !["name", "scene_lights"].includes(field.key)),
      values: { ...this._scene },
      labels: this._labels,
      choices: this._choices(room),
      states: this._hass.states,
      hass: this._hass,
    });
    form.addEventListener("value-changed", (event) => {
      this._scene[event.detail.key] = event.detail.value;
      this._touch();
    });

    main.querySelector("#name").addEventListener("input", (event) => {
      this._scene.name = event.target.value;
      this._touch();
    });
    main.querySelector("#mode").addEventListener("click", () =>
      live ? this._stopPreview().then(() => this._paintEditor()) : this._preview()
    );
    if (available.length) {
      main.querySelector("#add-light").appendChild(
        this._entityControl(available, (chosen) => {
          this._spec(chosen);
          this._touch();
          this._paintEditor();
          this._pushPreview();
        })
      );
    }
    if (this._dirty) this._touch();
    main.querySelector("#save").addEventListener("click", () => this._save());
    main.querySelector("#cancel").addEventListener("click", async () => {
      await this._stopPreview();
      this._dirty = false;
      this._scene = null;
      this._view = { kind: "scenes" };
      this._load();
    });
    // A copy to work from, which is how most second scenes in a room begin.
    main.querySelector("#duplicate")?.addEventListener("click", async () => {
      await this._stopPreview();
      this._scene = {
        ...JSON.parse(JSON.stringify(this._scene)),
        scene_id: undefined,
        name: `${this._scene.name} ${this._t("copy_suffix")}`,
      };
      await this._save();
    });
    main.querySelector("#delete")?.addEventListener("click", async () => {
      if (!(await this._confirm())) return;
      this._dirty = false;
      await this._call("delete_scene", {
        room_id: room.id,
        scene_id: this._scene.scene_id,
      });
      await this._stopPreview();
      this._scene = null;
      this._load();
    });

    this._paintLightList();
  }

  /** Something on this screen changed, so there is something to save. */
  _touch() {
    this._dirty = true;
    const main = this.shadowRoot.getElementById("main");
    for (const id of ["save", "cancel"]) {
      const button = main.querySelector(`#${id}`);
      if (button) button.disabled = false;
    }
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
        <span class="bulb" style="color:${this._swatch(
          entityId
        )};opacity:${this._swatchDim(entityId)}">${
          this._icon(this._bulbIcon(entityId)) ||
          `<span class="swatch" style="background:${this._swatch(
            entityId
          )}"></span>`
        }</span>
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
            this._touch();
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
            this._touch();
            this._pushPreview();
          }
        )
      );

      const keep = document.createElement("button");
      keep.className = "flat";
      keep.title = this._t("save_as_preset");
      keep.innerHTML = this._icon("mdi:palette-swatch-outline") || "+";
      keep.addEventListener("click", (event) => {
        event.stopPropagation();
        this._savePreset(entityId);
      });
      row.appendChild(keep);

      const remove = document.createElement("button");
      remove.className = "flat";
      remove.title = this._t("remove");
      remove.innerHTML = this._icon("mdi:delete-outline") || "\u2715";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        delete this._scene.lights[entityId];
        this._touch();
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
      room_id: this._roomId,
      scene: this._scene,
    });
    this._scene.scene_id = sceneId;
    this._dirty = false;
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
    await this._call("preview", { room_id: this._roomId, scene: this._scene });
  }

  async _stopPreview() {
    if (!this._previewing) return;
    this._previewing = false;
    await this._call("stop_preview", { room_id: this._roomId });
  }

}

customElements.define("better-lighting-panel", BetterLightingPanel);
