/* CTC Local Easyconf: the page's cards, with an explanation for every value.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-card.js and loaded as a
 * Lovelace resource. The page itself is built in Python (dashboard_views.py) and uses
 * four cards from here, all of them drawn by hand rather than out of Home Assistant's
 * own tiles, because a heat pump has a hundred values and they have to fit on a screen:
 *
 *   custom:ctc-ecozenith-chips      what the pump is doing right now, a line of chips
 *   custom:ctc-ecozenith-readings   the key figures, a small name over a big number
 *   custom:ctc-ecozenith-controls   one row per control: name on the left, the thing
 *                                   it is on the right, a list, a slider, a field,
 *                                   a button or, for a switch, a tick box
 *   custom:ctc-ecozenith-rows       a list of values, name on the left and value on
 *                                   the right, in as many columns as there is room
 *                                   for, with headings and a filter over the lot
 *
 * Every value carries a blue "i" right after its name. It writes the explanation out
 * underneath, together with where the value comes from and a way into Home Assistant's
 * own dialog for the entity, which is what the NIBE page settled on. The "i" is the
 * one toggle for its note (R41): a button, with aria-expanded and aria-controls
 * pointing at the note, and no hover text on the name, which made a screen reader
 * read every explanation twice. In the list a click on the row is passed on to it,
 * and a row without an explanation, and so without an "i", opens Home Assistant's
 * dialog instead. Every string goes into the page as text, never as HTML.
 *
 * The pure helpers at the top of the closure are what decides a write, which
 * control a number gets and what a typed field is taken to say, and what a state
 * reads as when the page has a word of its own for it. They are loaded into node
 * by tests/test_card*.py, so the closure hands them over and stops before the
 * custom elements when there is no browser. Nothing is a global in either place:
 * the NIBE card declares the same names at its top level, and the two may well be
 * loaded on one page.
 */

