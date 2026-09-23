/**
 * A dashboard card for one room.
 *
 * Deliberately not a light card with extra buttons. A room already knows what
 * it is doing -- adaptive, a scene, held on by hand, somebody standing in it,
 * due to switch itself off in four minutes -- and the card's job is to say so
 * and offer the three things anybody actually reaches for: on, brighter, a
 * different scene. Colour and temperature are a tap away in Home Assistant's
 * own dialog rather than two more buttons that get used twice a year.
 *
 * Every control is an entity this integration already publishes, so the card
 * has no private channel to the backend and keeps working if it is loaded on
 * its own.
 *
 * The brightness bar and the scene menu are drawn here rather than borrowed.
 * Home Assistant's own are close, but their internals move between releases,
 * and the browser's own dropdown cannot be styled at all -- which is how a
 * card ends up with one control that looks like nothing else on it.
 *
 * No build step, for the same reason the panel has none: one file, readable in
 * the browser that runs it.
 */

const HA_ICON = "ha-icon";

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

/**
 * A colour temperature as something to paint with.
 *
 * The same approximation the panel uses for its colour chips, so a room reads
 * the same warmth in both places.
 */
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

// How many scenes the menu shows before it scrolls. Fewer where there is less
// screen to give.
const MENU_ROWS = 5;

/**
 * The card's own words.
 *
 * Carried here rather than fetched: the card talks to no private endpoint of
 * ours, and a dozen strings are cheaper to ship than a round trip and a
 * dependency on the panel being installed. English is the fallback for every
 * language not listed.
 */
const WORDS = {
  en: {
    choose_room: "Choose a room.",
    unknown_entity: "Unknown entity",
    brightness: "Brightness",
    previous_scene: "Previous scene",
    next_scene: "Next scene",
    back_to_adaptive: "Back to adaptive",
    turn_on: "Turn on",
    turn_off: "Turn off",
    presence: "Presence detected",
    nobody: "Nobody here",
    by_hand: "On by hand",
    automatically: "On automatically",
    night: "Night mode",
    simulating: "Simulating presence",
    switching_off: "Switching off",
    room: "Room",
    hidden_scenes: "Hidden scenes",
    hidden_scenes_hint: "Ticked scenes are left out of this card's menu and skipped by the arrows.",
    no_scenes: "This room has no scenes yet.",
    extra_button: "Extra header button",
    extra_button_hint:
      "An entity to put beside the power button -- a house mode, a helper, " +
      "anything worth reaching from this room.",
    extra_button_icon: "Button icon",
    extra_button_icon_hint: "Left empty, the entity's own icon is used.",
  },
  de: {
    choose_room: "Raum auswählen.",
    unknown_entity: "Unbekannte Entität",
    brightness: "Helligkeit",
    previous_scene: "Vorherige Szene",
    next_scene: "Nächste Szene",
    back_to_adaptive: "Zurück zu adaptiv",
    turn_on: "Einschalten",
    turn_off: "Ausschalten",
    presence: "Anwesenheit erkannt",
    nobody: "Niemand hier",
    by_hand: "Von Hand eingeschaltet",
    automatically: "Automatisch eingeschaltet",
    night: "Nachtmodus",
    simulating: "Anwesenheit wird simuliert",
    switching_off: "Schaltet ab",
    room: "Raum",
    hidden_scenes: "Ausgeblendete Szenen",
    hidden_scenes_hint: "Angehakte Szenen fehlen im Menü dieser Karte und werden von den Pfeilen übersprungen.",
    no_scenes: "Dieser Raum hat noch keine Szenen.",
    extra_button: "Zusätzliche Kopfzeilen-Schaltfläche",
    extra_button_hint:
      "Eine Entität neben der Ein-/Aus-Schaltfläche -- ein Hausmodus, ein " +
      "Helfer, alles, was aus diesem Raum erreichbar sein soll.",
    extra_button_icon: "Symbol der Schaltfläche",
    extra_button_icon_hint: "Leer gelassen, wird das Symbol der Entität verwendet.",
  },
};

/**
 * The scene select belonging to a room's light.
 *
 * Found through the device rather than configured: a room's select is not
 * something anybody should have to look up, and the two are on one device by
 * construction.
 */
function sceneSelectFor(hass, entityId) {
  const mine = hass?.entities?.[entityId];
  if (!mine?.device_id) return null;
  return (
    Object.keys(hass.entities || {}).find(
      (id) =>
        id.startsWith("select.") && hass.entities[id].device_id === mine.device_id
    ) || null
  );
}

function words(hass) {
  const language = hass?.locale?.language || hass?.language || "en";
  return WORDS[language] || WORDS[language.split("-")[0]] || WORDS.en;
}

/**
 * What pressing an arbitrary entity ought to mean.
 *
 * The extra header button takes whatever entity somebody points it at, so it
 * cannot assume a toggle. Buttons are pressed, scenes and scripts are
 * started, anything with two states is toggled -- and anything else, a mode
 * select or a sensor, opens its own dialog, which is the only honest thing a
 * single tap can do with it.
 */
const PRESS = {
  button: ["button", "press"],
  input_button: ["input_button", "press"],
  scene: ["scene", "turn_on"],
  script: ["script", "turn_on"],
};
const TOGGLES = new Set([
  "automation",
  "cover",
  "fan",
  "humidifier",
  "input_boolean",
  "light",
  "media_player",
  "remote",
  "siren",
  "switch",
  "vacuum",
]);

