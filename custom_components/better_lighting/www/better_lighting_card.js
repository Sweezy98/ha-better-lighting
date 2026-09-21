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
  },
};

function words(hass) {
  const language = hass?.locale?.language || hass?.language || "en";
  return WORDS[language] || WORDS[language.split("-")[0]] || WORDS.en;
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
    this._menuOpen = false;
    this._tick = null;
    this._words = WORDS.en;
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
    this._closeMenu();
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
    return (
      Object.keys(hass.entities || {}).find(
        (id) =>
          id.startsWith(prefix) && hass.entities[id].device_id === mine.device_id
      ) || null
    );
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

        /* The brightness bar: one thick rounded track, filled. */
        .bar {
          position: relative; height: 46px; border-radius: 14px;
          background: var(--secondary-background-color);
          overflow: hidden; cursor: pointer; touch-action: none;
          outline: none;
        }
        .bar:focus-visible { box-shadow: 0 0 0 2px var(--primary-color); }
        .fill {
          position: absolute; inset: 0 auto 0 0;
          background: var(--bl-warm);
          transition: width .18s ease;
        }
        .bar.dragging .fill { transition: none; }
        .bar[aria-disabled="true"] { opacity: .5; cursor: default; }
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
        .step {
          flex: 0 0 auto; width: 42px; height: 42px; min-height: 0;
          border-radius: 999px; border: 1px solid var(--divider-color);
          background: none; color: var(--primary-text-color); cursor: pointer;
          display: inline-flex; align-items: center; justify-content: center;
        }
        .step:hover:not(:disabled) { background: var(--secondary-background-color); }
        .step:disabled { opacity: .4; cursor: default; }
        .picker {
          flex: 1 1 auto; min-width: 0; height: 42px;
          border-radius: 999px; border: 1px solid var(--divider-color);
          background: none; color: var(--primary-text-color); cursor: pointer;
          font: inherit; display: flex; align-items: center;
          /* Room on the right for the chevron, which was sitting hard against
             the edge of the pill. */
          padding: 0 16px 0 18px; gap: 10px;
        }
        .picker:hover { background: var(--secondary-background-color); }
        .picker .current {
          flex: 1 1 auto; min-width: 0; text-align: left;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .picker ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }

        /* The menu. A surface over the card rather than the browser's own
           list, which cannot be styled and looks like nothing else here. */
        /* Anchored to the scene row, and below it unless there is no room
           below -- which is the usual case for a card near the bottom of a
           dashboard, and was the only case before. */
        .menu {
          position: absolute; left: 14px; right: 14px;
          z-index: 3; border-radius: 16px;
          background: var(--card-background-color, #1c1c1c);
          box-shadow: 0 8px 28px rgba(0, 0, 0, .5);
          border: 1px solid var(--divider-color);
          overflow-y: auto; overscroll-behavior: contain; padding: 6px;
        }
        .menu button {
          display: flex; align-items: center; gap: 12px; width: 100%;
          padding: 11px 14px; border: 0; background: none; cursor: pointer;
          color: var(--primary-text-color); font: inherit; text-align: left;
          border-radius: 10px;
        }
        .menu button:hover, .menu button:focus-visible {
          background: var(--secondary-background-color); outline: none;
        }
        .menu button[aria-selected="true"] { color: var(--primary-color); }
        .menu ha-icon { --mdc-icon-size: 20px; flex: 0 0 auto; }
        /* Only for the card itself. A click anywhere else in Home Assistant
           is caught on the document, because a scrim the size of one card
           cannot cover the dashboard around it. */
        .scrim { position: absolute; inset: 0; z-index: 2; }

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
          <button class="picker" id="picker" aria-haspopup="listbox">
            <span class="current" id="current"></span>
            <span id="chevron"></span>
          </button>
          <button class="step" id="next" title="${this._words.next_scene}"></button>
        </div>

        <div class="scrim" id="scrim" hidden></div>
        <div class="menu" id="menu" role="listbox" hidden></div>
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
    $("picker").addEventListener("click", () => this._toggleMenu());
    $("scrim").addEventListener("click", () => this._closeMenu());
    this._wireBar($("bar"));
    this.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._menuOpen) {
        this._closeMenu();
        $("picker").focus();
      }
    });
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
      if (bar.getAttribute("aria-disabled") === "true") return;
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
    const on = this._light?.state === "on";
    const pct = this._shownPct();
    bar.setAttribute("aria-disabled", String(!on));
    bar.setAttribute("aria-valuenow", String(pct));
    const fill = this.shadowRoot.getElementById("fill");
    fill.style.width = `${on ? pct : 0}%`;
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

    const options = select.attributes.options || [];
    this.shadowRoot.getElementById("current").textContent = select.state;
    this.shadowRoot.getElementById("chevron").innerHTML =
      this._icon("mdi:chevron-down");
    this.shadowRoot.getElementById("prev").innerHTML =
      this._icon("mdi:chevron-left");
    this.shadowRoot.getElementById("next").innerHTML =
      this._icon("mdi:chevron-right");

    const single = options.length < 2;
    this.shadowRoot.getElementById("prev").disabled = single;
    this.shadowRoot.getElementById("next").disabled = single;
    this.shadowRoot.getElementById("picker").disabled = !options.length;

    if (this._menuOpen) this._paintMenu(options, select.state);
  }

  _paintMenu(options, current) {
    const menu = this.shadowRoot.getElementById("menu");
    menu.innerHTML = options
      .map(
        (option) => `<button role="option" data-option="${option}"
           aria-selected="${option === current}">
           ${this._icon(
             option === current ? "mdi:check" : "mdi:blank"
           )}<span>${option}</span></button>`
      )
      .join("");
    menu.querySelectorAll("button").forEach((row) =>
      row.addEventListener("click", () => {
        this._closeMenu();
        this._call("select", "select_option", {
          entity_id: this._selectId,
          option: row.dataset.option,
        });
      })
    );
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

  _step(service) {
    if (!this._selectId) return;
    this._call("select", service, { entity_id: this._selectId });
  }

  _toggleMenu() {
    if (this._menuOpen) {
      this._closeMenu();
    } else {
      this._openMenu();
    }
  }

  _openMenu() {
    const select = this._sceneSelect;
    if (!select) return;
    this._menuOpen = true;
    const options = select.attributes.options || [];
    this._paintMenu(options, select.state);

    const menu = this.shadowRoot.getElementById("menu");
    menu.hidden = false;
    this.shadowRoot.getElementById("scrim").hidden = false;
    this._placeMenu(menu, options.length);

    // Anywhere else in Home Assistant, not just anywhere else on this card.
    this._outside = (event) => {
      if (!event.composedPath().includes(this)) this._closeMenu();
    };
    document.addEventListener("pointerdown", this._outside, true);
    menu.querySelector('[aria-selected="true"], button')?.focus();
  }

  /**
   * Below the scene row, unless there is not room for it there.
   *
   * Measured against the window rather than the card: a card near the bottom
   * of a dashboard has plenty of room inside itself and none underneath, and
   * opening downwards into the edge of the screen is how a menu ends up with
   * two of its five entries reachable.
   */
  _placeMenu(menu, count) {
    const row = this.shadowRoot.getElementById("scenes").getBoundingClientRect();
    const below = window.innerHeight - row.bottom - 16;
    const above = row.top - 16;
    // Measured rather than assumed: a row's height follows the theme's font,
    // and five rows of a guess is four and a half rows on somebody's screen.
    const first = menu.querySelector("button");
    const rowHeight = first ? first.getBoundingClientRect().height : 44;
    const wanted = Math.min(count, MENU_ROWS) * rowHeight + 12;

    const downwards = below >= Math.min(wanted, above) || below >= wanted;
    const room = Math.max(120, downwards ? below : above);
    menu.style.maxHeight = `${Math.min(wanted, room)}px`;

    const card = this.getBoundingClientRect();
    if (downwards) {
      menu.style.top = `${row.bottom - card.top + 8}px`;
      menu.style.bottom = "auto";
    } else {
      menu.style.bottom = `${card.bottom - row.top + 8}px`;
      menu.style.top = "auto";
    }
  }

  _closeMenu() {
    this._menuOpen = false;
    if (this._outside) {
      document.removeEventListener("pointerdown", this._outside, true);
      this._outside = null;
    }
    const menu = this.shadowRoot?.getElementById("menu");
    const scrim = this.shadowRoot?.getElementById("scrim");
    if (menu) menu.hidden = true;
    if (scrim) scrim.hidden = true;
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
      this._picker.label = words(this._hass).room;
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