(() => {
  const HIDDEN_STATES = new Set(["unavailable", "unknown"]);
  //: A number control with more steps than a slider has pixels is a lottery to
  //: aim at, so it gets a field to type in instead: a room setpoint in tenths
  //: of a degree from 10 to 30 is 200 steps on 130 pixels.
  const SLIDER_STEPS = 130;
  //: R47: how long a list or a slider waits for the next change before it
  //: writes. A closed list fires change at every arrow key, and so does a
  //: slider, so stepping through them wrote every mode on the way, Manuell
  //: and Av included, each a Modbus write with six seconds of busy mark. A
  //: change now waits this long and a newer one takes its place, so only
  //: where the user stops goes to the pump.
  const DEBOUNCE_MS = 400;

  /* ---------------------------------------------------------- pure helpers */

  function withUnit(value, unit) {
    return unit ? `${value} ${unit}` : String(value);
  }

  /** Which control a number gets: a slider when its range has few enough steps
   *  to aim at, a field to type in otherwise. Asked from the entity's own min,
   *  max and step, so it has to wait for the first state: setConfig has no hass
   *  yet, and asking there made everything a field. */
  function widgetFor(min, max, step) {
    const size = Number(step) || 1;
    const steps = (Number(max) - Number(min)) / size;
    // NaN fails both comparisons, so a number without a range gets a field.
    return steps > 0 && steps <= SLIDER_STEPS ? "slider" : "field";
  }

  /** What a typed field holds: a finite number, or null when it holds nothing
   *  a pump should be sent. Number("") is 0, and for the compressor's top speed
   *  0 is a legal value that leaves the house cold until someone presses
   *  Release, so an emptied field must never turn into a write. */
  function parseFieldValue(text) {
    if (text === null || text === undefined) return null;
    const trimmed = String(text).trim();
    if (trimmed === "") return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
  }

  //: The domains a control lives in. One of these in "unknown" is a control
  //: without a value yet, not a reading that is missing.
  const CONTROL_DOMAINS = new Set(["number", "select"]);

  /** The page's own word for a state, or undefined when Home Assistant's own
   *  wording is the right one. The words come out of the table the page sends
   *  as `states`, in the language the page is built in. A binary sensor's on
   *  and off are looked up by device class first ("problem_on") and then
   *  plainly ("on"): Home Assistant would word them in the user's language
   *  instead, and put "Not running" and "Off" beside Swedish labels. A control
   *  in "unknown" has no value yet (the hot water setpoint and extra hot
   *  water have no mirror register, so they are unknown until something is
   *  written) and says so
   *  rather than "Unknown". A 0 the pump means as no limit at all is a word
   *  too, for the keys the page marks with `zeroMeans`, and only for the
   *  pump's own value: a 0 somebody wrote through the control is a 0. An
   *  unknown that means nothing has happened yet (the last alarm before a
   *  panel has shown one) is a word as well, for the items the page marks
   *  with `unknownMeans`, so the full list does not read "Okänd" for months.
   *  An enum sensor's state is the integration's own string already and is
   *  left to Home Assistant, as is everything the table has no word for, so
   *  an older page without the table reads as it did. */
  function ownWord(entityId, stateObj, states, zeroMeans, unknownMeans) {
    if (!stateObj || !states) return undefined;
    const domain = String(entityId || "").split(".")[0];
    const state = stateObj.state;
    const attributes = stateObj.attributes || {};
    if (domain === "binary_sensor" && (state === "on" || state === "off")) {
      const deviceClass = attributes.device_class;
      return (deviceClass && states[`${deviceClass}_${state}`]) || states[state];
    }
    if (CONTROL_DOMAINS.has(domain) && state === "unknown") {
      return (unread(attributes) && states.unread) || states.unset;
    }
    if (unknownMeans && state === "unknown") return states[unknownMeans];
    if (zeroMeans && parseFieldValue(state) === 0 && !controlActive(attributes)) {
      return states[zeroMeans];
    }
    return undefined;
  }

  /** Whether an override written from Home Assistant is in force on a control.
   *  The integration says it under the English `control_active`, a boolean,
   *  and under the Swedish `styrning aktiv`, "ja" or "nej", which goes in a
   *  later major version; an older integration sends only the Swedish one. */
  function controlActive(attributes) {
    const english = attributes.control_active;
    if (english !== undefined && english !== null) return english === true;
    return attributes["styrning aktiv"] === "ja";
  }

  /** R49: the pump's own value beside an override, where the integration
   *  gives it: under the English `pump_value`, or the Swedish `pumpens värde`
   *  an older integration may send. undefined where there is none. */
  function pumpValue(attributes) {
    for (const name of ["pump_value", "pumpens värde"]) {
      const value = attributes[name];
      if (value !== undefined && value !== null && String(value) !== "") return value;
    }
    return undefined;
  }

  /** F2.1, F2.3: whether a control's own value could not be read just now.
   *  A number or list with a mirror carries pump_value also when the mirror
   *  did not answer, empty, and stays available through a lost Modbus round
   *  with the state unknown, while one without a mirror, the hot water
   *  setpoint or extra hot water, carries no pump_value at all and is unknown
   *  until something is written. The two read "not read" and "not set", each
   *  with its own note. */
  function unread(attributes) {
    const carried = ["pump_value", "pumpens värde"].some(
      (name) => Object.prototype.hasOwnProperty.call(attributes, name));
    return carried && pumpValue(attributes) === undefined;
  }

  /** Whether two values say the same: as numbers where both are numbers, so
   *  the 50 an attribute carries is the "50.0" of a state, else as text. */
  function sameValue(a, b) {
    const x = parseFieldValue(a);
    const y = parseFieldValue(b);
    return x !== null && y !== null ? x === y : String(a) === String(b);
  }

  /** Whether an entity says if Home Assistant holds it at all: the
   *  integration's controls do, under either name, and a list of anything
   *  else does not. */
  function saysHeld(attributes) {
    return ["control_active", "styrning aktiv"].some(
      (name) => attributes[name] !== undefined && attributes[name] !== null
    );
  }

  /** What a mode list shows for a state (R49). While Home Assistant holds a
   *  mode it is the state; at rest it is the list's first option, Släpp
   *  styrningen, whatever the state says. The hot water program's state at
   *  rest is the pump's own mode out of 61500, and a list standing on it
   *  could not pick it: choosing the option a list already shows fires no
   *  change, so the mode the pump runs could never be held from the card,
   *  and after Släpp styrningen the state was a mode the list did not show,
   *  so the row stayed busy for six seconds. The pump's own mode is said
   *  under the row instead (pumpen själv). A state of unknown, the mirror
   *  unread, is at rest too. A list of another integration's, which says
   *  nothing about being held, shows its state as any list does. */
  function listValue(stateObj) {
    if (!stateObj) return "";
    const attributes = stateObj.attributes || {};
    const options = attributes.options || [];
    if (!options.length || !saysHeld(attributes) || controlActive(attributes)) {
      return stateObj.state;
    }
    return String(options[0]);
  }

  /** Whether a list or a slider stopped where it already stands at rest, so
   *  that sending would ask for nothing new and only start a hold: down and
   *  up again within the wait. A list then stands on what listValue shows at
   *  rest, a slider on the state's own number. Nothing held is ever passed
   *  over here: a held value is sent as it always was. */
  function unchangedAtRest(entityId, value, stateObj) {
    if (!stateObj) return false;
    const attributes = stateObj.attributes || {};
    if (controlActive(attributes)) return false;
    if (String(entityId).split(".")[0] === "select") return String(value) === listValue(stateObj);
    const number = parseFieldValue(value);
    return number !== null && number === parseFieldValue(stateObj.state);
  }

  /** R23, R28: the line under a figure in the page's language. What a
   *  coefficient of performance or a share of auxiliary heat rests on, and why
   *  it has no figure, are attributes whose values stay Swedish for automations
   *  (underlag and skäl, beside basis and reason), so the page sends what they
   *  say on it as `story` (dashboard_views.story_words): `words`, a whole line
   *  to the page's words, and `patterns`, each a regular expression for a line
   *  with a figure or a date in it and the page's words with $1, $2 for what
   *  it caught, first match wins. A line neither knows is shown as it is, and
   *  a Swedish page, or an older one, sends no table at all. The patterns are
   *  read once per table, not at every state Home Assistant sends. */
  const STORY_PATTERNS = new WeakMap();
  function storyLine(text, story) {
    if (!text || !story || typeof story !== "object") return text;
    const words = story.words || {};
    if (Object.prototype.hasOwnProperty.call(words, text)) return String(words[text]);
    let patterns = STORY_PATTERNS.get(story);
    if (!patterns) {
      patterns = [];
      for (const pattern of Array.isArray(story.patterns) ? story.patterns : []) {
        // An empty pattern would match every line, so only a written one counts.
        if (!pattern || typeof pattern.match !== "string" || !pattern.match) continue;
        try {
          patterns.push([new RegExp(pattern.match), String(pattern.text)]);
        } catch (err) {
          // A pattern this browser cannot read is passed over.
        }
      }
      STORY_PATTERNS.set(story, patterns);
    }
    for (const [found, said] of patterns) {
      if (found.test(text)) return text.replace(found, said);
    }
    return text;
  }

  if (typeof customElements === "undefined") {
    // Node, for tests/test_card*.py: hand over the helpers and stop here, before
    // anything below reaches for HTMLElement or document. A browser never takes
    // this branch, so the page gets no globals from it.
    if (typeof module !== "undefined") {
      module.exports = { widgetFor, parseFieldValue, ownWord, SLIDER_STEPS };
    }
    return;
  }

  /* ------------------------------------------------------------------ DOM */

  const STYLE = `
    /* A display rule of a card, or ha-card's own :host rule, would otherwise beat
       the browser's [hidden] and leave closed explanations on the page. */
    [hidden] { display: none !important; }
    /* R40: no color-scheme of the card's own. Home Assistant carries its
       theme's mode into the card through meta[name=color-scheme], and a list's
       pop-up, a number field's arrows and the search field's clear button
       follow it; a value on :host would write over it. */
    :host { display: block; }
    /* R40: tabular figures wherever a number stands, so a value in the list
       does not move sideways when 9.8 becomes 10.2: the list's values and its
       count, the key figures, and the reading beside a slider. */
    .value, .big, .reading, .count { font-variant-numeric: tabular-nums; }
    ha-card { padding: 12px 16px; }
    .why {
      flex: none; background: none; border: none; padding: 0 2px; cursor: pointer;
      color: var(--primary-color, #03a9f4); font: inherit; line-height: 1;
    }
    /* R41: a focus ring that can be seen, 2 px like the NIBE card's tabs. */
    .why:focus-visible {
      outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; border-radius: 50%;
    }
    .note {
      color: var(--secondary-text-color); font-size: .85em; line-height: 1.45; padding-top: 4px;
    }
    .note .meta { display: block; margin-top: 4px; font-size: .9em; opacity: .85; }
    .note a { color: var(--primary-color); cursor: pointer; text-decoration: underline; }
    .note a:focus-visible {
      outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; border-radius: 2px;
    }
    /* R49: a control Home Assistant holds right now, in the theme's primary
       colour, with what holding means as its hover text, and the pump's own
       value in small text under the row. */
    .held {
      display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 999px;
      border: 1px solid var(--primary-color, #03a9f4); color: var(--primary-color, #03a9f4);
      font-size: .7em; font-weight: 500; line-height: 1.5; vertical-align: middle; cursor: help;
    }
    .pump {
      grid-column: 1 / -1; color: var(--secondary-text-color); font-size: .75em; line-height: 1.3;
    }
  `;

  function moreInfo(element, entityId) {
    element.dispatchEvent(
      new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } })
    );
  }

  //: R41: the buttons that open each note, so that every one of them can say
  //: whether it is open. A control's note has two: the "i" after its name and
  //: the one beside a control without a value.
  const TOGGLES = new WeakMap();
  //: How many notes have been given an id, for the next one's.
  let noteIds = 0;

  /** The blue "i" that opens an explanation. Every value the page shows has one:
   *  a value is only worth reading if you know what it is. It is the one
   *  toggle for its note (R41), and says so: aria-controls names the note,
   *  which gets an id of its own here, and aria-expanded follows it. Its
   *  name is the word for explanation and the value's name, "Förklaring:
   *  Utetemperatur": since the row is no button, the "i" is the one tab stop
   *  per value, and a hundred of them all called "Förklaring" told a screen
   *  reader user nothing about which value each one was for. */
  function explainButton(label, note, toggle, name) {
    const why = document.createElement("button");
    why.className = "why";
    why.type = "button";
    why.textContent = "ⓘ";
    const word = label || "Explanation";
    why.setAttribute("aria-label", name ? `${word}: ${name}` : word);
    if (!note.id) note.id = `ctc-ecozenith-note-${++noteIds}`;
    why.setAttribute("aria-controls", note.id);
    why.setAttribute("aria-expanded", String(!note.hidden));
    TOGGLES.set(note, [...(TOGGLES.get(note) || []), why]);
    why.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      toggle();
    });
    return why;
  }

  /** Whether the user is holding this very control. The cards live in a shadow
   *  root, where the document's own idea of what has focus is the card itself,
   *  so the question has to be put to the root the control is in. */
  function holding(element) {
    const root = element.getRootNode();
    return Boolean(root) && root.activeElement === element;
  }

  //: R39: what a list remembers for the browser tab, under this prefix and
  //: the list's own key: what was typed in its search and whether its empty
  //: rows are shown.
  const KEEP_PREFIX = "ctc-ecozenith-rows:";

  /** What was kept under `key` for this tab, or null. sessionStorage may be
   *  missing or refuse (a private window, storage switched off, a sandboxed
   *  frame), and every touch of it is in a try: the list then starts afresh,
   *  as it always did. */
  function recall(key) {
    try {
      const raw = globalThis.sessionStorage.getItem(key);
      const kept = raw ? JSON.parse(raw) : null;
      return kept && typeof kept === "object" ? kept : null;
    } catch (err) {
      return null;
    }
  }

  /** Keep `value` under `key` for this tab, if the browser lets us. */
  function keep(key, value) {
    try {
      globalThis.sessionStorage.setItem(key, JSON.stringify(value));
    } catch (err) {
      // Nowhere to keep it: the list works on and forgets on the next build.
    }
  }

  /** What the cards have in common: the explanation, the state, the service call. */
  class CtcCard extends HTMLElement {
    constructor(style) {
      super();
      this.attachShadow({ mode: "open" });
      this._open = new Set();
      this._style = style;
    }

    setConfig(config) {
      this._config = config || {};
      this._build();
      if (this._hass) this._update();
    }

    set hass(hass) {
      this._hass = hass;
      this._update();
    }

    get hass() {
      return this._hass;
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    _shell() {
      const style = document.createElement("style");
      style.textContent = `${STYLE}${this._style || ""}`;
      const card = document.createElement("ha-card");
      this.shadowRoot.replaceChildren(style, card);
      return card;
    }

    /** The name, the "i" after it, and the note the "i" opens. The name has
     *  no hover text: the "i" is the way in (R41). */
    _named(item, note, tag = "span") {
      const name = document.createElement(tag);
      name.className = "label";
      name.append(item.name || item.entity);
      if (item.explanation) {
        name.appendChild(explainButton(
          this._config.explain, note, () => this._toggle(item, note), item.name || item.entity
        ));
      }
      return name;
    }

    /** Open the note or close it, from what the note itself shows: what is
     *  open is kept per entity and outlives a new setConfig on the same
     *  element, which draws its notes anew and closed, so asking that first
     *  made the next click close a note already closed. */
    _toggle(item, note) {
      const open = Boolean(note.hidden);
      if (open) this._open.add(item.entity);
      else this._open.delete(item.entity);
      this._fill(note, item, open);
    }

    /** Close a note whose value has left the screen, so that it is closed,
     *  and says so, when the value comes back. */
    _shut(item, note) {
      if (note.hidden && !this._open.has(item.entity)) return;
      this._open.delete(item.entity);
      this._fill(note, item, false);
    }

    /** The explanation, then where the value comes from and a way to its dialog. */
    _fill(note, item, open) {
      note.hidden = !open;
      for (const why of TOGGLES.get(note) || []) why.setAttribute("aria-expanded", String(open));
      if (!open) return;
      note.replaceChildren();
      this._lead(note, item);
      note.append(item.explanation || "");
      this._more(note, item);
      const meta = document.createElement("span");
      meta.className = "meta";
      if (item.source) meta.append(item.source, " · ");
      const link = document.createElement("a");
      link.textContent = this._config.more_info || "More info";
      link.setAttribute("role", "button");
      link.tabIndex = 0;
      const openDialog = (event) => {
        event.preventDefault();
        event.stopPropagation();
        moreInfo(this, item.entity);
      };
      link.addEventListener("click", openDialog);
      link.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") openDialog(event);
      });
      meta.appendChild(link);
      note.appendChild(meta);
    }

    /** What a card puts before the explanation: nothing, unless its notes
     *  stand apart from their values. */
    _lead(note, item) {}  // eslint-disable-line no-unused-vars

    /** What a card adds to a note between the explanation and the source:
     *  nothing, unless the card has something to say about this very value. */
    _more(note, item) {}  // eslint-disable-line no-unused-vars

    _state(entityId) {
      return this._hass ? this._hass.states[entityId] : undefined;
    }

    /** What a value reads as: the page's own word where it has one, else what
     *  Home Assistant makes of the state, else the state with its unit. */
    _text(item, stateObj) {
      if (!stateObj) return "";
      const own = ownWord(item.entity, stateObj, this._config.states, item.zero_means,
                          item.unknown_means);
      if (own !== undefined) return own;
      try {
        return this._hass.formatEntityState
          ? this._hass.formatEntityState(stateObj)
          : withUnit(stateObj.state, stateObj.attributes.unit_of_measurement);
      } catch (err) {
        return stateObj.state;
      }
    }

    _missing(item) {
      const stateObj = this._state(item.entity);
      if (!stateObj) return Boolean(item.hide_unavailable || item.show_reason);
      // R35: a value that can say why it is empty stays while merely unknown.
      if (item.show_reason) return stateObj.state === "unavailable";
      return Boolean(item.hide_unavailable) && HIDDEN_STATES.has(stateObj.state);
    }

    /** R35: the small line under a value: what the figure rests on, or, while
     *  there is no figure, why there is none. Both are attributes of the
     *  entity, named in the item as one name or a list of names, so an
     *  attribute the integration renames is still found by the page. The
     *  attribute's Swedish words are put in the page's language (R23) where
     *  the page sent a table for them under `story`. */
    _below(item, stateObj) {
      if (!stateObj || (!item.sub && !item.reason)) return "";
      const attributes = stateObj.attributes || {};
      const first = (names) => {
        for (const name of [].concat(names || [])) {
          const found = attributes[name];
          if (found !== undefined && found !== null && String(found) !== "") return String(found);
        }
        return "";
      };
      const line = HIDDEN_STATES.has(stateObj.state)
        ? first(item.reason) || first(item.sub)
        : first(item.sub);
      return storyLine(line, this._config.story);
    }

    /** R49: the mark that says Home Assistant holds this control, and the
     *  line for the pump's own value, for a number or a list; null for
     *  anything else, which nothing can hold. Both start hidden. */
    _heldParts(item) {
      if (!CONTROL_DOMAINS.has(String(item.entity).split(".")[0])) return null;
      const states = this._config.states || {};
      const mark = document.createElement("span");
      mark.className = "held";
      mark.textContent = states.controlled || "Overridden";
      // The keepalive in one sentence: what holding means, and how it ends.
      if (states.controlled_note) mark.title = states.controlled_note;
      mark.hidden = true;
      const pump = document.createElement("div");
      pump.className = "pump";
      pump.hidden = true;
      return { mark, pump };
    }

    /** R49: show the mark while Home Assistant holds the control
     *  (control_active, or "styrning aktiv" from an older integration), and
     *  the pump's own value where the integration gives it, while held or
     *  wherever it is not what the control shows. `shows` is what the control
     *  shows, the state unless given: a mode list at rest shows Släpp
     *  styrningen, and the pump's own mode goes under it. Answers whether it
     *  is held. */
    _showHeld(parts, item, stateObj, shows) {
      if (!parts) return false;
      const attributes = (stateObj && stateObj.attributes) || {};
      const held = Boolean(stateObj) && controlActive(attributes);
      parts.mark.hidden = !held;
      const own = stateObj ? pumpValue(attributes) : undefined;
      const value = shows === undefined && stateObj ? stateObj.state : shows;
      const shown = own !== undefined && (held || !sameValue(own, value));
      const states = this._config.states || {};
      parts.pump.textContent = shown
        ? `${states.pump_value || "pump's own"}: ${this._ownText(item, own, attributes)}`
        : "";
      parts.pump.hidden = !shown;
      return held;
    }

    /** The pump's own value as the row would word it: its 0 that means no
     *  limit as the word for that, a number with the control's unit, a mode
     *  as it is. */
    _ownText(item, value, attributes) {
      const states = this._config.states || {};
      const number = parseFieldValue(value);
      if (item.zero_means && number === 0 && states[item.zero_means]) return states[item.zero_means];
      return number !== null ? withUnit(value, attributes.unit_of_measurement) : String(value);
    }

    /** The service call for a control, returned so the caller can see it
     *  refused: Home Assistant answers a value outside min and max, or a write
     *  the pump would not take, with a rejected promise and a toast of its own. */
    _call(entityId, value) {
      const domain = String(entityId).split(".")[0];
      if (domain === "select") {
        return this._hass.callService("select", "select_option", { entity_id: entityId, option: value });
      }
      if (domain === "number") {
        return this._hass.callService("number", "set_value", { entity_id: entityId, value: Number(value) });
      }
      if (domain === "button") {
        return this._hass.callService("button", "press", { entity_id: entityId });
      }
      if (domain === "switch") {
        return this._hass.callService("switch", value ? "turn_on" : "turn_off", { entity_id: entityId });
      }
      return undefined;
    }
  }

  /* ------------------------------------------------------------------ chips */

  const CHIPS_STYLE = `
    .chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .chip {
      display: flex; gap: 6px; align-items: baseline; padding: 6px 12px; border-radius: 999px;
      background: var(--secondary-background-color, #f1f3f4);
    }
    .chip .label { color: var(--secondary-text-color); font-size: .8em; }
    .chip .value { color: var(--primary-text-color); font-weight: 500; }
    /* R41: a note per chip, under the line in the chips' order, each led by
       its chip's name, since two can be open at once. */
    .notes .note { padding-top: 8px; }
    .notes .lead { color: var(--primary-text-color); font-weight: 500; }
    /* R36: a boolean chip is only on the line while it is on, in the theme's
       colour for what it means: an alarm red, a block or SmartGrid amber, a
       defrost blue, a running compressor or heater green. The alarm chip is a
       solid red pill with white text, the one thing on the page that must not
       be missed; the others are a tint of their colour with a dot in it, which
       is how Home Assistant's own tiles carry a state colour, and they stay
       legible in both themes. Without color-mix every one is a solid pill. */
    .chip[data-color="error"] { --chip-color: var(--error-color, #db4437); }
    .chip[data-color="warning"] { --chip-color: var(--warning-color, #ffa600); }
    .chip[data-color="info"] { --chip-color: var(--info-color, #039be5); }
    .chip[data-color="success"] { --chip-color: var(--success-color, #43a047); }
    .chip[data-color] { background: var(--chip-color); }
    .chip[data-color] .label, .chip[data-color] .value, .chip[data-color] .why {
      color: var(--text-primary-color, #fff);
    }
    @supports (background: color-mix(in srgb, red 24%, white)) {
      .chip[data-color]:not([data-color="error"]) {
        background: color-mix(in srgb, var(--chip-color) 22%, var(--card-background-color, #fff));
        box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--chip-color) 45%, transparent);
      }
      .chip[data-color]:not([data-color="error"])::before {
        content: ""; width: 8px; height: 8px; border-radius: 50%; flex: none;
        align-self: center; background: var(--chip-color);
      }
      .chip[data-color]:not([data-color="error"]) .label { color: var(--secondary-text-color); }
      .chip[data-color]:not([data-color="error"]) .value { color: var(--primary-text-color); }
      .chip[data-color]:not([data-color="error"]) .why { color: var(--primary-color, #03a9f4); }
    }
  `;

  class CtcEcoZenithChips extends CtcCard {
    constructor() {
      super(CHIPS_STYLE);
    }

    _build() {
      const card = this._shell();
      const strip = document.createElement("div");
      strip.className = "chips";
      // R41: a note per chip. They shared one, while what is open was kept per
      // entity, so the "i" of a second chip left the first one's text open.
      const notes = document.createElement("div");
      notes.className = "notes";
      this._items = (this._config.items || []).map((item) => {
        const chip = document.createElement("div");
        chip.className = "chip";
        // R36: the colour of what the chip means, from the page's YAML.
        if (item.color) chip.dataset.color = String(item.color);
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const value = document.createElement("span");
        value.className = "value";
        chip.append(this._named(item, note), value);
        strip.appendChild(chip);
        notes.appendChild(note);
        return { item, chip, value, note };
      });
      card.append(strip, notes);
    }

    /** The chip's name before its explanation: the notes stand under the
     *  line, and two of them can be open at once. */
    _lead(note, item) {
      const lead = document.createElement("span");
      lead.className = "lead";
      lead.textContent = `${item.name || item.entity}: `;
      note.appendChild(lead);
    }

    /** The chip's word while it is on. An item may name attributes that carry
     *  the text to show instead, the first of them there: the display's alarm
     *  shows its code and text that way. Its own word for the state otherwise. */
    _chipText(item, stateObj) {
      const attributes = (stateObj && stateObj.attributes) || {};
      for (const name of [].concat(item.value_attribute || [])) {
        const found = attributes[name];
        if (found !== undefined && found !== null && String(found) !== "") return String(found);
      }
      return String(this._text(item, stateObj));
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        // R36: a chip with an on_state is on the line only while it reads it,
        // so "Larm: OK" and "Avfrostning: Av" are not there to read.
        const off = row.item.on_state !== undefined
          && (!stateObj || stateObj.state !== String(row.item.on_state));
        row.chip.hidden = off || this._missing(row.item);
        if (row.chip.hidden) this._shut(row.item, row.note);
        // The chip is its name and then the page's word for the state. A
        // word that is the name over again is not written twice: an alarm
        // chip named "Larm" whose word for on is "Larm" reads "Larm" once,
        // the name being the message and the colour the state.
        const text = this._chipText(row.item, stateObj);
        const label = String(row.item.name || row.item.entity);
        const repeat = text.toLowerCase() === label.toLowerCase();
        row.value.textContent = repeat ? "" : text;
        row.value.hidden = repeat || text === "";
      }
    }
  }

  /* --------------------------------------------------------------- readings */

  /* The grids ask for a track no wider than the card: a bare minmax(140px, 1fr)
     still lays out a 140 px track inside a narrower card and sticks out of it,
     which on a 360 px phone is what the list did. min(140px, 100%) gives in. */
  const READINGS_STYLE = `
    .tiles { display: grid; gap: 14px 24px; grid-template-columns: repeat(auto-fill, minmax(min(140px, 100%), 1fr)); }
    .tile .label { display: block; color: var(--secondary-text-color); font-size: .8em; overflow-wrap: anywhere; }
    .tile .big { font-size: 1.6rem; font-weight: 500; color: var(--primary-text-color); }
    /* R35: what the figure rests on, or why there is none, in small text under it. */
    .tile .sub {
      color: var(--secondary-text-color); font-size: .75em; line-height: 1.3; overflow-wrap: anywhere;
    }
    .tile .sub:empty { display: none; }
    .tile .note { padding-top: 2px; }
  `;

  class CtcEcoZenithReadings extends CtcCard {
    constructor() {
      super(READINGS_STYLE);
    }

    _build() {
      const card = this._shell();
      const grid = document.createElement("div");
      grid.className = "tiles";
      this._items = (this._config.items || []).map((item) => {
        const tile = document.createElement("div");
        tile.className = "tile";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const value = document.createElement("div");
        value.className = "big";
        tile.append(this._named(item, note, "div"), value);
        let sub = null;
        if (item.sub || item.reason) {
          // R35: the line under the figure, drawn from the entity's attributes.
          sub = document.createElement("div");
          sub.className = "sub";
          tile.appendChild(sub);
        }
        tile.appendChild(note);
        grid.appendChild(tile);
        return { item, tile, value, sub };
      });
      card.append(grid);
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        row.tile.hidden = this._missing(row.item);
        row.value.textContent = this._text(row.item, stateObj);
        if (row.sub) row.sub.textContent = this._below(row.item, stateObj);
      }
    }
  }

  /* --------------------------------------------------------------- controls */

  const CONTROLS_STYLE = `
    .control {
      display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 4px 12px;
      padding: 8px 0; border-bottom: 1px solid var(--divider-color, #eee);
    }
    .control:last-of-type { border-bottom: none; }
    .control .label { color: var(--primary-text-color); overflow-wrap: break-word; min-width: 0; }
    .widget { display: flex; align-items: center; gap: 8px; justify-self: end; }
    .widget[data-pending="1"] { opacity: .5; }
    /* A control without a value yet: the slider's track dimmed with no knob on
       it, so nothing looks set, the "i" beside it, and a word in grey where the
       value would be. appearance: none is what lets the knob be hidden at all;
       the track is then drawn as a line of the card's own. */
    .widget[data-unset="1"] input[type="range"] {
      -webkit-appearance: none; appearance: none; height: 20px; opacity: .6;
      background: linear-gradient(var(--divider-color, #ccc), var(--divider-color, #ccc))
        center / 100% 4px no-repeat;
    }
    .widget[data-unset="1"] input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none; appearance: none; width: 0; height: 0;
    }
    .widget[data-unset="1"] input[type="range"]::-moz-range-thumb {
      width: 0; height: 0; border: none; background: transparent;
    }
    .widget[data-unset="1"] .reading { color: var(--secondary-text-color); font-weight: 400; }
    select, input[type="number"] {
      font: inherit; padding: 6px 8px; border-radius: 8px; max-width: 190px;
      border: 1px solid var(--divider-color, #ccc);
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    input[type="range"] { width: 130px; accent-color: var(--primary-color, #03a9f4); }
    input[type="checkbox"] {
      width: 20px; height: 20px; margin: 0; cursor: pointer;
      accent-color: var(--primary-color, #03a9f4);
    }
    .reading { color: var(--primary-text-color); font-weight: 500; min-width: 56px; text-align: right; }
    .widget button {
      font: inherit; padding: 6px 14px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--primary-color, #03a9f4);
      background: transparent; color: var(--primary-color, #03a9f4);
    }
    .note { grid-column: 1 / -1; }
    .widget button.release { padding: 4px 10px; font-size: .85em; }
  `;

  class CtcEcoZenithControls extends CtcCard {
    constructor() {
      super(CONTROLS_STYLE);
    }

    _build() {
      const card = this._shell();
      this._items = (this._config.items || []).map((item) => {
        const row = document.createElement("div");
        row.className = "control";
        const note = document.createElement("div");
        note.className = "note";
        note.hidden = true;
        const widget = document.createElement("span");
        widget.className = "widget";
        widget.dataset.pending = "0";
        const label = this._named(item, note);
        // R49: "Styrd" after the name, the pump's own value under the row.
        const held = this._heldParts(item);
        if (held) label.appendChild(held.mark);
        row.append(label, widget);
        if (held) row.appendChild(held.pump);
        row.appendChild(note);
        card.appendChild(row);
        return { item, row, widget, note, held, release: null, update: null };
      });
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        if (!row.update) {
          // The control is built from the entity's own range and options, so it
          // waits for the first state rather than guessing at an empty one.
          if (!this._state(row.item.entity)) continue;
          row.update = this._widget(row.widget, row.item, row.note);
          if (String(row.item.entity).startsWith("number.")) {
            row.release = this._releaseButton(row.widget, row.item);
          }
        }
        row.update();
        const stateObj = this._state(row.item.entity);
        // A mode list shows Släpp styrningen at rest, so the pump's own mode
        // is said under it; a number shows its state.
        const shows = String(row.item.entity).startsWith("select.") ? listValue(stateObj) : undefined;
        const held = this._showHeld(row.held, row.item, stateObj, shows);
        if (row.release) {
          row.release.hidden = !held;
          // Let go of: the button is ready again for the next override.
          if (!held) row.release.disabled = false;
        }
      }
    }

    /** R49: a number Home Assistant holds gets a button that lets go of that
     *  one alone, through the integration's service release: nothing is
     *  written, Home Assistant stops writing, and the pump goes back to its own
     *  value within about five minutes. A list lets go by its own option,
     *  Släpp styrningen, and gets no button. The button waits for the state
     *  that says the number is no longer held; a refusal, which Home
     *  Assistant shows as a toast, makes it ready again at once. */
    _releaseButton(container, item) {
      const states = this._config.states || {};
      const button = document.createElement("button");
      button.type = "button";
      button.className = "release";
      button.textContent = states.release || "Release";
      button.setAttribute("aria-label", `${button.textContent}: ${item.name || item.entity}`);
      button.hidden = true;
      button.addEventListener("click", () => {
        button.disabled = true;
        const again = () => { button.disabled = false; };
        new Promise((resolve) => resolve(
          this._hass.callService("ctc_ecozenith", "release", { entity_id: item.entity })
        )).then(() => setTimeout(again, 6000), again);
      });
      container.appendChild(button);
      return button;
    }

    /** A control without a value says why in its note: the pump does not give
     *  out its own value here, so nothing is set until Home Assistant writes,
     *  or, for one with a mirror, the pump's value could not be read just now
     *  (F2.1, F2.3). A page from before the second note says the first. */
    _more(note, item) {
      const stateObj = this._state(item.entity);
      if (!stateObj || stateObj.state !== "unknown") return;
      const states = this._config.states || {};
      const text = (unread(stateObj.attributes || {}) && states.unread_note) || states.unset_note;
      if (!text) return;
      const why = document.createElement("span");
      why.className = "meta";
      why.textContent = text;
      note.appendChild(why);
    }

    /** Build the control itself and return how to keep it current. A control the
     *  user is holding is left alone: Home Assistant sends a new state while a
     *  slider is being dragged, and writing it back would fight the thumb. */
    _widget(container, item, note) {
      const entityId = item.entity;
      const domain = String(entityId).split(".")[0];
      // R47: the debounce timer of a list or a slider while a change waits,
      // and how many writes this control has sent. Only the latest write may
      // end the busy mark, and none may while a newer change is still
      // waiting to go out: the row is busy from the change on.
      let waiting = null;
      let sent = 0;
      // How many of those writes Home Assistant has not answered yet. Down
      // and up again within the wait sends nothing at rest, but not while an
      // earlier write is on its way: the state does not say it yet, and the
      // later value is what must win.
      let flying = 0;
      /** Send a value and answer whether it was taken. A refused write is a
       *  rejected promise, and the reason is already on the screen as Home
       *  Assistant's toast. The row only has to stop looking busy, now rather
       *  than six seconds from now. No row of its own for the error, no range
       *  check and no clipping here: HA holds min and max, and clipping would
       *  send something other than what was typed. The promise wrapper also
       *  turns a call that throws outright into a refusal. The answer is a
       *  resolved true or false rather than a rejection, so a control with
       *  nothing to undo, a slider or a list, need not catch anything. */
      const send = (value) => {
        const mine = ++sent;
        flying += 1;
        container.dataset.pending = "1";
        const settle = () => {
          if (mine === sent && waiting === null) container.dataset.pending = "0";
        };
        const timer = setTimeout(settle, 6000);
        return new Promise((resolve) => resolve(this._call(entityId, value))).then(
          () => {
            flying -= 1;
            return true;
          },
          () => {
            flying -= 1;
            clearTimeout(timer);
            settle();
            return false;
          }
        );
      };
      /** R47: send what a list or a slider was changed to, once it has stood
       *  still for DEBOUNCE_MS. A newer change clears the timer and takes the
       *  older one's place, so only the last value is written. The row looks
       *  busy from the first change, which also keeps an unset slider's knob
       *  where the user put it while the write waits. `after` hears whether
       *  the write was taken, unless a newer change or write has come since:
       *  then that one's answer is the one that counts. A value that is what
       *  the control shows at rest anyway, down and up again within the wait,
       *  is not sent at all: it would ask for nothing new and only make Home
       *  Assistant hold the register, Styrd and keepalive with it. */
      const sendLast = (value, after) => {
        container.dataset.pending = "1";
        clearTimeout(waiting);
        waiting = setTimeout(() => {
          waiting = null;
          if (flying === 0 && unchangedAtRest(entityId, value, this._state(entityId))) {
            container.dataset.pending = "0";
            return;
          }
          const answer = send(value);
          const mine = sent;
          answer.then((taken) => {
            if (after && mine === sent && waiting === null) after(taken);
          });
        }, DEBOUNCE_MS);
      };

      // What the control is for, said to a screen reader on the control
      // itself, as the release button does: the name stands in a span of its
      // own beside it, with the "i" and the mark Styrd in it as well, so
      // pointing at that span would read them too.
      const name = String(item.name || item.entity);

      if (domain === "button") {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = this._config.press || "Press";
        button.setAttribute("aria-label", `${button.textContent}: ${name}`);
        button.addEventListener("click", () => send(null));
        container.appendChild(button);
        return () => {
          const stateObj = this._state(entityId);
          button.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      if (domain === "switch") {
        // The integration's own switch, the debug report (switch.py): a tick
        // box, named on a screen reader by the row's name as the list is. A
        // click is sent at once, there being nothing to step through. While
        // the write is on its way the box keeps the tick the user gave it,
        // and the state that says the same ends the busy mark; a refusal,
        // which Home Assistant shows as a toast, puts back what the state says.
        const box = document.createElement("input");
        box.type = "checkbox";
        box.setAttribute("aria-label", name);
        const isOn = (stateObj) => Boolean(stateObj) && stateObj.state === "on";
        box.addEventListener("change", () => {
          send(box.checked).then((taken) => {
            if (!taken) box.checked = isOn(this._state(entityId));
          });
        });
        container.appendChild(box);
        return () => {
          const stateObj = this._state(entityId);
          if (stateObj && container.dataset.pending !== "1") box.checked = isOn(stateObj);
          if (stateObj && box.checked === isOn(stateObj)) container.dataset.pending = "0";
          box.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      if (domain === "select") {
        const select = document.createElement("select");
        select.setAttribute("aria-label", name);
        // Every arrow key in a closed list is a change; only the last is sent.
        // A refused write puts back what the list should show, whether or not
        // it still has the focus, as a refused slider does: the keyboard
        // leaves it focused, and the incoming states would pass it by.
        select.addEventListener("change", () => sendLast(select.value, (taken) => {
          if (taken) return;
          const stateObj = this._state(entityId);
          if (stateObj) select.value = listValue(stateObj);
        }));
        container.appendChild(select);
        return () => {
          const stateObj = this._state(entityId);
          const options = (stateObj && stateObj.attributes.options) || [];
          if (select.options.length !== options.length ||
              [...select.options].some((option, i) => option.value !== options[i])) {
            select.replaceChildren();
            for (const option of options) {
              const choice = document.createElement("option");
              choice.value = option;
              choice.textContent = option;
              select.appendChild(choice);
            }
          }
          // What the list should show: the mode held, or Släpp styrningen at
          // rest (listValue). Put in only while no change waits to be sent
          // and nobody holds the list: a list that lost the focus before its
          // wait ran out, a picker on a phone closed with Done, would
          // otherwise get the old mode back while the new one waits.
          const shows = listValue(stateObj);
          if (waiting === null && !holding(select) && stateObj) select.value = shows;
          // The list shows what the state says it should: the write has
          // landed, and the row stops looking busy now rather than when the
          // timer runs out. Asked whoever is holding the list, since the
          // keyboard leaves it focused, but not while a change still waits to
          // be sent (R47). After Släpp styrningen that is at once, since the
          // list at rest stands on it whatever mode the pump runs.
          if (stateObj && waiting === null && select.value === shows) {
            container.dataset.pending = "0";
          }
          select.disabled = !stateObj || stateObj.state === "unavailable";
        };
      }

      const attributes = (this._state(entityId) || {}).attributes || {};
      const step = Number(attributes.step) || 1;
      const states = this._config.states || {};
      // A number without a value yet: the hot water setpoint and extra hot
      // water have no mirror register (61503 did not follow 1006, R50), so
      // they are unknown until something is written, and one with a
      // mirror is unknown while idle through a Modbus round that was lost
      // (F2.1), which the reading and the note then say instead. The browser
      // would draw its slider with the knob in the middle, as if someone had
      // set it there, and whoever dragged it wrote a value they never meant.
      // The row is marked unset instead: the style hides the knob and dims the
      // track, the reading says so, and an "i" beside it opens the row's note.
      const why = explainButton(this._config.explain, note, () => this._toggle(item, note), name);
      why.hidden = true;
      const mark = (unset) => {
        container.dataset.unset = unset ? "1" : "0";
        why.hidden = !unset;
      };
      // The card's YAML may insist on one or the other; otherwise the range decides.
      const kind = item.widget === "slider" || item.widget === "field"
        ? item.widget
        : widgetFor(attributes.min, attributes.max, attributes.step);
      if (kind === "slider") {
        const slider = document.createElement("input");
        slider.type = "range";
        slider.setAttribute("aria-label", name);
        slider.min = attributes.min;
        slider.max = attributes.max;
        slider.step = step;
        const reading = document.createElement("span");
        reading.className = "reading";
        slider.addEventListener("input", () => {
          // The user is setting it: whatever the pump says, it is no longer unset.
          mark(false);
          reading.textContent = withUnit(slider.value, attributes.unit_of_measurement);
        });
        /** Keep the slider current. `force` puts the pump's value in even while
         *  the user holds the slider: a range input keeps the focus after a
         *  drag, and a refused write must not leave the dragged value beside
         *  a knob the unset style has hidden. Taking the focus instead would
         *  pull it from a keyboard user in the middle of adjusting. */
        const update = (force = false) => {
          const stateObj = this._state(entityId);
          if (!stateObj) return;
          const unset = stateObj.state === "unknown";
          // A write on its way from an unset slider: the knob stays where the
          // user put it until the pump's value arrives or the refusal lets go.
          if (unset && container.dataset.pending === "1") return;
          // Neither while the user holds the slider nor while a change waits
          // to be sent: a slider a tap or a click does not give the focus
          // (Safari, iOS) would get the pump's old value back meanwhile.
          if (force || (waiting === null && !holding(slider))) {
            // Unset, the knob is parked at the bottom of the range, hidden by
            // the style, rather than left in the middle by the browser.
            slider.value = unset
              ? (attributes.min !== undefined ? attributes.min : 0)
              : stateObj.state;
            reading.textContent = this._text(item, stateObj);
          }
          // The pump reports what the slider shows: the write has landed, and
          // the row stops looking busy now rather than when the timer runs out.
          // Asked whoever is holding the slider, since a range input keeps the
          // focus after a drag, and asked of the numbers: Home Assistant writes
          // a float as "40.0" and a range input serializes it back as "40".
          // unknown and unavailable are NaN and never equal. Not while a
          // change still waits to be sent, though (R47).
          if (waiting === null && Number(slider.value) === Number(stateObj.state)) {
            container.dataset.pending = "0";
          }
          mark(unset);
          slider.disabled = stateObj.state === "unavailable";
        };
        // A refused write goes straight back to what the pump says, unset
        // included, rather than waiting for the next state to come round, and
        // whether or not the slider still has the focus from the drag. The
        // arrow keys fire change at every step, so the value waits (R47).
        slider.addEventListener("change", () => {
          sendLast(slider.value, (taken) => { if (!taken) update(true); });
        });
        container.append(slider, reading, why);
        return update;
      }

      const field = document.createElement("input");
      field.type = "number";
      field.setAttribute("aria-label", name);
      if (attributes.min !== undefined) field.min = attributes.min;
      if (attributes.max !== undefined) field.max = attributes.max;
      field.step = attributes.step || "any";
      const unit = document.createElement("span");
      unit.className = "reading";
      unit.textContent = attributes.unit_of_measurement || "";
      // What the field last showed from the state, or last sent: a commit that
      // asks for the same thing again is a tab through the field, or a blur
      // after Enter, and must not become a second write to the pump.
      let shown = NaN;
      // The pump's own value back into the field, and counted as shown, so the
      // next blur has nothing new to send. With no state, or one the number
      // input will not hold (unavailable), the field ends up "", and shown has
      // to be NaN rather than Number("") = 0, or a 0 typed on purpose afterwards
      // would pass for already sent.
      const restore = () => {
        const stateObj = this._state(entityId);
        field.value = stateObj ? stateObj.state : "";
        const back = parseFieldValue(field.value);
        shown = back === null ? NaN : back;
      };
      const commit = () => {
        const value = parseFieldValue(field.value);
        if (value === null) {
          // Emptied, or holding something that is not a number: that asks for
          // nothing, so the pump's own value goes back in.
          restore();
          return;
        }
        if (value === shown) return;
        shown = value;
        send(value).then((taken) => {
          // Refused, and nothing shown or sent since: the refused number must
          // not count as shown, or Enter on it again would be dead. While the
          // field still shows it, the pump's own value goes back in, so a blur
          // sends nothing. A number the user has begun typing since is left
          // alone, and simply counts as new.
          if (taken || shown !== value) return;
          if (parseFieldValue(field.value) === value) restore();
          else shown = NaN;
        });
      };
      // Enter and leaving the field are the two ways of saying "this is it".
      // The browser's change event would also fire on every click of the
      // spinner arrows, one Modbus write per tenth of a degree.
      field.addEventListener("keydown", (event) => {
        if (event.key === "Enter") commit();
      });
      field.addEventListener("blur", commit);
      container.append(field, unit, why);
      return () => {
        const stateObj = this._state(entityId);
        if (!stateObj) return;
        const unset = stateObj.state === "unknown";
        if (!holding(field)) {
          field.value = stateObj.state;
          shown = Number(stateObj.state);
          if (String(field.value) === String(stateObj.state)) container.dataset.pending = "0";
        }
        // The number input holds "" for unknown; the placeholder says what that
        // is, not set or not read.
        field.placeholder = unset
          ? (unread(stateObj.attributes || {}) && states.unread) || states.unset || ""
          : "";
        mark(unset);
        field.disabled = stateObj.state === "unavailable";
      };
    }
  }

  /* ------------------------------------------------------------------- rows */

  const ROWS_STYLE = `
    .toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding-bottom: 10px; }
    .toolbar input {
      flex: 1; min-width: 0; max-width: 420px; padding: 8px 12px; font: inherit;
      border: 1px solid var(--divider-color, #ccc); border-radius: 8px;
      background: var(--card-background-color, #fff); color: var(--primary-text-color);
    }
    /* On a phone the field takes the whole line and the count goes under it. */
    @media (max-width: 480px) {
      .toolbar input { flex-basis: 100%; max-width: none; }
    }
    .count { color: var(--secondary-text-color); font-size: .9em; }
    /* R39: the box that shows the empty rows too. It is no search field, so
       the field's look above is taken off it again. */
    .empties {
      display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
      color: var(--secondary-text-color); font-size: .9em;
    }
    .toolbar .empties input {
      flex: none; width: auto; max-width: none; margin: 0; padding: 0; border: none;
      background: none; accent-color: var(--primary-color, #03a9f4);
    }
    .grid { display: grid; gap: 0 24px; grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr)); }
    .heading {
      grid-column: 1 / -1; font-size: .8rem; font-weight: 500; margin: 14px 0 4px;
      color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em;
    }
    .heading:first-child { margin-top: 0; }
    .row {
      display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; align-items: baseline;
      padding: 9px 4px; border-bottom: 1px solid var(--divider-color, #eee);
      border-radius: 4px;
    }
    /* The row is no tab stop of its own (R41): its "i" is, with its own ring.
       It looks clickable only where a click does something: it opens the
       note, or Home Assistant's dialog where there is no "i". */
    .row[data-tap="1"] { cursor: pointer; }
    .row[data-tap="1"]:hover { background: var(--secondary-background-color, #f5f5f5); }
    .row .label { color: var(--primary-text-color); overflow-wrap: anywhere; }
    .value { color: var(--primary-text-color); font-weight: 500; white-space: nowrap; text-align: right; }
    /* R35: what a figure rests on, or why there is none, under its row. */
    .row .sub { grid-column: 1 / -1; color: var(--secondary-text-color); font-size: .75em; line-height: 1.3; }
    .row .sub:empty { display: none; }
    .note { grid-column: 1 / -1; }
    .empty { color: var(--secondary-text-color); padding: 16px 4px; }
  `;

  class CtcEcoZenithRows extends CtcCard {
    constructor() {
      super(ROWS_STYLE);
      // What is typed in the search, as typed and as it is matched, and
      // whether the empty rows are shown too (R39).
      this._typed = "";
      this._query = "";
      this._all = false;
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.rows)) {
        throw new Error("ctc-ecozenith-rows needs rows");
      }
      super.setConfig(config);
    }

    getCardSize() {
      return 1 + this._items.filter((row) => !row.element.hidden).length;
    }

    _build() {
      const card = this._shell();
      if (this._config.filter) {
        this._recall();
        card.appendChild(this._searchField());
      }
      const grid = document.createElement("div");
      grid.className = "grid";
      this._items = [];
      this._headings = [];
      for (const item of this._config.rows) {
        if (item && item.heading !== undefined) {
          const heading = document.createElement("div");
          heading.className = "heading";
          heading.textContent = item.heading;
          grid.appendChild(heading);
          this._headings.push({ element: heading, rows: [] });
          continue;
        }
        const row = this._row(item);
        grid.appendChild(row.element);
        this._items.push(row);
        if (this._headings.length) this._headings[this._headings.length - 1].rows.push(row);
      }
      this._empty = document.createElement("div");
      this._empty.className = "empty";
      this._empty.textContent = this._config.empty || "";
      this._empty.hidden = true;
      card.append(grid, this._empty);
    }

    _searchField() {
      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const input = document.createElement("input");
      input.type = "search";
      input.placeholder = this._config.filter === true ? "" : String(this._config.filter);
      input.setAttribute("aria-label", input.placeholder);
      input.value = this._typed;
      input.addEventListener("input", () => {
        this._typed = input.value;
        this._query = this._typed.trim().toLowerCase();
        this._keep();
        this._show();
      });
      toolbar.appendChild(input);
      if (this._config.show_empty) {
        // R39: a row with nothing to show is left out unless this is ticked.
        const label = document.createElement("label");
        label.className = "empties";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = this._all;
        box.addEventListener("change", () => {
          this._all = Boolean(box.checked);
          this._keep();
          this._show();
        });
        label.append(box, String(this._config.show_empty));
        toolbar.appendChild(label);
      }
      this._count = document.createElement("span");
      this._count.className = "count";
      // What the three numbers are: shown, with a value, all of them.
      if (this._config.count) this._count.title = String(this._config.count);
      toolbar.appendChild(this._count);
      return toolbar;
    }

    /** R39: where this list keeps its search and its box: the page may set a
     *  key, and otherwise the first entity names the list, which on the CTC
     *  page carries the pump's own prefix. */
    _keepKey() {
      const first = (this._config.rows || []).find((item) => item && item.entity);
      return KEEP_PREFIX + String(this._config.storage_key || (first && first.entity) || "");
    }

    /** R39: the search and the box as this tab last left them. A new layout
     *  (lovelace_updated) builds the card anew and used to throw the search
     *  away; without sessionStorage the card keeps what it had itself. */
    _recall() {
      const kept = recall(this._keepKey());
      if (kept) {
        if (typeof kept.query === "string") this._typed = kept.query;
        if (typeof kept.all === "boolean") this._all = kept.all;
      }
      this._query = this._typed.trim().toLowerCase();
    }

    _keep() {
      keep(this._keepKey(), { query: this._typed, all: this._all });
    }

    _row(item) {
      const element = document.createElement("div");
      element.className = "row";
      const note = document.createElement("div");
      note.className = "note";
      note.hidden = true;
      const value = document.createElement("span");
      value.className = "value";
      const label = this._named(item, note);
      // R49: "Styrd" after the name, the pump's own value under the row.
      const held = this._heldParts(item);
      if (held) label.appendChild(held.mark);
      element.append(label, value);
      if (held) element.appendChild(held.pump);
      let sub = null;
      if (item.sub || item.reason) {
        // R35: the line under the row, drawn from the entity's attributes.
        sub = document.createElement("div");
        sub.className = "sub";
        element.appendChild(sub);
      }
      element.appendChild(note);
      // R41: the "i" is the one toggle, for a screen reader and for Tab, where
      // the row was a second one with a tab stop of its own, 120 of them on
      // All values. A click anywhere else on the row is passed on to the "i",
      // so a finger or a mouse still opens the note from the whole row. The
      // "i" and the note's link stop their own clicks from reaching the row.
      // A row without an explanation has no "i", as in a list written by
      // hand or started from the card picker: its click opens Home
      // Assistant's own dialog for the entity, as Home Assistant's entity
      // rows do. Only a row whose click does something looks clickable.
      if (item.explanation || item.entity) element.dataset.tap = "1";
      element.addEventListener("click", () => {
        const why = (TOGGLES.get(note) || [])[0];
        if (why) why.click();
        else if (item.entity) moreInfo(this, item.entity);
      });
      return { item, element, note, value, sub, held, seen: undefined, gone: false, blank: false };
    }

    _update() {
      if (!this._hass) return;
      for (const row of this._items) {
        const stateObj = this._state(row.item.entity);
        row.gone = this._missing(row.item);
        row.blank = this._blank(row.item, stateObj);
        // Only when the state itself changed: every update of any entity in
        // Home Assistant sets hass again.
        if (row.seen === stateObj) continue;
        row.seen = stateObj;
        this._showHeld(row.held, row.item, stateObj);
        if (!stateObj) continue;
        row.value.textContent = this._text(row.item, stateObj);
        if (row.sub) row.sub.textContent = this._below(row.item, stateObj);
      }
      this._show();
    }

    /** R39: whether a row has nothing to show: no state at all, or unavailable
     *  or unknown without a word of the page's own for it, as hide_unavailable
     *  has it elsewhere. A word of the page's own says something: "ej satt"
     *  on a control and "inget larm ännu" on the last alarm stay, and so does
     *  an unknown row whose reason is under it (F3.3). Such rows are counted
     *  among those with a value, the middle number, as rows with something
     *  to show. */
    _blank(item, stateObj) {
      if (!stateObj) return true;
      if (!HIDDEN_STATES.has(stateObj.state)) return false;
      // A row that says why it has no figure says something: the coefficient
      // of performance stays available without a number for just that, and
      // its reason stands under the row.
      if (stateObj.state === "unknown" && item.reason && this._below(item, stateObj)) return false;
      const own = ownWord(item.entity, stateObj, this._config.states, item.zero_means,
                          item.unknown_means);
      return own === undefined;
    }

    /** What is on show: what has a value to show, and what the search asks for.
     *  With the page's box for empty rows (R39) a row with nothing to show is
     *  left out until the box is ticked, and a search looks among those too:
     *  what is asked for is shown whatever it reads. The count then says how
     *  many are shown, how many have a value, and how many there are. */
    _show() {
      let shown = 0;
      let valued = 0;
      const hiding = Boolean(this._config.show_empty) && !this._all;
      for (const row of this._items) {
        if (!row.gone && !row.blank) valued += 1;
        const hidden = row.gone || (this._query ? !this._matches(row) : hiding && row.blank);
        row.element.hidden = hidden;
        if (hidden) this._shut(row.item, row.note);
        else shown += 1;
      }
      // A heading with nothing under it says nothing.
      for (const heading of this._headings) {
        heading.element.hidden = !heading.rows.some((row) => !row.element.hidden);
      }
      if (this._count) {
        this._count.textContent = this._config.show_empty
          ? `${shown} / ${valued} / ${this._items.length}`
          : `${shown} / ${this._items.length}`;
      }
      if (this._empty) this._empty.hidden = !(this._config.filter && this._query && !shown);
    }

    /** A row is searched by everything it says: its name, its explanation, where
     *  the value comes from, and the value itself, and while Home Assistant
     *  holds it, the mark and the pump's own value (R49), so "styrd" finds
     *  what is held right now. */
    _matches(row) {
      if (!this._query) return true;
      const item = row.item;
      const held = row.held && !row.held.mark.hidden ? row.held.mark.textContent : "";
      const own = row.held && !row.held.pump.hidden ? row.held.pump.textContent : "";
      const haystack = [
        item.name, item.explanation, item.source, item.entity, row.value.textContent,
        row.sub ? row.sub.textContent : "", held, own,
      ].join(" ").toLowerCase();
      return this._query.split(/\s+/).every((word) => haystack.includes(word));
    }
  }

  const CARDS = {
    "ctc-ecozenith-chips": CtcEcoZenithChips,
    "ctc-ecozenith-readings": CtcEcoZenithReadings,
    "ctc-ecozenith-controls": CtcEcoZenithControls,
    "ctc-ecozenith-rows": CtcEcoZenithRows,
  };
  for (const [name, card] of Object.entries(CARDS)) {
    if (!customElements.get(name)) customElements.define(name, card);
  }
})();