/** A stand-in for the icon Home Assistant would pick from the domain. */
const DOMAIN_ICONS = {
  automation: "mdi:robot",
  binary_sensor: "mdi:radiobox-blank",
  button: "mdi:gesture-tap-button",
  climate: "mdi:thermostat",
  cover: "mdi:window-shutter",
  fan: "mdi:fan",
  input_boolean: "mdi:toggle-switch-variant",
  input_button: "mdi:gesture-tap-button",
  input_select: "mdi:format-list-bulleted",
  light: "mdi:lightbulb",
  media_player: "mdi:cast",
  scene: "mdi:palette",
  script: "mdi:script-text",
  select: "mdi:format-list-bulleted",
  sensor: "mdi:eye",
  switch: "mdi:toggle-switch-variant",
  vacuum: "mdi:robot-vacuum",
};

// States that mean the thing is not doing anything. Everything else counts
// as active, so a cover that is `open` and a player that is `playing` both
// light the button up without either needing a rule of its own.
const RESTING = new Set(["off", "closed", "idle", "unavailable", "unknown", ""]);

/**
 * The dropdown this integration uses wherever it needs one.
 *
 * Defined here because the card is its first and most demanding consumer and
 * has to work on a dashboard with nothing else of ours loaded; the panel uses
 * the same element, so the two cannot drift.
 *
 * What it does that a browser's own `select` cannot: carry an icon per
 * option, centre the current one between equal gutters, drop a popup exactly
 * as wide as itself, and show a whole number of rows -- never four and a
 * sliver, which is a scrollbar over two pixels nobody asked for.
 */
class BlDropdown extends HTMLElement {
  // How many rows to show before scrolling, where there is room for them.
  static ROWS = 5;

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._options = [];
    this._value = "";
    this._open = false;
  }

  connectedCallback() {
    if (!this._drawn) this._render();
    this._paint();
  }

  disconnectedCallback() {
    this._close();
  }

  set options(list) {
    this._options = Array.isArray(list) ? list : [];
    this._paint();
  }

  get options() {
    return this._options;
  }

  set value(next) {
    this._value = next ?? "";
    this._paint();
  }

  get value() {
    return this._value;
  }

  set disabled(off) {
    this._disabled = Boolean(off);
    this._paint();
  }

  /**
   * How to draw the current value when it is not one of the options.
   *
   * Something else can perfectly well set a value this dropdown does not
   * offer -- a scene hidden from this card, chosen from a dashboard or by an
   * automation. The trigger has to say what is actually on; the list still
   * must not offer it, because leaving it out was the point.
   */
  set current(option) {
    this._explicit = option || null;
    this._paint();
  }

  _render() {
    this._drawn = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block; position: relative;
          --bl-gutter: 20px;
          --bl-pad: 18px;
        }
        /* While open, the host outranks whatever is drawn after it. A fixed
           menu escapes an ancestor's *clipping*, but it is still painted in
           its host's place in the stacking order -- so without this it went
           underneath every section further down the page. */
        :host([open]) { z-index: 999; }
        /* Three columns rather than a row: equal gutters either side are the
           only way the label between them is centred rather than merely
           looking it. */
        .trigger {
          width: 100%; height: 42px; box-sizing: border-box;
          border-radius: 999px; border: 1px solid var(--divider-color);
          background: none; color: var(--primary-text-color); cursor: pointer;
          font: inherit; display: grid;
          grid-template-columns: var(--bl-gutter) 1fr var(--bl-gutter);
          align-items: center; padding: 0 var(--bl-pad);
        }
        .trigger:hover:not(:disabled) { background: var(--secondary-background-color); }
        .trigger:disabled { opacity: .4; cursor: default; }
        .label {
          min-width: 0; text-align: center; padding: 0 8px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .lead { display: inline-flex; justify-content: flex-start; }
        .trail { display: inline-flex; justify-content: flex-end; }
        ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }

