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
    switching_off: "Switching off",
    room: "Room",
    hidden_scenes: "Hidden scenes",
    hidden_scenes_hint: "Ticked scenes are left out of this card's menu and skipped by the arrows.",
    no_scenes: "This room has no scenes yet.",
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
    switching_off: "Schaltet ab",
    room: "Raum",
    hidden_scenes: "Ausgeblendete Szenen",
    hidden_scenes_hint: "Angehakte Szenen fehlen im Menü dieser Karte und werden von den Pfeilen übersprungen.",
    no_scenes: "Dieser Raum hat noch keine Szenen.",
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
        /* The icon column sits on the same line as the trigger's: one border
           and this padding come off the trigger's own. */
        .menu button {
          display: grid; grid-template-columns: var(--bl-gutter) 1fr;
          align-items: center; width: 100%; box-sizing: border-box;
          padding: 11px 12px 11px calc(var(--bl-pad) - 6px);
          border: 0; background: none; cursor: pointer; border-radius: 10px;
          color: var(--primary-text-color); font: inherit; text-align: left;
        }
        .menu button:hover, .menu button:focus-visible {
          background: var(--secondary-background-color); outline: none;
        }
        .menu button[aria-selected="true"] { color: var(--primary-color); }
        /* Always there, even for an option with no icon: the column has to
           be occupied or the label slides into it and gets the icon's width
           -- which is how a list of modes came out as "p." */
        .menu button .ico { display: inline-flex; justify-content: flex-start; }
        .menu button .text {
          min-width: 0; padding: 0 8px; text-align: left;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
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
    menu.innerHTML = this._options
      .map(
        (option) => `<button role="option" data-value="${option.value}"
           aria-selected="${option.value === this._value}">
           <span class="ico">${this._icon(
             // The tick takes the icon's place on the current row: which one
             // is chosen is already said by the icon on the trigger.
             option.value === this._value ? "mdi:check" : option.icon
           )}</span><span class="text">${option.label}</span></button>`
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
   * The height is rows plus the menu's own padding and border, measured from
   * the element rather than assumed -- two pixels short of the truth is a
   * scrollbar for the sake of the last row's bottom edge, which is exactly
   * what a guessed constant produced.
   */
  _place(menu) {
    const style = getComputedStyle(menu);
    const chrome =
      parseFloat(style.paddingTop) +
      parseFloat(style.paddingBottom) +
      parseFloat(style.borderTopWidth) +
      parseFloat(style.borderBottomWidth);

    const first = menu.querySelector("button");
    const rowHeight = first ? first.getBoundingClientRect().height : 44;
    const box = this.getBoundingClientRect();
    const below = window.innerHeight - box.bottom - 16;
    const above = box.top - 16;

    const fits = (room) =>
      Math.max(1, Math.floor((room - chrome) / rowHeight));
    const downwards = below >= above || fits(below) >= this._options.length;
    const room = downwards ? below : above;
    const rows = Math.min(
      this._options.length,
      BlDropdown.ROWS,
      Math.max(1, fits(room))
    );

    const height = rows * rowHeight + chrome;
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
    this._config = { name: null, hidden_scenes: [], ...config, entity };
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
           buttons: nothing here is a thing to press. */
        .badges { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
        .badge { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .badge.on { color: var(--primary-color); }
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
        button.round.power[aria-pressed="true"] {
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

    this._paintBar();
    this._paintScenes();
    this._watchCountdown(attrs.bl_off_at);
  }

  _paintBadges(attrs, on) {
    const badges = [];
    if (attrs.bl_presence === true) {
      badges.push(["mdi:motion-sensor", "on", this._words.presence]);
    } else if (attrs.bl_presence === false) {
      badges.push(["mdi:motion-sensor-off", "", this._words.nobody]);
    }
    const left = this._secondsLeft(attrs.bl_off_at);
    // A countdown only runs when nobody is holding the lights on, so saying
    // "on automatically" beside it is the same fact twice -- and the room
    // name is what loses the space.
    if (on && left === null) {
      badges.push(
        attrs.bl_held_by_hand
          ? ["mdi:hand-back-right", "on", this._words.by_hand]
          : ["mdi:motion-sensor", "", this._words.automatically]
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
          ([icon, cls, title]) =>
            `<span title="${title}">${this._icon(icon, `badge ${cls}`)}</span>`
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
 * Two questions. The room, answered with only the rooms this integration
 * publishes -- a list of every light in the house would be a list of mostly
 * wrong answers, since the card reads attributes only a Better Lighting room
 * has. Then which of that room's scenes to leave out, which cannot be asked
 * until the first is answered and means nothing once it changes.
 */
class BetterLightingCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { hidden_scenes: [], ...config };
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
    }

    this._picker.hass = this._hass;
    this._picker.label = text.room;
    this._picker.value = this._config?.entity || "";
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