/* --------------------------------------- the card picker and the section card (L11)
 *
 * A closure of its own after the page's cards, which it touches only to give each
 * of them a stub configuration for Home Assistant's card picker:
 *
 *   custom:ctc-ecozenith-section    one section of the CTC page on a dashboard of
 *                                   one's own, from a heat pump (device_id) and a
 *                                   section, with the page's explanations
 *
 * The section card writes no list of values of its own. It asks the integration
 * for the section's cards over the websocket command ctc_ecozenith/items, which
 * cuts them out of the page as the page would be built this moment
 * (section_card.py), and draws them with the page's own cards above. So the texts
 * stay in the integration and follow it, and the section follows the page: the
 * card asks again whenever an open page would fetch its layout again, which is
 * when lovelace_updated names the page's address.
 *
 * All five cards are in the card picker (window.customCards), the section card
 * first. The four page cards take lists of values, which the picker cannot
 * write: their stub is a handful of the integration's own entities, enough to see
 * the card, and the description says that the section card brings the
 * explanations. The block hands node nothing of its own: tests/test_card_picker.py
 * and tests/test_card_section.py work it through the stub DOM, so the helpers the
 * page cards hand over above stay exactly what they were.
 */

(() => {
  const DOMAIN = "ctc_ecozenith";
  //: The CTC page's address, which lovelace_updated names when the page changes.
  const PAGE = "ctc-ecozenith";
  const COMMAND = `${DOMAIN}/items`;
  const SECTION_CARD = "ctc-ecozenith-section";
  const DOCS = "https://github.com/beolink/HA-CTC-Local-Easyconf#the-section-card";
  //: After a request that failed the card asks again with a later state, but
  //: not before this: a pump that is starting answers within half a minute.
  const RETRY_MS = 30000;
  //: What the section card's stub shows: a section every pump has.
  const STUB_SECTION = "temperatures";

  //: The sections, in the page's order, as section_card.SECTIONS has them, and
  //: what the editor calls each (section_card.label). tests/test_card_picker.py
  //: holds the two sides equal.
  const SECTIONS = [
    "status", "overrides", "quick_controls", "key_readings",
    "heating_controls", "hot_water_controls", "power_controls", "troubleshooting",
    "temperatures", "hot_water", "energy", "compressor", "cycles", "settings", "unit", "other",
    "values",
  ];
  const SECTION_LABELS = {
    sv: {
      status: "Översikt: Drift",
      overrides: "Översikt: Styrs från Home Assistant",
      quick_controls: "Översikt: Snabbstyrning",
      key_readings: "Översikt: Nyckeltal",
      heating_controls: "Styrning: Värme",
      hot_water_controls: "Styrning: Varmvatten",
      power_controls: "Styrning: Drift och el",
      troubleshooting: "Styrning: Felsökning",
      temperatures: "Prestanda: Temperaturer",
      hot_water: "Prestanda: Varmvatten",
      energy: "Prestanda: Energi och värmefaktor",
      compressor: "Prestanda: Kompressor och köldkrets",
      cycles: "Prestanda: Starter, körningar och avfrostningar",
      settings: "Prestanda: Pumpens inställningar",
      unit: "Prestanda: Om enheten",
      other: "Prestanda: Övrigt",
      values: "Alla värden",
    },
    en: {
      status: "Overview: Operation",
      overrides: "Overview: Held by Home Assistant",
      quick_controls: "Overview: Quick controls",
      key_readings: "Overview: Key readings",
      heating_controls: "Controls: Heating",
      hot_water_controls: "Controls: Hot water",
      power_controls: "Controls: Operation and power",
      troubleshooting: "Controls: Troubleshooting",
      temperatures: "Performance: Temperatures",
      hot_water: "Performance: Hot water",
      energy: "Performance: Energy and COP",
      compressor: "Performance: Compressor and refrigerant circuit",
      cycles: "Performance: Starts, runs and defrosts",
      settings: "Performance: Settings in the heat pump",
      unit: "Performance: About the unit",
      other: "Performance: Other",
      values: "All values",
    },
  };

  //: The page's cards, by what their stub is made of.
  const PAGE_CARDS = {
    chips: "ctc-ecozenith-chips",
    readings: "ctc-ecozenith-readings",
    controls: "ctc-ecozenith-controls",
    rows: "ctc-ecozenith-rows",
  };

  //: The card picker's name and description of each card, in the language
  //: Home Assistant is shown in.
  const PICKER = {
    sv: {
      section: [
        "CTC: sektion av sidan",
        "En sektion av CTC-sidan på en egen instrumentpanel, med sidans förklaring till "
          + "varje värde. Välj värmepump och sektion, så hämtas resten ur integrationen.",
      ],
      chips: [
        "CTC: chips",
        "Värden som en rad chips, som driftraden på CTC-sidan. Värdena listas under items. "
          + "Sidans förklaringar får du med sektionskortet.",
      ],
      readings: [
        "CTC: nyckeltal",
        "Litet namn över stor siffra, som nyckeltalen på CTC-sidan. Värdena listas under items. "
          + "Sidans förklaringar får du med sektionskortet.",
      ],
      controls: [
        "CTC: reglage",
        "En rad per reglage, med listan, skjutreglaget eller fältet till höger, som styrningen "
          + "på CTC-sidan. Reglagen listas under items. Sidans förklaringar får du med "
          + "sektionskortet.",
      ],
      rows: [
        "CTC: värdelista",
        "En tät lista, namn till vänster och värde till höger, som listorna på CTC-sidan. "
          + "Värdena listas under rows. Sidans förklaringar får du med sektionskortet.",
      ],
    },
    en: {
      section: [
        "CTC: section of the page",
        "A section of the CTC page on a dashboard of your own, with the page's explanation "
          + "for every value. Pick the heat pump and the section, and the rest comes from the "
          + "integration.",
      ],
      chips: [
        "CTC: chips",
        "Values as a line of chips, like the status line on the CTC page. The values are listed "
          + "under items. The section card brings the page's explanations.",
      ],
      readings: [
        "CTC: key figures",
        "A small name over a large number, like the key figures on the CTC page. The values are "
          + "listed under items. The section card brings the page's explanations.",
      ],
      controls: [
        "CTC: controls",
        "One row per control, with the list, slider or field on the right, like the controls on "
          + "the CTC page. The controls are listed under items. The section card brings the "
          + "page's explanations.",
      ],
      rows: [
        "CTC: list of values",
        "A dense list, name on the left and value on the right, like the lists on the CTC page. "
          + "The values are listed under rows. The section card brings the page's explanations.",
      ],
    },
  };

  //: The section card's own words: its editor's two fields, and what it says
  //: in place of the section. A refusal it has no word for is said in the
  //: integration's own.
  const WORDS = {
    sv: {
      device_id: "Värmepump",
      section: "Sektion",
      not_found: "Enheten är ingen värmepump i CTC Local Easyconf.",
      not_running: "Värmepumpen är inte igång just nu. Kortet fylls i när integrationen "
        + "har startat.",
      unknown_command: "Kortet är nyare än integrationen som körs. Starta om Home Assistant "
        + "efter uppdateringen.",
      empty: "Sidan visar inget i den här sektionen för den här värmepumpen just nu.",
      failed: "Sektionen kunde inte hämtas.",
    },
    en: {
      device_id: "Heat pump",
      section: "Section",
      not_found: "The device is not a heat pump of CTC Local Easyconf.",
      not_running: "The heat pump is not running right now. The card fills in once the "
        + "integration has started.",
      unknown_command: "The card is newer than the integration that is running. Restart Home "
        + "Assistant after the update.",
      empty: "The page shows nothing in this section for this heat pump right now.",
      failed: "The section could not be fetched.",
    },
  };

  /* ---------------------------------------------------------- pure helpers */

  /** Swedish for any sv-* setting, English otherwise, as the page decides. */
  function language(lang) {
    return String(lang || "").toLowerCase().startsWith("sv") ? "sv" : "en";
  }

  /** The language Home Assistant is shown in, before any hass is at hand: the
   *  frontend writes it on the document, and the browser's own is the fallback. */
  function pickerLanguage() {
    const root = typeof document !== "undefined" && document ? document.documentElement : null;
    if (root && root.lang) return language(root.lang);
    return language(typeof navigator !== "undefined" && navigator ? navigator.language : "");
  }

  function domainOf(entityId) {
    return String(entityId || "").split(".")[0];
  }

  /** The integration's own entities that have a state, as the registry entries
   *  the frontend holds (hass.entities), sorted by entity id. A hidden entity,
   *  and a diagnostic one, is not what a stub should show, nor is a
   *  configuration one unless `config` lets it through: every number and
   *  select of the integration is a configuration entity (number.py and
   *  select.py), so the controls card's stub would otherwise always be empty. */
  function ownEntries(hass, config = false) {
    const registry = (hass && hass.entities) || {};
    const states = (hass && hass.states) || {};
    const category = (entry) => !entry.entity_category
      || (config && entry.entity_category === "config");
    return Object.values(registry)
      .filter((entry) => entry && entry.platform === DOMAIN && !entry.hidden
        && category(entry) && states[entry.entity_id])
      .sort((a, b) => (a.entity_id < b.entity_id ? -1 : a.entity_id > b.entity_id ? 1 : 0));
  }

  //: What each page card's stub is made of: the list it fills, how many of
  //: the integration's entities at most, and which. `config` lets the
  //: integration's configuration entities in, which the controls are.
  const STUBS = {
    chips: {
      field: "items", count: 3,
      wants: (id, s) => domainOf(id) === "sensor" && s.attributes.device_class === "enum",
    },
    readings: {
      field: "items", count: 4,
      wants: (id, s) => domainOf(id) === "sensor" && Boolean(s.attributes.unit_of_measurement)
        && s.state !== "" && Number.isFinite(Number(s.state)),
    },
    controls: {
      field: "items", count: 3, config: true,
      wants: (id) => domainOf(id) === "number" || domainOf(id) === "select",
    },
    rows: { field: "rows", count: 6, wants: (id) => domainOf(id) === "sensor" },
  };

  /** A configuration the card picker can show a card with. The section card's
   *  names the first of the integration's heat pumps; a page card's lists a
   *  few of the integration's entities under their friendly names, or none
   *  where there are none, which the cards draw as an empty frame. */
  function stubConfig(kind, hass) {
    if (kind === "section") {
      const pump = ownEntries(hass).find((entry) => entry.device_id);
      return { device_id: pump ? pump.device_id : "", section: STUB_SECTION };
    }
    const stub = STUBS[kind];
    const entries = ownEntries(hass, Boolean(stub.config));
    const states = (hass && hass.states) || {};
    const items = entries
      .map((entry) => entry.entity_id)
      .filter((id) => stub.wants(id, states[id]))
      .slice(0, stub.count)
      .map((id) => ({ entity: id, name: String(states[id].attributes.friendly_name || id) }));
    return { [stub.field]: items };
  }

  /** Whether a card's visibility, as the page writes it, lets it be seen:
   *  every condition holds, an "or" when one of its own does, and a state or
   *  numeric state condition as Home Assistant reads it, with a missing entity
   *  unavailable and a state that is no number below and above every bound.
   *  A kind of condition the page does not write lets the card be seen. */
  function visible(conditions, states) {
    const all = states || {};
    const holds = (condition) => {
      if (!condition || typeof condition !== "object") return true;
      if (condition.condition === "or") return (condition.conditions || []).some(holds);
      if (condition.condition === "and") return (condition.conditions || []).every(holds);
      if (condition.condition === "numeric_state") {
        // What Home Assistant holds in the pump (R49): its cards are seen
        // while the count is above zero.
        const stateObj = all[condition.entity];
        const value = stateObj ? Number(stateObj.state) : NaN;
        if (Number.isNaN(value)) return false;
        const above = condition.above == null ? NaN : Number(condition.above);
        const below = condition.below == null ? NaN : Number(condition.below);
        return (Number.isNaN(above) || value > above) && (Number.isNaN(below) || value < below);
      }
      if (condition.condition !== "state") return true;
      const stateObj = all[condition.entity];
      const state = stateObj ? stateObj.state : "unavailable";
      if (condition.state_not !== undefined) return ![].concat(condition.state_not).includes(state);
      if (condition.state !== undefined) return [].concat(condition.state).includes(state);
      return true;
    };
    return !Array.isArray(conditions) || conditions.every(holds);
  }

  /** The editor Home Assistant draws for the section card from a schema: the
   *  heat pump, among this integration's devices only, and the section. */
  function configForm(lang) {
    const words = WORDS[lang];
    return {
      schema: [
        {
          name: "device_id",
          required: true,
          selector: { device: { filter: { integration: DOMAIN } } },
        },
        {
          name: "section",
          required: true,
          selector: {
            select: {
              mode: "dropdown",
              options: SECTIONS.map((id) => ({ value: id, label: SECTION_LABELS[lang][id] })),
            },
          },
        },
      ],
      computeLabel: (schema) => words[schema.name],
    };
  }

  // Node without the stub DOM: nothing to define, and nothing to hand over.
  if (typeof customElements === "undefined") return;

  /* ------------------------------------------------------------------ DOM */

  const STYLE = `
    :host { display: block; }
    :host([hidden]) { display: none; }
    /* The page's cards say display: block on their own host, which would beat
       the browser's [hidden] on a card whose visibility says no. */
    [hidden] { display: none !important; }
    .stack { display: flex; flex-direction: column; gap: 8px; }
    .message { display: block; padding: 12px 16px; color: var(--secondary-text-color); }
  `;

  /** One section of the CTC page: the page's own cards, as the integration
   *  answers for the heat pump and the section in the configuration. */
  class CtcEcoZenithSection extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      const style = document.createElement("style");
      style.textContent = STYLE;
      this._stack = document.createElement("div");
      this._stack.className = "stack";
      this._note = document.createElement("ha-card");
      this._note.className = "message";
      this._note.hidden = true;
      this.shadowRoot.append(style, this._stack, this._note);
      //: The request last sent, "<device_id>|<section>", and the answer last
      //: drawn, as JSON: an answer like the last leaves the cards alone, with
      //: their open notes and their search.
      this._asked = null;
      this._answer = null;
      this._cards = [];
      this._message = null;
      this._failedAt = 0;
      this._askedAt = 0;
      this._preview = false;
      this._unsubscribe = null;
      this._subscribing = false;
    }

    static getStubConfig(hass) {
      return stubConfig("section", hass);
    }

    static getConfigForm() {
      return configForm(pickerLanguage());
    }

    setConfig(config) {
      if (!config || !config.device_id) {
        throw new Error(`${SECTION_CARD} needs device_id`);
      }
      if (!SECTIONS.includes(config.section)) {
        throw new Error(
          `${SECTION_CARD} has no section "${config.section}"; the sections are `
            + SECTIONS.join(", ")
        );
      }
      this._config = { ...config };
      // Asked again whatever the configuration says now: the editor sends one
      // per change, and an answer like the last is not drawn again.
      this._asked = null;
      if (this._hass) this._ask();
    }

    set hass(hass) {
      this._hass = hass;
      for (const card of this._cards) card.element.hass = hass;
      if (this._config) {
        const stale = this._asked !== this._request();
        const retry = this._failedAt && Date.now() - this._failedAt >= RETRY_MS;
        if (stale || retry) this._ask();
      }
      this._subscribe();
      this._show();
    }

    get hass() {
      return this._hass;
    }

    /** Home Assistant's editor and card picker set this. The card then says so
     *  when the page has nothing in the section, rather than vanish. */
    set preview(value) {
      this._preview = Boolean(value);
      this._show();
    }

    get preview() {
      return this._preview;
    }

    /** Shown again, on a view opened anew: the section is asked for again, as
     *  the page is built when it is opened, unless it was just asked for. */
    connectedCallback() {
      if (this._config && this._hass && Date.now() - this._askedAt >= RETRY_MS) this._ask();
      this._subscribe();
    }

    disconnectedCallback() {
      const unsubscribe = this._unsubscribe;
      this._unsubscribe = null;
      if (unsubscribe) Promise.resolve().then(unsubscribe).catch(() => {});
    }

    getCardSize() {
      const size = this._cards.reduce((sum, card) => (
        sum + (card.element.getCardSize ? Number(card.element.getCardSize()) || 1 : 1)
      ), 0);
      return size || 1;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    _request() {
      return `${this._config.device_id}|${this._config.section}`;
    }

    _ask() {
      const asked = this._request();
      this._asked = asked;
      this._askedAt = Date.now();
      this._failedAt = 0;
      const request = {
        type: COMMAND, device_id: this._config.device_id, section: this._config.section,
      };
      Promise.resolve()
        .then(() => this._hass.callWS(request))
        .then(
          (answer) => { if (this._asked === asked) this._draw((answer && answer.cards) || []); },
          (err) => { if (this._asked === asked) this._fail(err); }
        );
    }

    /** Listen for the page changing, the way an open page does, and ask again
     *  then. Only while the card is on a page, and once. */
    _subscribe() {
      if (this._unsubscribe || this._subscribing || !this.isConnected || !this._config) return;
      const connection = this._hass && this._hass.connection;
      if (!connection || typeof connection.subscribeEvents !== "function") return;
      this._subscribing = true;
      Promise.resolve()
        .then(() => connection.subscribeEvents((event) => {
          const data = (event && event.data) || {};
          if (data.url_path === PAGE && this._config && this._hass) this._ask();
        }, "lovelace_updated"))
        .then(
          (unsubscribe) => {
            this._subscribing = false;
            if (this.isConnected) this._unsubscribe = unsubscribe;
            else Promise.resolve().then(unsubscribe).catch(() => {});
          },
          () => { this._subscribing = false; }
        );
    }

    /** The section's cards, each the page's own card with the page's config. */
    _draw(cards) {
      const answer = JSON.stringify(cards);
      if (answer === this._answer && this._message === null) return;
      const drawn = [];
      try {
        for (const config of cards) {
          const tag = String((config && config.type) || "").replace(/^custom:/, "");
          const Card = Object.values(PAGE_CARDS).includes(tag) ? customElements.get(tag) : undefined;
          if (!Card) continue;
          const element = new Card();
          element.setConfig(config);
          if (this._hass) element.hass = this._hass;
          drawn.push({ config, element });
        }
      } catch (err) {
        // A card that will not take what the page gave it: said in its words.
        this._fail(err);
        return;
      }
      this._answer = answer;
      this._message = null;
      this._cards = drawn;
      this._stack.replaceChildren(...drawn.map((card) => card.element));
      this._show();
    }

    _fail(err) {
      this._failedAt = Date.now();
      this._answer = null;
      this._cards = [];
      this._stack.replaceChildren();
      const words = WORDS[language(this._hass && this._hass.language)];
      const code = err && err.code;
      this._message = (code && words[code]) || (err && err.message) || words.failed;
      this._show();
    }

    /** Which cards are seen, by the visibility the page gave them, and what
     *  the card says in their place: a refusal always, and an empty section
     *  only in the editor, where a card that vanished could not be found. */
    _show() {
      const states = (this._hass && this._hass.states) || {};
      let seen = false;
      for (const card of this._cards) {
        const shown = visible(card.config.visibility, states);
        card.element.hidden = !shown;
        seen = seen || shown;
      }
      const answered = this._answer !== null;
      const words = WORDS[language(this._hass && this._hass.language)];
      let message = this._message;
      if (message === null && answered && !seen && this._preview) message = words.empty;
      this._stack.hidden = this._message !== null;
      this._note.hidden = message === null;
      const text = message === null ? "" : message;
      if (this._note.textContent !== text) this._note.textContent = text;
      this._hide(answered && !seen && message === null);
    }

    /** Hide the card or show it, and tell Lovelace, whose wrapper around a
     *  card decides from the card's own hidden whether it takes room. */
    _hide(hidden) {
      if (Boolean(this.hidden) === hidden) return;
      this.hidden = hidden;
      if (typeof this.dispatchEvent === "function" && typeof Event !== "undefined") {
        this.dispatchEvent(new Event("card-visibility-changed", { bubbles: true, composed: true }));
      }
    }
  }

  if (!customElements.get(SECTION_CARD)) customElements.define(SECTION_CARD, CtcEcoZenithSection);

  for (const [kind, tag] of Object.entries(PAGE_CARDS)) {
    const Card = customElements.get(tag);
    if (Card) Card.getStubConfig = (hass) => stubConfig(kind, hass);
  }

  const scope = typeof window !== "undefined" ? window : globalThis;
  scope.customCards = scope.customCards || [];
  const picker = PICKER[pickerLanguage()];
  const listed = [
    [SECTION_CARD, "section"],
    ...Object.entries(PAGE_CARDS).map(([kind, tag]) => [tag, kind]),
  ];
  for (const [type, kind] of listed) {
    if (scope.customCards.some((card) => card.type === type)) continue;
    const [name, description] = picker[kind];
    scope.customCards.push({ type, name, description, preview: true, documentationURL: DOCS });
  }
})();