/* As wide as the control it drops from, and no wider: a menu that
           spans more than that does not look attached to what opened it.

           Fixed rather than absolute, which is about escape rather than
           placement: absolutely positioned, it was clipped by whatever
           scrolling container it happened to sit in and added its own height
           to that container's scroll area -- so on a page of stacked rows it
           stretched the row it belonged to instead of covering it. Fixed is
           laid out against the viewport, so no ancestor's overflow can reach
           it. The cost is that it does not follow a scroll, which is why it
           closes on one. */
        .menu {
          position: fixed; box-sizing: border-box; margin: 0;
          z-index: 9; border-radius: 16px; padding: 6px;
          background: var(--card-background-color, #1c1c1c);
          border: 1px solid var(--divider-color);
          box-shadow: 0 8px 28px rgba(0, 0, 0, .5);
          overflow-y: auto; overscroll-behavior: contain;
        }
        /* The same three columns as the trigger, so a row lines up with the
           control it dropped out of: the option's icon under the trigger's,
           the tick under the chevron. One border and this padding come off
           the trigger's own, which is what puts them on the same line. */
        .menu button {
          display: grid;
          grid-template-columns: var(--bl-gutter) 1fr var(--bl-gutter);
          align-items: center; width: 100%; box-sizing: border-box;
          padding: 11px calc(var(--bl-pad) - 6px);
          border: 0; background: none; cursor: pointer; border-radius: 10px;
          color: var(--primary-text-color); font: inherit; text-align: left;
        }
        /* Nothing in the list has an icon, so there is no column for the
           labels to line up against and the gutter would be indentation for
           its own sake. */
        .menu.plain button { grid-template-columns: 1fr var(--bl-gutter); }
        .menu button:hover, .menu button:focus-visible {
          background: var(--secondary-background-color); outline: none;
        }
        .menu button[aria-selected="true"] { color: var(--primary-color); }
        /* Both cells are always occupied, even when empty. Two reasons, and
           both were bugs: an empty *element* still holds the column, where
           no element at all lets the label slide into it and take the icon's
           width -- which is how a list of modes came out as "p." -- and a
           given height keeps every row the same, where a cell sized by its
           contents made the chosen row, the one carrying the tick, taller
           than the rest and every count of rows wrong. */
        .menu button .ico, .menu button .tick {
          display: inline-flex; align-items: center; height: 20px;
        }
        .menu button .ico { justify-content: flex-start; }
        .menu button .tick { justify-content: flex-end; }
        .menu button .text {
          min-width: 0; padding: 0 8px; text-align: left;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .menu.plain button .text { padding-left: 0; }
        [hidden] { display: none !important; }
      </style>
      <button class="trigger" part="trigger" aria-haspopup="listbox">
        <span class="lead" id="lead"></span>
        <span class="label" id="label"></span>
        <span class="trail" id="trail"></span>
      </button>
      <div class="menu" id="menu" role="listbox" hidden></div>`;

    this.shadowRoot
      .querySelector(".trigger")
      .addEventListener("click", () => (this._open ? this._close() : this._show()));
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._open) {
        this._close();
        this.shadowRoot.querySelector(".trigger").focus();
      }
    });
  }

  _icon(name) {
    if (!name) return "";
    if (customElements.get("ha-icon")) {
      return `<ha-icon icon="${name}"></ha-icon>`;
    }
    return `<span>•</span>`;
  }

  _current() {
    return (
      this._options.find((option) => option.value === this._value) ||
      (this._explicit?.value === this._value ? this._explicit : null)
    );
  }

  _paint() {
    if (!this._drawn) return;
    const $ = (id) => this.shadowRoot.getElementById(id);
    const current = this._current();
    $("label").textContent = current?.label ?? this._value ?? "";
    $("lead").innerHTML = this._icon(current?.icon);
    $("trail").innerHTML = this._icon("mdi:chevron-down");
    this.shadowRoot.querySelector(".trigger").disabled =
      this._disabled || !this._options.length;
    if (this._open) this._fill();
  }

  _fill() {
    const menu = this.shadowRoot.getElementById("menu");
    // An option's own icon always shows -- it is what the row is recognised
    // by, and the tick has a column of its own on the right, under the
    // trigger's chevron. Where no option has one there is nothing to line
    // the labels up against, so that column goes rather than standing empty.
    const illustrated = this._options.some((option) => option.icon);
    menu.classList.toggle("plain", !illustrated);
    menu.innerHTML = this._options
      .map(
        (option) => `<button role="option" data-value="${option.value}"
           aria-selected="${option.value === this._value}">${
             illustrated
               ? `<span class="ico">${this._icon(option.icon)}</span>`
               : ""
           }<span class="text">${option.label}</span><span class="tick">${
             option.value === this._value ? this._icon("mdi:check") : ""
           }</span></button>`
      )
      .join("");
    menu.querySelectorAll("button").forEach((row) =>
      row.addEventListener("click", () => {
        this._close();
        const value = row.dataset.value;
        if (value === this._value) return;
        this._value = value;
        this._paint();
        this.dispatchEvent(
          new CustomEvent("value-changed", {
            detail: { value },
            bubbles: true,
            composed: true,
          })
        );
      })
    );
  }

  _show() {
    if (!this._options.length) return;
    this._open = true;
    this.setAttribute("open", "");
    const menu = this.shadowRoot.getElementById("menu");
    this._fill();

    // The top layer, where the browser puts anything that is meant to be
    // over everything else. Being fixed only escapes an ancestor's
    // *clipping*; it is still painted in this element's place in the
    // stacking order, which put the menu underneath the cards further down
    // the page. A popover has no place in that order at all.
    if (typeof menu.showPopover === "function") {
      menu.hidden = false;
      menu.popover = "manual";
      menu.showPopover();
    } else {
      menu.hidden = false;
    }
    this._place(menu);

    this._outside = (event) => {
      if (!event.composedPath().includes(this)) this._close();
    };
    document.addEventListener("pointerdown", this._outside, true);

    // `preventScroll`, because moving focus into the menu will otherwise
    // scroll whatever contains it -- and the listener below reads a scroll
    // as a reason to close, so the menu shut itself the instant it opened.
    menu
      .querySelector('[aria-selected="true"], button')
      ?.focus({ preventScroll: true });

    // A fixed menu stays where it was put, so a scroll would leave it
    // hanging over whatever slid underneath. Closing is both simpler and
    // what every other menu on the page does. Armed on the next frame, so
    // nothing the opening itself did can trip it.
    this._moved = () => this._close();
    requestAnimationFrame(() => {
      if (!this._open) return;
      window.addEventListener("scroll", this._moved, true);
      window.addEventListener("resize", this._moved);
    });
  }

  /**
   * Below the trigger unless there is no room below, and always a whole
   * number of rows.
   *
   * Everything here is measured off the menu rather than assumed, because
   * every assumption tried has been wrong by just enough to put a scrollbar
   * there for the sake of a few pixels of the row below:
   *
   * * the padding and border are read from the element -- a guessed constant
   *   was two pixels short;
   * * the rows are added up one by one instead of one row's height times a
   *   count -- rows are not all the same height, and it takes very little to
   *   make them differ;
   * * the total is rounded up, because a box asked for a fractional height
   *   is laid out at the whole pixel below it while its contents keep the
   *   fraction.
   */
  _place(menu) {
    const style = getComputedStyle(menu);
    const chrome =
      parseFloat(style.paddingTop) +
      parseFloat(style.paddingBottom) +
      parseFloat(style.borderTopWidth) +
      parseFloat(style.borderBottomWidth);

    const heights = [...menu.querySelectorAll("button")].map(
      (row) => row.getBoundingClientRect().height
    );
    if (!heights.length) heights.push(44);
    // How tall the first n rows are, so every height offered below lands on
    // a boundary between rows rather than through one.
    const upTo = (n) => heights.slice(0, n).reduce((sum, row) => sum + row, 0);

    const box = this.getBoundingClientRect();
    const below = window.innerHeight - box.bottom - 16;
    const above = box.top - 16;

    const fits = (room) => {
      let count = 0;
      while (count < heights.length && upTo(count + 1) + chrome <= room) {
        count += 1;
      }
      return Math.max(1, count);
    };
    const downwards = below >= above || fits(below) >= heights.length;
    const room = downwards ? below : above;
    const rows = Math.min(heights.length, BlDropdown.ROWS, fits(room));

    const height = Math.ceil(upTo(rows) + chrome);
    menu.style.maxHeight = `${height}px`;
    menu.style.width = `${box.width}px`;

    // Where this menu's own zero actually is.
    //
    // A fixed element is normally laid out against the viewport -- but an
    // ancestor with a transform becomes its containing block instead, and
    // the panel animates whole screens that way. Feeding viewport
    // coordinates to a menu anchored somewhere else put it off the side of
    // the window. Rather than guess which case applies, park it at zero and
    // measure where zero landed.
    menu.style.top = "0px";
    menu.style.left = "0px";
    menu.style.bottom = "auto";
    const origin = menu.getBoundingClientRect();

    menu.style.left = `${box.left - origin.left}px`;
    menu.style.top = `${
      (downwards ? box.bottom + 8 : box.top - 8 - height) - origin.top
    }px`;
  }

  _close() {
    this._open = false;
    this.removeAttribute("open");
    if (this._outside) {
      document.removeEventListener("pointerdown", this._outside, true);
      this._outside = null;
    }
    if (this._moved) {
      window.removeEventListener("scroll", this._moved, true);
      window.removeEventListener("resize", this._moved);
      this._moved = null;
    }
    const menu = this.shadowRoot?.getElementById("menu");
    if (!menu) return;
    if (menu.popover) {
      try {
        menu.hidePopover();
      } catch {
        // Already closed, which is the state we wanted anyway.
      }
    }
    menu.hidden = true;
  }
}

if (!customElements.get("bl-dropdown")) {
  customElements.define("bl-dropdown", BlDropdown);
}

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
    // What the handle is being dragged to. It wins over the entity's own
    // brightness until the drag ends, so an echo from the bulbs cannot yank
    // the handle out from under whoever is moving it.
    this._pending = null;
    this._tick = null;
    this._words = WORDS.en;
  }

  setConfig(config) {
    const entity = config?.entity || "";
    if (entity && !entity.startsWith("light.")) {
      throw new Error("A Better Lighting card needs a room's light entity");
    }
    this._config = {
      name: null,
      hidden_scenes: [],
      button_entity: "",
      button_icon: "",
      ...config,
      entity,
    };
    this._selectId = null;
    this._drawn = false;
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    const before = this._hass?.locale?.language || this._hass?.language;
    this._hass = hass;
    this._words = words(hass);
    // A language change rebuilds rather than repaints: half of these strings
    // are written into the markup once.
    if (!this._drawn || before !== (hass?.locale?.language || hass?.language)) {
      this._render();
    }
    this._sync();
  }

  disconnectedCallback() {
    this._stopTicking();
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
  get _sceneSelect() {
    // Cached only once it is found. Caching the miss would be permanent, and
    // a card can easily be drawn before the entity registry has loaded.
    if (!this._selectId) {
      this._selectId = sceneSelectFor(this._hass, this._config.entity);
    }
    return this._selectId ? this._hass.states[this._selectId] : null;
  }

  /**
   * The scenes this card offers.
   *
   * Hidden ones are dropped everywhere at once -- the menu and the two step
   * buttons -- because a next button that lands on something the menu does
   * not list is a card arguing with itself.
   */
  _options(select) {
    const hidden = new Set(this._config.hidden_scenes || []);
    return (select?.attributes?.options || []).filter(
      (option) => !hidden.has(option)
    );
  }

  // -- drawing -------------------------------------------------------------

  _render() {
    this._drawn = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { --bl-warm: var(--state-light-color, #ffc768); }
        ha-card {
          padding: 14px;
          display: flex;
          flex-direction: column;
          gap: 14px;
          position: relative;
          /* Said here because some themes clip a card's contents, and the
             scene menu is deliberately taller than the card it belongs to. */
          overflow: visible;
        }
        .head { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .title {
          display: flex; align-items: center; gap: 12px;
          flex: 1 1 auto; min-width: 0;
          cursor: pointer; background: none; border: 0; padding: 0;
          color: inherit; font: inherit; text-align: left;
        }
        .title:focus-visible { outline: 2px solid var(--primary-color); }
        .name {
          font-size: 1.35rem; font-weight: 600; letter-spacing: -.01em;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          color: var(--primary-text-color);
        }
        .room-icon {
          --mdc-icon-size: 26px;
          color: var(--state-icon-color, var(--secondary-text-color));
        }
        /* Badges say why the room looks the way it does. They are not
           buttons: nothing here is a thing to press -- and since none of
           them is more of a thing to press than the others, none of them is
           coloured either. One of them used to be, and it read as the only
           live control in a row of decorations. */
        .badges { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
        .badge { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .countdown {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 3px 9px 3px 7px; border-radius: 999px;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          font-size: .78rem; font-variant-numeric: tabular-nums;
        }
        .countdown ha-icon { --mdc-icon-size: 15px; }

        button.round {
          flex: 0 0 auto; width: 42px; height: 42px; min-height: 0;
          border: 0; border-radius: 50%; cursor: pointer;
          display: inline-flex; align-items: center; justify-content: center;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          transition: background .15s ease, color .15s ease;
        }
        button.round:hover { filter: brightness(1.18); }
        button.round.adaptive {
          background: rgba(255, 199, 104, .16); color: var(--bl-warm);
        }
        button.round[aria-pressed="true"] {
          background: rgba(var(--rgb-primary-color, 3, 169, 244), .18);
          color: var(--primary-color);
        }

        /* The brightness bar: one thick track, filled. Fully rounded, like
           the pills under it -- a softly cornered rectangle among four
           lozenges was the thing that still looked borrowed. */
        .bar {
          position: relative; height: 46px; border-radius: 999px;
          background: var(--secondary-background-color);
          overflow: hidden; cursor: pointer; touch-action: none;
          outline: none;
        }
        .bar:focus-visible { box-shadow: 0 0 0 2px var(--primary-color); }
        .fill {
          position: absolute; inset: 0 auto 0 0;
          background: var(--bl-warm); border-radius: 999px;
          transition: width .18s ease;
        }
        .bar.dragging .fill { transition: none; }

        /* Shown while dragging and not otherwise. The length of the fill
           is the reading; a number sitting on top of it is either the wrong
           colour for the track or the wrong colour for the fill, and at rest
           it is answering a question nobody asked. */
        .bar .pct {
          position: absolute; inset: 0; display: flex; align-items: center;
          justify-content: flex-end; padding: 0 16px;
          font-size: .85rem; font-weight: 600;
          color: var(--primary-text-color);
          pointer-events: none; font-variant-numeric: tabular-nums;
          opacity: 0; transition: opacity .12s ease;
        }
        .bar.dragging .pct { opacity: .85; }

        .scenes { display: flex; align-items: center; gap: 8px; }
        .scenes bl-dropdown { flex: 1 1 auto; min-width: 0; }
        /* Wide, not round. Three pills across the row, the middle one
           widest -- a circle either side made them read as icon buttons
           rather than as the two ends of one control. */
        .step {
          flex: 0 0 22%; min-width: 58px; max-width: 110px;
          height: 42px; min-height: 0;
          border-radius: 999px; border: 1px solid var(--divider-color);
          background: none; color: var(--primary-text-color); cursor: pointer;
          display: inline-flex; align-items: center; justify-content: center;
        }
        .step:hover:not(:disabled) { background: var(--secondary-background-color); }
        .step:disabled { opacity: .4; cursor: default; }
        .missing { padding: 16px; color: var(--error-color, #db4437); }
        /* An explicit display beats the hidden attribute, so anything that
           hides itself has to say so louder than its own layout rule. */
        [hidden] { display: none !important; }
        @media (prefers-reduced-motion: reduce) {
          button.round, .step, .fill, .bar .pct { transition: none; }
        }
      </style>
      <ha-card>
        <div class="head">
          <button class="title" id="more">
            <span id="icon"></span>
            <span class="name" id="name"></span>
          </button>
          <div class="badges" id="badges"></div>
          <button class="round extra" id="extra" hidden></button>
          <button class="round adaptive" id="adaptive"></button>
          <button class="round power" id="power"></button>
        </div>

        <div class="bar" id="bar" role="slider" tabindex="0"
             aria-label="${this._words.brightness}"
             aria-valuemin="1" aria-valuemax="100">
          <div class="fill" id="fill"></div>
          <div class="pct" id="pct"></div>
        </div>

        <div class="scenes" id="scenes">
          <button class="step" id="prev" title="${this._words.previous_scene}"></button>
          <bl-dropdown id="picker"></bl-dropdown>
          <button class="step" id="next" title="${this._words.next_scene}"></button>
        </div>

      </ha-card>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    // The title is the way to colour and temperature: Home Assistant's own
    // dialog already does both well, and two more buttons on every room to
    // reach something used twice a year is a bad trade.
    $("more").addEventListener("click", () => this._openMoreInfo());
    $("power").addEventListener("click", () => this._toggle());
    $("extra").addEventListener("click", () => this._pressExtra());
    $("adaptive").addEventListener("click", () => this._backToAdaptive());
    $("prev").addEventListener("click", () => this._step(-1));
    $("next").addEventListener("click", () => this._step(1));
    $("picker").addEventListener("value-changed", (event) =>
      this._call("select", "select_option", {
        entity_id: this._selectId,
        option: event.detail.value,
      })
    );
    this._wireBar($("bar"));
  }

  /** Drag, click and arrow keys on the brightness bar. */
  _wireBar(bar) {
    const value = (event) => {
      const box = bar.getBoundingClientRect();
      const across = (event.clientX - box.left) / box.width;
      return Math.max(1, Math.min(100, Math.round(across * 100)));
    };
    const move = (event) => {
      this._pending = value(event);
      this._paintBar();
    };
    const up = (event) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      bar.classList.remove("dragging");
      this._setBrightness(value(event));
      this._pending = null;
    };
    bar.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      bar.classList.add("dragging");
      move(event);
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
    bar.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5 }[
        event.key
      ];
      if (step === undefined) return;
      event.preventDefault();
      this._setBrightness((this._shownPct() || 0) + step);
    });
  }

  _icon(name, className = "") {
    if (customElements.get(HA_ICON)) {
      return `<ha-icon class="${className}" icon="${name}"></ha-icon>`;
    }
    // A dot rather than nothing: the layout should not collapse because an
    // icon set is missing.
    return `<span class="${className}">•</span>`;
  }

  // -- keeping it in step with the room ------------------------------------

  _shownPct() {
    if (this._pending !== null) return this._pending;
    const attrs = this._light?.attributes || {};
    return this._light?.state === "on" && attrs.brightness
      ? Math.round((attrs.brightness / 255) * 100)
      : 0;
  }

  /**
   * Setting a brightness on a dark room lights it.
   *
   * Not a special case here, because it is not one in the room either: a
   * brightness on its own, on a room that is off, is an explicit request for
   * that level. The room takes the brightness axis as manually owned and
   * leaves colour to the sun, which is exactly "on, adaptive, but this
   * bright". So the bar is never disabled -- a dark room is the most likely
   * reason somebody reaches for it.
   */

  _sync() {
    const card = this.shadowRoot.querySelector("ha-card");
    if (!this._config.entity) {
      // Being drawn in the card picker before a room has been chosen.
      card.innerHTML = `<div class="missing">${this._words.choose_room}</div>`;
      this._drawn = false;
      return;
    }
    const light = this._light;
    if (!light) {
      card.innerHTML = `<div class="missing">${this._words.unknown_entity}: ${this._config.entity}</div>`;
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

    this._paintBadges(attrs, on);

    // Not a toggle: there is no such thing as turning adaptive off from here.
    // It is the way back once something else has taken the room over, so it
    // appears only when the room is lit *and* something else is driving it.
    // A dark room has nothing to come back from, which is why it used to sit
    // there on every room that was simply switched off.
    $("adaptive").hidden = !on || attrs.bl_adaptive !== false;
    $("adaptive").innerHTML = this._icon("mdi:white-balance-sunny");
    $("adaptive").title = this._words.back_to_adaptive;

    const power = $("power");
    power.innerHTML = this._icon("mdi:power");
    power.setAttribute("aria-pressed", String(on));
    power.title = on ? this._words.turn_off : this._words.turn_on;

    this._paintExtra();
    this._paintBar();
    this._paintScenes();
    this._watchCountdown(attrs.bl_off_at);
  }

  /**
   * The one button on this card that is not about the room.
   *
   * It carries no label, only an icon -- the header has a room name in it
   * already, and a word beside it would be the widest thing in the row. What
   * it is shows in its tooltip and in its state: lit when the entity is
   * doing something, plain when it is not.
   */
  _paintExtra() {
    const button = this.shadowRoot.getElementById("extra");
    const entityId = this._config.button_entity;
    const state = entityId ? this._hass?.states?.[entityId] : null;
    // Hidden rather than empty when there is nothing behind it: an unset
    // option should cost nothing, and an entity that has gone away should
    // not leave a button that does nothing when pressed.
    button.hidden = !state;
    if (!state) return;

    const domain = entityId.split(".")[0];
    button.innerHTML = this._icon(
      this._config.button_icon ||
        state.attributes.icon ||
        DOMAIN_ICONS[domain] ||
        "mdi:tune"
    );
    button.setAttribute(
      "aria-pressed",
      String(!RESTING.has(String(state.state).toLowerCase()))
    );
    button.title = state.attributes.friendly_name || entityId;
  }

  _paintBadges(attrs, on) {
    const badges = [];
    // Only when the room actually has a sensor: the attribute is null
    // otherwise, which is not the same as nobody being in the room.
    if (attrs.bl_presence === true) {
      badges.push(["mdi:motion-sensor", this._words.presence]);
    } else if (attrs.bl_presence === false) {
      badges.push(["mdi:motion-sensor-off", this._words.nobody]);
    }
    if (attrs.bl_night === true) {
      badges.push(["mdi:weather-night", this._words.night]);
    }
    if (attrs.bl_simulating === true) {
      badges.push(["mdi:home-clock", this._words.simulating]);
    }
    const left = this._secondsLeft(attrs.bl_off_at);
    // A countdown only runs when nobody is holding the lights on, so saying
    // "on automatically" beside it is the same fact twice -- and the room
    // name is what loses the space.
    if (on && left === null) {
      badges.push(
        attrs.bl_held_by_hand
          ? ["mdi:hand-back-right", this._words.by_hand]
          // Not a motion sensor: this says nobody reached for a switch, which
          // is true of a scene an automation set in a room with no sensor in
          // it. Drawn as one, it was read as presence detection -- and read
          // that way most often in the moment before `bl_held_by_hand` caught
          // up with a switch press, which is a flicker of exactly the thing
          // the room does not have.
          : ["mdi:auto-mode", this._words.automatically]
      );
    }
    const countdown =
      left === null
        ? ""
        : `<span class="countdown" title="${this._words.switching_off}">${this._icon(
            "mdi:timer-outline"
          )}${this._clock(left)}</span>`;
    this.shadowRoot.getElementById("badges").innerHTML =
      badges
        .map(
          ([icon, title]) =>
            `<span title="${title}">${this._icon(icon, "badge")}</span>`
        )
        .join("") + countdown;
  }

  _paintBar() {
    const bar = this.shadowRoot.getElementById("bar");
    const pct = this._shownPct();
    bar.setAttribute("aria-valuenow", String(pct));
    const fill = this.shadowRoot.getElementById("fill");
    // While dragging, the handle follows the finger even in a dark room --
    // that drag is how the room gets lit, so showing nothing until it is
    // over would be the one moment the control says least.
    fill.style.width = `${pct}%`;
    fill.style.background = this._fillColour();
    this.shadowRoot.getElementById("pct").textContent = `${pct}%`;
  }

  /**
   * What the room is actually giving off.
   *
   * A bar that is always the same amber is a bar that says nothing about a
   * room sitting in deep orange for a film. The group entity reports whatever
   * its members agree on, so this is the room's own light or, when they
   * disagree and it reports nothing, the default warmth.
   */
  _fillColour() {
    const attrs = this._light?.attributes || {};
    const rgb =
      attrs.rgb_color ||
      (attrs.color_temp_kelvin ? kelvinToRgb(attrs.color_temp_kelvin) : null);
    return rgb ? `rgb(${rgb.slice(0, 3).join(",")})` : "var(--bl-warm)";
  }

  _paintScenes() {
    const select = this._sceneSelect;
    const row = this.shadowRoot.getElementById("scenes");
    row.hidden = !select;
    if (!select) return;

    const options = this._options(select);
    const picker = this.shadowRoot.getElementById("picker");
    picker.options = options.map((option) => ({
      value: option,
      label: option,
      icon: this._optionIcon(select, option),
    }));
    // Named even when hidden: a scene left out of this card can still be the
    // one that is on, set from somewhere else.
    picker.current = {
      value: select.state,
      label: select.state,
      icon: this._optionIcon(select, select.state),
    };
    picker.value = select.state;

    this.shadowRoot.getElementById("prev").innerHTML =
      this._icon("mdi:chevron-left");
    this.shadowRoot.getElementById("next").innerHTML =
      this._icon("mdi:chevron-right");

    // Nothing to step through in a dark room: the buttons would light it at
    // whatever the next scene happens to be, which is not what an arrow next
    // to a scene name offers to do. The menu stays live -- picking a scene by
    // name is an explicit enough answer to turn a room on with.
    const on = this._light?.state === "on";
    const single = options.length < 2;
    this.shadowRoot.getElementById("prev").disabled = single || !on;
    this.shadowRoot.getElementById("next").disabled = single || !on;
  }

  /** The icon a scene is drawn with, as the room itself reports it. */
  _optionIcon(select, option) {
    return select?.attributes?.bl_option_icons?.[option] || "mdi:palette";
  }

  // -- the countdown -------------------------------------------------------

  _secondsLeft(when) {
    if (!when) return null;
    const left = Math.round((new Date(when).getTime() - Date.now()) / 1000);
    return left > 0 ? left : null;
  }

  _clock(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (minutes >= 60) {
      return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(
        2,
        "0"
      )}m`;
    }
    return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  }

  /**
   * Tick only while there is something to count.
   *
   * The state itself does not change every second -- the backend publishes
   * when the room is due to go off, once -- so the second hand is ours to
   * run, and ours to stop.
   */
  _watchCountdown(when) {
    if (this._secondsLeft(when) === null) {
      this._stopTicking();
      return;
    }
    if (this._tick) return;
    this._tick = window.setInterval(() => {
      const attrs = this._light?.attributes || {};
      if (this._secondsLeft(attrs.bl_off_at) === null) this._stopTicking();
      this._paintBadges(attrs, this._light?.state === "on");
    }, 1000);
  }

  _stopTicking() {
    if (this._tick) {
      window.clearInterval(this._tick);
      this._tick = null;
    }
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

  /**
   * One step along the scenes this card shows.
   *
   * Not `select_next`: that walks the entity's own list, hidden entries and
   * all. Stepping from a scene that is itself hidden starts at whichever end
   * the press was heading towards.
   */
  _step(direction) {
    const select = this._sceneSelect;
    const options = this._options(select);
    if (!this._selectId || !options.length) return;
    const at = options.indexOf(select.state);
    const next =
      at === -1
        ? direction > 0
          ? 0
          : options.length - 1
        : (at + direction + options.length) % options.length;
    this._call("select", "select_option", {
      entity_id: this._selectId,
      option: options[next],
    });
  }

  _pressExtra() {
    const entityId = this._config.button_entity;
    if (!entityId || !this._hass?.states?.[entityId]) return;
    const domain = entityId.split(".")[0];

    const press = PRESS[domain];
    if (press) {
      this._call(press[0], press[1], { entity_id: entityId });
      return;
    }
    if (TOGGLES.has(domain)) {
      this._call("homeassistant", "toggle", { entity_id: entityId });
      return;
    }
    this._openMoreInfo(entityId);
  }

  _openMoreInfo(entityId = this._config.entity) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      })
    );
  }
}

