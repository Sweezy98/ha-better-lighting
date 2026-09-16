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

  configure({ fields, values, labels, choices, states }) {
    this._fields = fields || [];
    this._values = values || {};
    this._labels = labels || this._labels;
    this._choices = choices || {};
    this._states = states || {};
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
    switch (field.kind) {
      case "boolean": {
        const box = document.createElement("div");
        box.className = "switch";
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
      case "select":
      case "entity": {
        const options =
          field.kind === "entity" ? this._entities(field) : this._choicesFor(field);
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
      default: {
        const input = document.createElement("input");
        input.type = "text";
        input.value = value ?? "";
        input.addEventListener("change", () => this._set(field.key, input.value));
        return input;
      }
    }
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
  }

  disconnectedCallback() {
    window.removeEventListener("beforeunload", this._unload);
    this._stopPreview();
  }

  async _call(type, payload = {}) {
    return this._hass.connection.sendMessagePromise({
      type: `better_lighting/${type}`,
      ...payload,
    });
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

  /** One of the panel's own strings, in the language the browser asked for. */
  _t(key) {
    return this._schema?.ui?.[key] ?? key;
  }

  get _labels() {
    return this._schema?.labels || { data: {}, descriptions: {}, options: {}, sections: {} };
  }

  /** Home Assistant's own page for this integration, where its flows live. */
  _openIntegrationPage() {
    // There is no deep link that starts a subentry flow: the frontend's
    // redirect table offers the integration page and the add-integration
    // dialog, and nothing between. So this lands on the page the flow lives
    // on, and the panel reloads when it is returned to.
    const path = "/config/integrations/integration/better_lighting";
    history.pushState(null, "", path);
    this.dispatchEvent(
      new CustomEvent("location-changed", { bubbles: true, composed: true })
    );
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
        h2 { margin:0 0 12px; font-size:16px; font-weight:500; }
        ul { list-style:none; margin:0; padding:0; }
        li { padding:10px 12px; border-radius:8px; cursor:pointer; }
        li:hover { background:var(--secondary-background-color); }
        li[aria-selected="true"] { background:var(--primary-color); color:#fff; }
        ul.sub { margin:2px 0 8px 12px; border-left:2px solid var(--divider-color,#ddd); }
        ul.sub li { font-size:14px; padding:7px 10px; }
        h3 { margin:18px 0 10px; font-size:15px; font-weight:500; }
        details { border-top:1px solid var(--divider-color,#e0e0e0); padding:4px 0; }
        details:first-of-type { border-top:none; }
        summary { cursor:pointer; padding:12px 4px; font-size:15px; font-weight:500;
                  list-style:none; display:flex; align-items:center; gap:8px; }
        summary::-webkit-details-marker { display:none; }
        summary::before { content:"\u25B8"; transition:transform .15s;
                          color:var(--secondary-text-color); }
        details[open] > summary::before { transform:rotate(90deg); }
        details > bl-form { padding:4px 4px 12px; }
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
        input[type=text] { font:inherit; padding:8px; border-radius:8px; width:100%;
                           border:1px solid var(--divider-color,#ccc);
                           background:var(--card-background-color); color:inherit; }
        .editor { display:grid; grid-template-columns:1fr 300px; gap:16px; }
        @media (max-width:1000px) { .editor { grid-template-columns:1fr; } }
        .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:12px;
                background:var(--secondary-background-color); }
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
        <div id="main"></div>
      </div>`;
  }

  _paint() {
    const nav = this.shadowRoot.getElementById("rooms");
    const labels = this._labels;
    const sections = (this._schema?.forms.zone || []).map((group) => group.section);

    const roomRows = this._rooms
      .map((room) => {
        const open = room.id === this._roomId && this._view.kind !== "hub" &&
          this._view.kind !== "mode";
        const children = open
          ? `<ul class="sub">${[
              ...sections.map(
                (section) =>
                  `<li data-section="${section}" aria-selected="${
                    this._view.kind === "room" && this._view.section === section
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
    // Adding one is Home Assistant's own flow, not an imitation of it: its
    // dialog validates, names and creates the subentry, and it is the screen
    // people already meet everywhere else. A custom panel cannot open that
    // dialog in place -- doing so needs a dialogImport from the frontend's
    // own module graph, which is not reachable from here -- so this goes to
    // the page it lives on.
    nav.querySelector("#add-room").addEventListener("click", () =>
      this._openIntegrationPage()
    );
    nav.querySelector("#add-mode").addEventListener("click", () =>
      this._openIntegrationPage()
    );

    this._paintMain();
  }

  _paintMain() {
    if (this._scene) return this._paintEditor();
    switch (this._view.kind) {
      case "presets":
        return this._paintPresets();
      case "rules":
        return this._paintRules();
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
    const room = this._room;
    const main = this.shadowRoot.getElementById("main");
    if (!room) {
      main.innerHTML = `<div class="card"><p class="muted">${this._t(
        "no_rooms"
      )}</p><div class="bar"><button id="add">${this._t(
        "add_room"
      )}</button></div></div>`;
      main.querySelector("#add").addEventListener("click", () =>
        this._openIntegrationPage()
      );
      return;
    }

    const groups = this._schema?.forms.zone || [];
    const group = groups.find((g) => g.section === this._view.section) || groups[0];
    const values = { ...room.data };

    // Naming a room, choosing its lights and deleting it all belong to Home
    // Assistant's own flow. What is left here is what that flow does not
    // cover: how the room behaves.
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
    });
  }

  _paintMode() {
    const creating = this._view.creating || !this._mode;
    const mode = this._mode;
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
      extra: creating
        ? null
        : {
            label: `${this._labels.sections.rules || this._t("rules")} (${
              (mode.data.rules || []).length
            })`,
            go: () => {
              this._view = { kind: "rules" };
              this._paint();
            },
          },
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
          <div class="bar"><button id="add">${this._t("add")}</button>
            <button class="flat" id="back">⬅️ ${this._t("back")}</button></div>
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
      main.querySelector("#back").addEventListener("click", () => {
        this._view = { ...this._view, index: undefined, sub: undefined };
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

  /** What a mode does to each room, in each of its states. */
  _paintRules() {
    const mode = this._mode;
    if (!mode) return;
    const rules = mode.data.rules || [];
    const roomNames = Object.fromEntries(
      this._rooms.map((room) => [room.id, room.name])
    );
    // A rule names one room, so its scene picker offers that room's scenes.
    const ruleRoom = (rule) =>
      this._rooms.find((room) => room.id === rule?.zones) || null;

    this._paintListEditor({
      title: `${mode.name} — ${this._labels.sections.rules || this._t("rules")}`,
      items: rules,
      formKey: "rule",
      choices: {
        ...this._choices(ruleRoom(rules[this._view.index])),
        mode_states: (mode.data.states || []).map((state) => ({
          value: state,
          label: state,
        })),
      },
      describe: (rule) => {
        const states = (rule.mode_states || []).join(", ") || "—";
        const room = roomNames[rule.zones] || rule.zones || "—";
        return `${room}: ${rule.action || "keep"} (${states})`;
      },
      onSave: (next) =>
        this._call("save_mode", {
          mode_id: mode.id,
          data: { ...mode.data, rules: next },
        }),
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
            ? `<div class="bar"><select id="add-scene">
                 <option value="">${this._t("add_scene")}</option>
                 ${unused
                   .map(
                     (scene) =>
                       `<option value="${scene.scene_id}">${scene.name}</option>`
                   )
                   .join("")}
               </select></div>`
            : '<p class="muted">${this._t("all_scenes_used")}</p>'
        }
        <div class="bar"><button class="flat" id="back">⬅️ ${this._t("back")}</button></div>
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
    main.querySelector("#add-scene")?.addEventListener("change", async (event) => {
      if (event.target.value) await save([...order, event.target.value]);
    });
    main.querySelector("#back").addEventListener("click", () => {
      this._view = { ...this._view, sub: undefined };
      this._paint();
    });
  }

  /** The one settings screen: a form, a Save, and sometimes a Delete. */
  _paintSettings({ title, form, values, choices, save, remove, extra }) {
    const main = this.shadowRoot.getElementById("main");
    main.innerHTML = `
      <div class="card">
        <h2>${title}</h2>
        <div id="error" class="muted"></div>
        <div id="form"></div>
        <div class="bar">
          <button id="save">${this._t("save")}</button>
          ${extra ? `<button class="flat" id="extra">${extra.label}</button>` : ""}
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
    main.querySelector("#extra")?.addEventListener("click", () => extra.go());
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
        ${
          available.length || !entries.includes(ALL)
            ? `<div class="bar">
                 <select id="add-light">
                   <option value="">${this._t("add_light")}</option>
                   ${
                     entries.includes(ALL)
                       ? ""
                       : `<option value="${ALL}">${this._t("every_light")}</option>`
                   }
                   ${available
                     .map(
                       (id) =>
                         `<option value="${id}">${this._name(id)}</option>`
                     )
                     .join("")}
                 </select>
               </div>`
            : ""
        }
      </div>

      <div class="card">
        <div class="bar">
          <button id="save">${this._t("save")}</button>
          <button class="flat" id="recapture">${this._t("capture_room")}</button>
          <button class="flat" id="back">⬅️ ${this._t("back")}</button>
          ${this._scene.scene_id ? `<button class="danger" id="delete">${this._t("delete")}</button>` : ""}
        </div>
      </div>`;

    main.querySelector("#name").addEventListener("input", (event) => {
      this._scene.name = event.target.value;
    });
    main.querySelector("#mode").addEventListener("click", () =>
      live ? this._stopPreview().then(() => this._paintEditor()) : this._preview()
    );
    main.querySelector("#add-light")?.addEventListener("change", (event) => {
      if (!event.target.value) return;
      this._spec(event.target.value);
      this._paintEditor();
      this._pushPreview();
    });
    main.querySelector("#save").addEventListener("click", () => this._save());
    main.querySelector("#recapture").addEventListener("click", () => {
      this._scene.lights = this._captureRoom();
      this._paintEditor();
    });
    main.querySelector("#back").addEventListener("click", async () => {
      await this._stopPreview();
      this._scene = null;
      this._load();
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

    list.innerHTML = entries
      .map((entityId) => {
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
        return `<div class="light" data-light="${entityId}">
            <span class="swatch" style="background:${this._swatch(entityId)}"></span>
            <span class="grow">
              ${this._name(entityId)}
              <div class="muted">${this._area(entityId) || detail}</div>
            </span>
            <select data-action="${entityId}">
              ${[
                ["apply", this._t("set_it")],
                ["off", this._t("switch_it_off")],
                ["leave", this._t("leave_it_alone")],
              ]
                .map(
                  ([value, label]) =>
                    `<option value="${value}" ${
                      (spec.action || "apply") === value ? "selected" : ""
                    }>${label}</option>`
                )
                .join("")}
            </select>
            <select data-colour="${entityId}">
              ${[
                ["inherit", this._t("colour_as_set")],
                ["none", this._t("colour_follows_sun")],
              ]
                .map(
                  ([value, label]) =>
                    `<option value="${value}" ${
                      (spec.color_format === "none" ? "none" : "inherit") === value
                        ? "selected"
                        : ""
                    }>${label}</option>`
                )
                .join("")}
            </select>
            <button class="flat" data-remove="${entityId}" title="${this._t("remove")}">🗑</button>
          </div>`;
      })
      .join("");

    list.querySelectorAll(".light").forEach((row) =>
      row.addEventListener("click", (event) => {
        if (event.target.closest("select,button")) return;
        const entityId = row.dataset.light;
        if (entityId === ALL || !live) return;
        // Home Assistant's own dialog, with the controls already learned.
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId },
            bubbles: true,
            composed: true,
          })
        );
      })
    );
    list.querySelectorAll("[data-action]").forEach((select) =>
      select.addEventListener("change", () => {
        this._spec(select.dataset.action).action = select.value;
        this._paintLightList();
        this._pushPreview();
      })
    );
    list.querySelectorAll("[data-colour]").forEach((select) =>
      select.addEventListener("change", () => {
        const spec = this._spec(select.dataset.colour);
        if (select.value === "none") spec.color_format = "none";
        else delete spec.color_format;
        this._pushPreview();
      })
    );
    list.querySelectorAll("[data-remove]").forEach((button) =>
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        delete this._scene.lights[button.dataset.remove];
        this._paintEditor();
        this._pushPreview();
      })
    );
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
