/**
 * A dashboard card for one room.
 *
 * Deliberately not a light card with extra buttons. A room already knows what
 * it is doing -- adaptive, a scene, held on by hand, somebody standing in it --
 * and the card's job is to say so and offer the three things anybody actually
 * reaches for: on, brighter, a different scene. Colour and temperature are a
 * tap away in Home Assistant's own dialog rather than two more buttons that
 * get used twice a year.
 *
 * Every control is an entity this integration already publishes, so the card
 * has no private channel to the backend and keeps working if it is loaded on
 * its own.
 *
 * No build step, for the same reason the panel has none: one file, readable in
 * the browser that runs it.
 */

/** Home Assistant's own controls, borrowed when they are there. */
const HA = {
  icon: "ha-icon",
  slider: "ha-control-slider",
};

class BetterLightingCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("better-lighting-card-editor");
  }

  static getStubConfig(hass) {
    const first = Object.keys(hass?.states || {}).find(
      (id) =>
        id.startsWith("light.") &&
        hass.states[id].attributes.bl_room_id !== undefined
    );
    return { entity: first || "" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._drawn = false;
    // Held while somebody is dragging, so an echo from the bulbs does not
    // yank the handle out from under them.
    this._dragging = false;
  }

  setConfig(config) {
    const entity = config?.entity || "";
    if (entity && !entity.startsWith("light.")) {
      throw new Error("A Better Lighting card needs a room's light entity");
    }
    this._config = { name: null, ...config, entity };
    this._selectId = null;
    this._drawn = false;
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._drawn) this._render();
    this._sync();
  }

  // -- the entities this card drives --------------------------------------

  get _light() {
    return this._hass?.states?.[this._config.entity] || null;
  }

  /**
   * The room's other entities, found through the device the light belongs to.
   *
   * Configured by one entity rather than four: a room's select and its
   * buttons are not things anybody should have to look up, and they are all
   * on one device by construction.
   */
  _sibling(prefix) {
    const hass = this._hass;
    const mine = hass?.entities?.[this._config.entity];
    if (!mine?.device_id) return null;
    const found = Object.keys(hass.entities || {}).find(
      (id) =>
        id.startsWith(prefix) &&
        hass.entities[id].device_id === mine.device_id
    );
    return found || null;
  }

  get _sceneSelect() {
    // Cached only once it is found. Caching the miss would be permanent, and
    // a card can easily be drawn before the entity registry has loaded.
    if (!this._selectId) this._selectId = this._sibling("select.");
    return this._selectId ? this._hass.states[this._selectId] : null;
  }

  // -- drawing -------------------------------------------------------------

  _render() {
    this._drawn = true;
    this.shadowRoot.innerHTML = `
      <style>
        ha-card {
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }
        .head {
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
        }
        .title {
          display: flex;
          align-items: center;
          gap: 10px;
          flex: 1 1 auto;
          min-width: 0;
          cursor: pointer;
          background: none;
          border: 0;
          padding: 0;
          color: inherit;
          font: inherit;
          text-align: left;
        }
        .title:focus-visible { outline: 2px solid var(--primary-color); }
        .name {
          font-size: 1.5rem;
          font-weight: 500;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          color: var(--primary-text-color);
        }
        .room-icon { --mdc-icon-size: 28px; color: var(--state-icon-color, #9b9b9b); }
        /* Badges say why the room looks the way it does. They are not
           buttons: nothing here is a thing to press. */
        .badges { display: flex; gap: 2px; flex: 0 0 auto; }
        .badge {
          --mdc-icon-size: 18px;
          color: var(--secondary-text-color);
          opacity: .85;
        }
        .badge.on { color: var(--primary-color); opacity: 1; }
        button.round {
          flex: 0 0 auto;
          width: 44px;
          height: 44px;
          min-height: 0;
          border: 0;
          border-radius: 50%;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          transition: background .15s ease, color .15s ease;
        }
        button.round:hover { filter: brightness(1.15); }
        button.round[aria-pressed="true"] {
          background: rgba(var(--rgb-state-light-color, 255, 214, 10), .2);
          color: var(--state-light-color, #ffd60a);
        }
        button.round.power[aria-pressed="true"] {
          background: rgba(var(--rgb-primary-color, 3, 169, 244), .2);
          color: var(--primary-color);
        }
        button.round:disabled { opacity: .45; cursor: default; }
        .slider-row { display: flex; align-items: center; gap: 8px; }
        ha-control-slider { flex: 1 1 auto; --control-slider-thickness: 48px; }
        input[type="range"] { flex: 1 1 auto; }
        .scenes { display: flex; align-items: center; gap: 8px; }
        select {
          flex: 1 1 auto;
          min-width: 0;
          height: 40px;
          border-radius: 20px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          padding: 0 12px;
          font: inherit;
        }
        .step {
          flex: 0 0 auto;
          width: 40px;
          height: 40px;
          min-height: 0;
          border-radius: 50%;
          border: 1px solid var(--divider-color);
          background: none;
          color: var(--primary-text-color);
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          justify-content: center;
        }
        .step:hover { background: var(--secondary-background-color); }
        .step:disabled { opacity: .4; cursor: default; }
        .missing { padding: 16px; color: var(--error-color, #db4437); }
        /* An explicit display beats the hidden attribute, so anything that
           hides itself has to say so louder than its own layout rule. */
        [hidden] { display: none !important; }
        @media (prefers-reduced-motion: reduce) {
          button.round, .step { transition: none; }
        }
      </style>
      <ha-card>
        <div class="head">
          <button class="title" id="more">
            <span id="icon"></span>
            <span class="name" id="name"></span>
          </button>
          <div class="badges" id="badges"></div>
          <button class="round" id="adaptive" title=""></button>
          <button class="round power" id="power" title=""></button>
        </div>
        <div class="slider-row" id="slider-row"></div>
        <div class="scenes" id="scenes">
          <button class="step" id="prev" title="Previous scene"></button>
          <select id="scene"></select>
          <button class="step" id="next" title="Next scene"></button>
        </div>
      </ha-card>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    // The title is the way to colour and temperature: Home Assistant's own
    // dialog already does both well, and two more buttons on every room to
    // reach something used twice a year is a bad trade.
    $("more").addEventListener("click", () => this._openMoreInfo());
    $("power").addEventListener("click", () => this._toggle());
    $("adaptive").addEventListener("click", () => this._backToAdaptive());
    $("prev").addEventListener("click", () => this._step("select_previous"));
    $("next").addEventListener("click", () => this._step("select_next"));
    $("scene").addEventListener("change", (event) =>
      this._call("select", "select_option", {
        entity_id: this._selectId,
        option: event.target.value,
      })
    );
    this._buildSlider($("slider-row"));
  }

  /**
   * Home Assistant's own slider where it exists, a plain range where it does
   * not -- so the card still works on an older frontend rather than showing
   * an empty box.
   */
  _buildSlider(row) {
    const ha = customElements.get(HA.slider);
    const slider = document.createElement(ha ? HA.slider : "input");
    if (ha) {
      slider.mode = "start";
      slider.min = 1;
      slider.max = 100;
      slider.addEventListener("value-changed", (event) => {
        this._dragging = false;
        this._setBrightness(event.detail.value);
      });
      slider.addEventListener("slider-moved", () => {
        this._dragging = true;
      });
    } else {
      slider.type = "range";
      slider.min = "1";
      slider.max = "100";
      slider.addEventListener("input", () => {
        this._dragging = true;
      });
      slider.addEventListener("change", (event) => {
        this._dragging = false;
        this._setBrightness(Number(event.target.value));
      });
    }
    this._slider = slider;
    row.appendChild(slider);
  }

  _icon(name, className) {
    if (customElements.get(HA.icon)) {
      return `<ha-icon class="${className}" icon="${name}"></ha-icon>`;
    }
    // A dot rather than nothing: the layout should not collapse because an
    // icon set is missing.
    return `<span class="${className}">•</span>`;
  }

  // -- keeping it in step with the room ------------------------------------

  _sync() {
    const card = this.shadowRoot.querySelector("ha-card");
    if (!this._config.entity) {
      // Being drawn in the card picker before a room has been chosen.
      card.innerHTML = `<div class="missing">Choose a room.</div>`;
      this._drawn = false;
      return;
    }
    const light = this._light;
    if (!light) {
      card.innerHTML = `<div class="missing">Unknown entity: ${
        this._config.entity
      }</div>`;
      this._drawn = false;
      return;
    }

    const $ = (id) => this.shadowRoot.getElementById(id);
    const attrs = light.attributes;
    const on = light.state === "on";

    $("icon").innerHTML = this._icon(
      attrs.icon || "mdi:lightbulb-group",
      "room-icon"
    );
    $("name").textContent =
      this._config.name || attrs.friendly_name || this._config.entity;

    // Two badges, and only when they have something to say. A room with no
    // sensor is not a room nobody is in, so it shows nothing at all.
    const badges = [];
    if (attrs.bl_presence === true) {
      badges.push([`mdi:motion-sensor`, "on", "Presence detected"]);
    } else if (attrs.bl_presence === false) {
      badges.push([`mdi:motion-sensor-off`, "", "Nobody here"]);
    }
    if (on) {
      badges.push(
        attrs.bl_held_by_hand
          ? ["mdi:hand-back-right", "on", "On by hand"]
          : ["mdi:motion-sensor", "", "On automatically"]
      );
    }
    $("badges").innerHTML = badges
      .map(
        ([icon, cls, title]) =>
          `<span title="${title}">${this._icon(icon, `badge ${cls}`)}</span>`
      )
      .join("");

    // Not a toggle: there is no such thing as turning adaptive off from here.
    // It is the way back once something else has taken the room over, so it
    // is only there when there is something to come back from.
    const adaptive = attrs.bl_adaptive === true;
    const back = $("adaptive");
    back.hidden = adaptive || attrs.bl_adaptive === undefined;
    back.innerHTML = this._icon("mdi:white-balance-sunny", "");
    back.title = "Back to adaptive";

    const power = $("power");
    power.innerHTML = this._icon("mdi:power", "");
    power.setAttribute("aria-pressed", String(on));
    power.title = on ? "Turn off" : "Turn on";

    if (!this._dragging) {
      const pct = on && attrs.brightness ? Math.round((attrs.brightness / 255) * 100) : 0;
      if ("value" in this._slider) this._slider.value = pct;
      this._slider.disabled = !on;
    }

    this._syncScenes();
  }

  _syncScenes() {
    const select = this._sceneSelect;
    const row = this.shadowRoot.getElementById("scenes");
    row.hidden = !select;
    if (!select) return;

    const options = select.attributes.options || [];
    const picker = this.shadowRoot.getElementById("scene");
    const signature = options.join(" ");
    if (picker.dataset.signature !== signature) {
      picker.dataset.signature = signature;
      picker.innerHTML = options
        .map((option) => `<option value="${option}">${option}</option>`)
        .join("");
    }
    picker.value = select.state;

    const at = options.indexOf(select.state);
    const single = options.length < 2;
    this.shadowRoot.getElementById("prev").disabled = single;
    this.shadowRoot.getElementById("next").disabled = single;
    this.shadowRoot.getElementById("prev").innerHTML = this._icon(
      "mdi:chevron-left",
      ""
    );
    this.shadowRoot.getElementById("next").innerHTML = this._icon(
      "mdi:chevron-right",
      ""
    );
    picker.dataset.at = String(at);
  }

  // -- doing things --------------------------------------------------------

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data);
  }

  _toggle() {
    this._call("light", "toggle", { entity_id: this._config.entity });
  }

  /**
   * Relative, by the room's own rules.
   *
   * Setting a brightness on the room's light entity is already the relative
   * move the room was configured for -- the members keep their own headroom.
   * So this is a plain turn_on and not a fan-out.
   */
  _setBrightness(pct) {
    const value = Math.max(1, Math.min(100, Math.round(pct)));
    this._call("light", "turn_on", {
      entity_id: this._config.entity,
      brightness_pct: value,
    });
  }

  _backToAdaptive() {
    this._call("better_lighting", "set_adaptive", {
      entity_id: this._config.entity,
    });
  }

  _step(service) {
    if (!this._selectId) return;
    this._call("select", service, { entity_id: this._selectId });
  }

  _openMoreInfo() {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId: this._config.entity },
        bubbles: true,
        composed: true,
      })
    );
  }
}

/**
 * The visual editor.
 *
 * One question, and only rooms this integration publishes as answers: a list
 * of every light in the house would be a list of mostly wrong answers, since
 * the card reads attributes only a Better Lighting room has.
 */
class BetterLightingCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass) return;
    if (!this._picker) {
      this.innerHTML = "";
      this._picker = document.createElement("ha-entity-picker");
      this._picker.label = "Room";
      this._picker.allowCustomEntity = false;
      // A room, not a bulb. `includeDomains` narrows it to lights for the
      // frontends that ignore a filter function; the filter does the rest.
      this._picker.includeDomains = ["light"];
      this._picker.entityFilter = (state) =>
        state?.attributes?.bl_room_id !== undefined;
      this._picker.addEventListener("value-changed", (event) => {
        if (event.detail.value === this._config?.entity) return;
        this._config = { ...this._config, entity: event.detail.value };
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: this._config },
            bubbles: true,
            composed: true,
          })
        );
      });
      this.appendChild(this._picker);
    }
    this._picker.hass = this._hass;
    this._picker.value = this._config?.entity || "";
  }
}

if (!customElements.get("better-lighting-card")) {
  customElements.define("better-lighting-card", BetterLightingCard);
  customElements.define("better-lighting-card-editor", BetterLightingCardEditor);
}

// So the card shows up in the "Add card" picker rather than only in YAML.
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "better-lighting-card")) {
  window.customCards.push({
    type: "better-lighting-card",
    name: "Better Lighting room",
    description:
      "One room: what it is doing, how bright, and which scene. Colour and " +
      "temperature are in the dialog behind the title.",
    preview: true,
    documentationURL: "https://github.com/Sweezy98/ha-better-lighting",
  });
}