/**
 * The visual editor.
 *
 * The room first, answered with only the rooms this integration publishes --
 * a list of every light in the house would be a list of mostly wrong
 * answers, since the card reads attributes only a Better Lighting room has.
 * Then which of that room's scenes to leave out, which cannot be asked until
 * the first is answered and means nothing once it changes. Last, the extra
 * header button, which is about the house rather than the room and so is
 * asked of every entity there is.
 */
class BetterLightingCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { hidden_scenes: [], button_entity: "", button_icon: "", ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get _sceneOptions() {
    const entity = this._config?.entity;
    if (!entity) return [];
    const selectId = sceneSelectFor(this._hass, entity);
    return selectId
      ? this._hass.states[selectId]?.attributes?.options || []
      : [];
  }

  _emit(config) {
    this._config = config;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config },
        bubbles: true,
        composed: true,
      })
    );
  }

  _render() {
    if (!this._hass) return;
    const text = words(this._hass);

    if (!this._picker) {
      this.innerHTML = `<style>
          .bl-editor { display: flex; flex-direction: column; gap: 12px; }
          .bl-editor h4 { margin: 4px 0 0; font-size: .95rem; font-weight: 600; }
          .bl-editor .hint { color: var(--secondary-text-color); font-size: .8rem; }
          .bl-scenes { display: flex; flex-wrap: wrap; gap: 8px; }
          .bl-scenes label {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 6px 12px; border-radius: 999px;
            border: 1px solid var(--divider-color); cursor: pointer;
          }
        </style>
        <div class="bl-editor">
          <div id="bl-room"></div>
          <div id="bl-hidden"></div>
          <h4>${text.extra_button}</h4>
          <div class="hint">${text.extra_button_hint}</div>
          <div id="bl-button"></div>
          <div id="bl-button-icon"></div>
          <div class="hint">${text.extra_button_icon_hint}</div>
        </div>`;
      this._picker = document.createElement("ha-entity-picker");
      this._picker.allowCustomEntity = false;
      // A room, not a bulb. `includeDomains` narrows it to lights for the
      // frontends that ignore a filter function; the filter does the rest.
      this._picker.includeDomains = ["light"];
      this._picker.entityFilter = (state) =>
        state?.attributes?.bl_room_id !== undefined;
      this._picker.addEventListener("value-changed", (event) => {
        const entity = event.detail.value;
        if (entity === this._config?.entity) return;
        // A different room has different scenes, so the old choice is not a
        // choice about anything any more. Carrying it over would hide scenes
        // by name in a room that happens to share one.
        this._emit({ ...this._config, entity, hidden_scenes: [] });
        this._render();
      });
      this.querySelector("#bl-room").appendChild(this._picker);

      // Anything at all: what belongs beside a room's power button is a
      // question about the house, and narrowing it here would only be
      // guessing which half of the answers to throw away.
      this._button = document.createElement("ha-entity-picker");
      this._button.allowCustomEntity = false;
      this._button.addEventListener("value-changed", (event) => {
        const chosen = event.detail.value || "";
        if (chosen === this._config?.button_entity) return;
        this._emit({ ...this._config, button_entity: chosen });
      });
      this.querySelector("#bl-button").appendChild(this._button);

      // The icon is an override, so it starts empty and the entity's own is
      // used. A plain field is the fallback because `ha-icon-picker` is not
      // guaranteed to be defined on a dashboard that has never needed one.
      this._buttonIcon = customElements.get("ha-icon-picker")
        ? document.createElement("ha-icon-picker")
        : document.createElement("input");
      this._buttonIcon.addEventListener("value-changed", (event) =>
        this._emit({ ...this._config, button_icon: event.detail.value || "" })
      );
      this._buttonIcon.addEventListener("change", (event) => {
        if (event.detail) return;
        this._emit({ ...this._config, button_icon: event.target.value || "" });
      });
      this.querySelector("#bl-button-icon").appendChild(this._buttonIcon);
    }

    this._picker.hass = this._hass;
    this._picker.label = text.room;
    this._picker.value = this._config?.entity || "";

    this._button.hass = this._hass;
    this._button.label = text.extra_button;
    this._button.value = this._config?.button_entity || "";

    this._buttonIcon.hass = this._hass;
    this._buttonIcon.label = text.extra_button_icon;
    this._buttonIcon.placeholder = text.extra_button_icon;
    this._buttonIcon.value = this._config?.button_icon || "";

    this._renderHidden(text);
  }

  _renderHidden(text) {
    const into = this.querySelector("#bl-hidden");
    const options = this._sceneOptions;
    const signature = `${this._config?.entity || ""}|${options.join("\u0000")}|${(
      this._config?.hidden_scenes || []
    ).join("\u0000")}`;
    if (into.dataset.signature === signature) return;
    into.dataset.signature = signature;

    if (!this._config?.entity) {
      into.innerHTML = "";
      return;
    }
    if (!options.length) {
      // The room is chosen but its select has not arrived yet, or it has no
      // scenes at all. Either way there is nothing to tick.
      into.innerHTML = `<div class="hint">${text.no_scenes}</div>`;
      return;
    }

    const hidden = new Set(this._config.hidden_scenes || []);
    into.innerHTML = `<h4>${text.hidden_scenes}</h4>
      <div class="hint">${text.hidden_scenes_hint}</div>
      <div class="bl-scenes">${options
        .map(
          (option) =>
            `<label><input type="checkbox" value="${option}"${
              hidden.has(option) ? " checked" : ""
            }><span>${option}</span></label>`
        )
        .join("")}</div>`;

    into.querySelectorAll("input").forEach((box) =>
      box.addEventListener("change", () => {
        const chosen = [...into.querySelectorAll("input")]
          .filter((one) => one.checked)
          .map((one) => one.value);
        this._emit({ ...this._config, hidden_scenes: chosen });
      })
    );
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
