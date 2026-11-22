/* CTC Local Easyconf: the coefficient of performance beside the outdoor temperature.
 *
 * Served by the integration at /ctc_ecozenith/ctc-ecozenith-chart.js and loaded into
 * the frontend beside the page's cards. One card:
 *
 *   custom:ctc-ecozenith-cop-chart   the coefficient of performance on the left axis
 *                                    and the outdoor temperature in °C on the right,
 *                                    in one chart: over the last hours as lines
 *                                    ("history"), or a bar a day with the day's mean
 *                                    temperature as a line over them ("days")
 *
 * Home Assistant's own graphs cannot draw the two together: the history graph puts
 * each unit in a chart of its own, one under the other, and the statistics graph
 * draws both on one axis with no unit at all. On an air to water heat pump the
 * coefficient of performance follows the outdoor temperature closely, which is
 * what the two axes are for.
 *
 * The figures are read over the websocket as Home Assistant's own graphs read them:
 * history/history_during_period for the lines, recorder/statistics_during_period
 * for the days, and again every few minutes while the card is on the page. Every
 * string goes into the page as text, never as HTML.
 *
 * The pure helpers at the top are loaded into node by tests/test_card_chart.py, so
 * the closure hands them over and stops before the custom element when there is
 * no browser.
 */

(() => {
  //: How often the card reads its figures again while it is on the page.
  const REFRESH_MS = 5 * 60 * 1000;
  const HOUR_MS = 3600 * 1000;
  const DAY_MS = 24 * HOUR_MS;
  //: The chart's height and the room around the plot for the axes.
  const HEIGHT = 260;
  const PAD = { top: 22, right: 46, bottom: 26, left: 40 };
  //: The least span the temperature axis gets, in °C: a sensor that reads
  //: whole degrees steps from 0 to 1 over a day, and drawn over the whole
  //: height that looked like a change of weather.
  const MIN_TEMPERATURE_SPAN = 4;
  //: The colours, as close to Home Assistant's own chart palette as a card
  //: outside it can get: the coefficients in blue and yellow, the outdoor
  //: temperature in red, as the statistics graph drew them before.
  const COLOURS = ["#4ea8de", "#f2b632", "#9b7fe6", "#3fb68b"];
  const TEMPERATURE_COLOUR = "#e8553e";

  /* ---------------------------------------------------------- pure helpers */

  /** A number out of a state or a statistic, or null. */
  function numberOf(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  /** One entity's history, as history/history_during_period answers with a
   *  minimal response: [[time in ms, value or null], ...] in time order. A
   *  state that is no number (unknown, unavailable) is a gap, not a zero. */
  function historyPoints(answer, entityId) {
    const states = (answer && answer[entityId]) || [];
    const points = [];
    for (const state of states) {
      const seconds = numberOf(state.lu !== undefined ? state.lu : state.lc);
      if (seconds === null) continue;
      points.push([seconds * 1000, numberOf(state.s)]);
    }
    return points.sort((a, b) => a[0] - b[0]);
  }

  /** One statistic's days, as recorder/statistics_during_period answers:
   *  [[start of the day in ms, mean or null], ...]. */
  function dayPoints(answer, statisticId) {
    const rows = (answer && answer[statisticId]) || [];
    return rows
      .map((row) => [numberOf(row.start), numberOf(row.mean)])
      .filter((row) => row[0] !== null)
      .sort((a, b) => a[0] - b[0]);
  }

  /** Round steps for an axis from `low` to `high`, about `count` of them:
   *  {low, high, step, ticks}, the ends widened to whole steps. An axis with
   *  nothing on it, or one value only, still gets a span to draw in. */
  function niceScale(low, high, count = 5, zero = false) {
    if (low === null || high === null || !Number.isFinite(low) || !Number.isFinite(high)) {
      low = 0;
      high = 1;
    }
    if (zero) {
      low = Math.min(low, 0);
      high = Math.max(high, 0);
    }
    if (high - low < 1e-9) {
      low -= 1;
      high += 1;
    }
    const rough = (high - low) / Math.max(1, count);
    const power = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= rough) || 10 * power;
    const start = Math.floor(low / step + 1e-9) * step;
    const end = Math.ceil(high / step - 1e-9) * step;
    const ticks = [];
    for (let tick = start; tick <= end + step / 2; tick += step) ticks.push(Number(tick.toFixed(10)));
    return { low: Number(start.toFixed(10)), high: Number(end.toFixed(10)), step, ticks };
  }

  /** A scale for a second axis with exactly `steps` round steps, so that its
   *  labels stand at the heights of the first axis's grid lines and are round
   *  numbers too: the step grows until the values fit. */
  function alignedScale(low, high, steps) {
    if (low === null || high === null) {
      low = 0;
      high = 1;
    }
    steps = Math.max(1, steps);
    if (high - low < 1e-9) {
      low -= 1;
      high += 1;
    }
    const rough = (high - low) / steps;
    const power = Math.pow(10, Math.floor(Math.log10(rough)));
    for (const factor of [1, 2, 2.5, 5, 10, 20, 25, 50, 100]) {
      const step = factor * power;
      const start = Math.floor(low / step + 1e-9) * step;
      if (start + step * steps >= high - 1e-9) {
        const ticks = [];
        for (let i = 0; i <= steps; i++) ticks.push(Number((start + i * step).toFixed(10)));
        return { low: start, high: start + step * steps, step, ticks };
      }
    }
    return niceScale(low, high, steps);
  }

  /** `low` and `high` widened about their middle to at least `span`. */
  function widened(low, high, span) {
    if (low === null || high === null || high - low >= span) return [low, high];
    const middle = (low + high) / 2;
    return [middle - span / 2, middle + span / 2];
  }

  /** How many decimals an axis's labels need for its step to read true. */
  function decimalsFor(step) {
    for (let digits = 0; digits < 4; digits++) {
      if (Math.abs(step * Math.pow(10, digits) - Math.round(step * Math.pow(10, digits))) < 1e-6) {
        return digits;
      }
    }
    return 4;
  }

  /** The smallest and largest number among the points' values, or nulls. */
  function extent(series) {
    let low = null;
    let high = null;
    for (const points of series) {
      for (const [, value] of points) {
        if (value === null) continue;
        low = low === null ? value : Math.min(low, value);
        high = high === null ? value : Math.max(high, value);
      }
    }
    return [low, high];
  }

  /** An SVG path of a history as steps: a value holds until the next one, and
   *  a gap (null) lifts the pen. `x` and `y` map time and value to pixels; the
   *  last value runs on to `end`, the moment the chart ends. */
  function stepPath(points, x, y, end) {
    let path = "";
    let pen = false;
    points.forEach(([time, value], index) => {
      const next = index + 1 < points.length ? points[index + 1][0] : end;
      if (value === null) {
        pen = false;
        return;
      }
      const px = x(time).toFixed(1);
      const py = y(value).toFixed(1);
      path += pen ? `L${px},${py}` : `M${px},${py}`;
      path += `L${x(Math.min(next, end)).toFixed(1)},${py}`;
      pen = true;
    });
    return path;
  }

  /** An SVG path through the days' middles, lifting the pen over a missing day. */
  function dayLine(points, x, y) {
    let path = "";
    let pen = false;
    for (const [time, value] of points) {
      if (value === null) {
        pen = false;
        continue;
      }
      path += `${pen ? "L" : "M"}${x(time).toFixed(1)},${y(value).toFixed(1)}`;
      pen = true;
    }
    return path;
  }

  /** The value a history held at `time`: the last one at or before it, or null. */
  function valueAt(points, time) {
    let found = null;
    for (const [at, value] of points) {
      if (at > time) break;
      found = value;
    }
    return found;
  }

  /** A figure as the card writes it: decimals by size, the locale's comma. */
  function figure(value, locale, digits) {
    if (value === null || value === undefined) return "–";
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  }

  /** The start of the local day `days` before the one `now` is in. */
  function dayStart(now, days) {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - days);
    return start.getTime();
  }

  /** The start of the local day `days` after the one `start` begins: by the
   *  calendar, since a day is 23 or 25 hours where the clock is put back or
   *  forward, and 24 hours a day from late October landed on the evening before. */
  function dayAfter(start, days) {
    const day = new Date(start);
    day.setDate(day.getDate() + days);
    day.setHours(0, 0, 0, 0);
    return day.getTime();
  }

  if (typeof customElements === "undefined") {
    // Node, for tests/test_card_chart.py: hand over the helpers and stop here.
    if (typeof module !== "undefined") {
      module.exports = {
        historyPoints, dayPoints, niceScale, alignedScale, widened, decimalsFor, extent, stepPath, dayLine, valueAt,
        figure, dayStart, dayAfter,
      };
    }
    return;
  }

  /* --------------------------------------------------------------- the card */

  const SVG = "http://www.w3.org/2000/svg";

  function svg(tag, attributes = {}) {
    const element = document.createElementNS(SVG, tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    return element;
  }

  const STYLE = `
    :host { display: block; }
    ha-card { padding: 12px 12px 8px; overflow: hidden; }
    .chart { position: relative; }
    svg { display: block; width: 100%; overflow: visible; }
    .grid { stroke: var(--divider-color, rgba(127,127,127,.25)); stroke-width: 1; }
    .tick { fill: var(--secondary-text-color); font-size: 11px; font-variant-numeric: tabular-nums; }
    .unit { fill: var(--secondary-text-color); font-size: 11px; }
    .cursor { stroke: var(--secondary-text-color); stroke-width: 1; stroke-dasharray: 3 3; }
    .legend { display: flex; flex-wrap: wrap; justify-content: center; gap: 4px 16px;
              margin-top: 6px; font-size: 13px; color: var(--primary-text-color); }
    .legend span { display: inline-flex; align-items: center; gap: 6px; }
    .legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; }
    .legend .line i { height: 3px; border-radius: 2px; }
    .legend b { font-weight: 400; color: var(--secondary-text-color);
                font-variant-numeric: tabular-nums; }
    .tip { position: absolute; pointer-events: none; padding: 6px 8px; border-radius: 6px;
           background: var(--card-background-color, #222); color: var(--primary-text-color);
           border: 1px solid var(--divider-color, #444); font-size: 12px; white-space: nowrap;
           box-shadow: 0 2px 8px rgba(0,0,0,.3); font-variant-numeric: tabular-nums; }
    .tip div { display: flex; gap: 6px; align-items: center; }
    .tip i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
    .empty { color: var(--secondary-text-color); padding: 24px 0; text-align: center; }
  `;

  class CtcEcoZenithCopChart extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._data = null;
      this._width = 0;
      this._timer = null;
      this._asked = 0;
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.series) || !config.series.length) {
        throw new Error("ctc-ecozenith-cop-chart needs series");
      }
      this._config = config;
      this._data = null;
      this._asked = 0;
      this._draw();
      if (this._hass) this._read();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) this._read();
    }

    get hass() {
      return this._hass;
    }

    getCardSize() {
      return 6;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    connectedCallback() {
      if (!this._observer && typeof ResizeObserver !== "undefined") {
        this._observer = new ResizeObserver(() => {
          const width = Math.round(this.clientWidth);
          if (width && width !== this._width) {
            this._width = width;
            this._draw();
          }
        });
      }
      if (this._observer) this._observer.observe(this);
      if (!this._timer) this._timer = setInterval(() => this._read(), REFRESH_MS);
      if (this._hass && Date.now() - this._asked > REFRESH_MS) this._read();
    }

    disconnectedCallback() {
      if (this._observer) this._observer.disconnect();
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
    }

    get _days() {
      return this._config.mode === "days";
    }

    get _locale() {
      const locale = this._hass && this._hass.locale;
      return (locale && locale.language) || (this._hass && this._hass.language) || undefined;
    }

    /** Ask Home Assistant for the figures the chart draws, as its own graphs do. */
    async _read() {
      if (!this._hass || !this._config) return;
      this._asked = Date.now();
      const ids = this._config.series.map((s) => s.entity);
      const now = Date.now();
      try {
        if (this._days) {
          const days = Number(this._config.days) || 30;
          const start = dayStart(now, days - 1);
          const answer = await this._hass.callWS({
            type: "recorder/statistics_during_period",
            start_time: new Date(start).toISOString(),
            end_time: new Date(now).toISOString(),
            statistic_ids: ids,
            period: "day",
            types: ["mean"],
          });
          this._data = {
            start,
            end: dayStart(now, -1),
            series: this._config.series.map((s) => dayPoints(answer, s.entity)),
          };
        } else {
          const hours = Number(this._config.hours) || 24;
          const start = now - hours * HOUR_MS;
          const answer = await this._hass.callWS({
            type: "history/history_during_period",
            start_time: new Date(start).toISOString(),
            end_time: new Date(now).toISOString(),
            entity_ids: ids,
            minimal_response: true,
            no_attributes: true,
            significant_changes_only: false,
          });
          this._data = {
            start,
            end: now,
            series: this._config.series.map((s) => historyPoints(answer, s.entity)),
          };
        }
      } catch (err) {
        this._data = { error: String((err && err.message) || err) };
      }
      this._draw();
    }

    _draw() {
      if (!this._config) return;
      const style = document.createElement("style");
      style.textContent = STYLE;
      const card = document.createElement("ha-card");
      this.shadowRoot.replaceChildren(style, card);
      const data = this._data;
      const empty = this._config.empty || "No values to show yet.";
      if (!data || data.error || !data.series.some((p) => p.some(([, v]) => v !== null))) {
        const note = document.createElement("div");
        note.className = "empty";
        note.textContent = data && data.error ? `${empty} (${data.error})` : data ? empty : "…";
        card.appendChild(note);
        return;
      }
      const width = Math.max(240, this._width || this.clientWidth || 600) - 24;
      const holder = document.createElement("div");
      holder.className = "chart";
      card.appendChild(holder);
      this._plot(holder, width, data);
      card.appendChild(this._legend(data));
    }

    _plot(holder, width, data) {
      const config = this._config;
      const cop = [];
      const temperature = [];
      config.series.forEach((s, index) => {
        (s.axis === "temperature" ? temperature : cop).push({ s, points: data.series[index], index });
      });
      const copScale = niceScale(...extent(cop.map((c) => c.points)), 5, this._days);
      const tempScale = alignedScale(
        ...widened(...extent(temperature.map((c) => c.points)), MIN_TEMPERATURE_SPAN),
        copScale.ticks.length - 1
      );
      const plotWidth = width - PAD.left - PAD.right;
      const plotHeight = HEIGHT - PAD.top - PAD.bottom;
      const days = this._days;
      const dayCount = days ? Math.max(1, Math.round((data.end - data.start) / DAY_MS)) : 0;
      const slot = days ? plotWidth / dayCount : 0;
      const x = days
        ? (time) => PAD.left + (Math.round((time - data.start) / DAY_MS) + 0.5) * slot
        : (time) => PAD.left + ((time - data.start) / (data.end - data.start)) * plotWidth;
      const yOf = (scale) => (value) =>
        PAD.top + plotHeight - ((value - scale.low) / (scale.high - scale.low)) * plotHeight;
      const yCop = yOf(copScale);
      const yTemp = yOf(tempScale);
      const locale = this._locale;

      const chart = svg("svg", { viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT, role: "img" });
      chart.setAttribute("aria-label", this._config.title || "");
      // The grid and the left axis follow the coefficient's steps; the right
      // axis writes the temperature at the same heights.
      copScale.ticks.forEach((tick) => {
        const py = yCop(tick);
        chart.appendChild(svg("line", { class: "grid", x1: PAD.left, x2: PAD.left + plotWidth, y1: py, y2: py }));
        const label = svg("text", { class: "tick", x: PAD.left - 6, y: py + 4, "text-anchor": "end" });
        label.textContent = figure(tick, locale, decimalsFor(copScale.step));
        chart.appendChild(label);
      });
      if (temperature.length) {
        for (const value of tempScale.ticks) {
          const label = svg("text", {
            class: "tick", x: PAD.left + plotWidth + 6, y: yTemp(value) + 4, "text-anchor": "start",
          });
          label.textContent = figure(value, locale, decimalsFor(tempScale.step));
          chart.appendChild(label);
        }
      }
      const left = svg("text", { class: "unit", x: PAD.left - 6, y: 12, "text-anchor": "end" });
      left.textContent = config.cop_unit || "COP";
      chart.appendChild(left);
      if (temperature.length) {
        const right = svg("text", { class: "unit", x: PAD.left + plotWidth + 6, y: 12, "text-anchor": "start" });
        right.textContent = config.temperature_unit || "°C";
        chart.appendChild(right);
      }

      // The time axis: every few hours, or every few days.
      const ticks = [];
      if (days) {
        const every = Math.max(1, Math.ceil(dayCount / Math.max(2, Math.floor(plotWidth / 70))));
        for (let i = dayCount - 1; i >= 0; i -= every) ticks.push(dayAfter(data.start, i));
      } else {
        const span = data.end - data.start;
        const every = [1, 2, 3, 4, 6, 12].map((h) => h * HOUR_MS)
          .find((s) => plotWidth / (span / s) >= 60) || 12 * HOUR_MS;
        const first = Math.ceil(data.start / every) * every;
        for (let t = first; t <= data.end; t += every) ticks.push(t);
      }
      const dateFormat = new Intl.DateTimeFormat(locale, days
        ? { day: "numeric", month: "short" }
        : { hour: "2-digit", minute: "2-digit" });
      for (const tick of ticks) {
        const label = svg("text", { class: "tick", x: x(tick), y: HEIGHT - 6, "text-anchor": "middle" });
        label.textContent = dateFormat.format(new Date(tick));
        chart.appendChild(label);
      }

      // The coefficients: bars side by side a day, or steps over the hours.
      if (days) {
        const width1 = Math.max(2, (slot * 0.72) / Math.max(1, cop.length));
        const base = yCop(Math.max(copScale.low, 0));
        cop.forEach((c, n) => {
          for (const [time, value] of c.points) {
            if (value === null) continue;
            const px = x(time) - (width1 * cop.length) / 2 + n * width1;
            const py = yCop(value);
            chart.appendChild(svg("rect", {
              x: px.toFixed(1), y: Math.min(py, base).toFixed(1), width: (width1 * 0.9).toFixed(1),
              height: Math.max(1, Math.abs(base - py)).toFixed(1), rx: 1.5,
              fill: COLOURS[n % COLOURS.length], "fill-opacity": 0.85,
            }));
          }
        });
      } else {
        cop.forEach((c, n) => {
          chart.appendChild(svg("path", {
            d: stepPath(c.points, x, yCop, data.end), fill: "none",
            stroke: COLOURS[n % COLOURS.length], "stroke-width": 2, "stroke-linejoin": "round",
          }));
        });
      }
      // The outdoor temperature over them, on its own axis.
      for (const c of temperature) {
        chart.appendChild(svg("path", {
          d: days ? dayLine(c.points, x, yTemp) : stepPath(c.points, x, yTemp, data.end),
          fill: "none", stroke: TEMPERATURE_COLOUR, "stroke-width": 2, "stroke-linejoin": "round",
        }));
        if (days) {
          for (const [time, value] of c.points) {
            if (value === null) continue;
            chart.appendChild(svg("circle", { cx: x(time), cy: yTemp(value), r: 2.5, fill: TEMPERATURE_COLOUR }));
          }
        }
      }

      const cursor = svg("line", { class: "cursor", y1: PAD.top, y2: PAD.top + plotHeight, visibility: "hidden" });
      chart.appendChild(cursor);
      holder.appendChild(chart);
      const tip = document.createElement("div");
      tip.className = "tip";
      tip.hidden = true;
      holder.appendChild(tip);

      const timeFormat = new Intl.DateTimeFormat(locale, days
        ? { weekday: "short", day: "numeric", month: "short" }
        : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      const show = (event) => {
        const box = chart.getBoundingClientRect();
        const scale = width / Math.max(1, box.width);
        const px = (event.clientX - box.left) * scale;
        if (px < PAD.left || px > PAD.left + plotWidth) {
          hide();
          return;
        }
        let time;
        if (days) {
          const index = Math.min(dayCount - 1, Math.max(0, Math.floor((px - PAD.left) / slot)));
          time = dayAfter(data.start, index);
        } else {
          time = data.start + ((px - PAD.left) / plotWidth) * (data.end - data.start);
        }
        const at = days ? x(time) : px;
        cursor.setAttribute("x1", at);
        cursor.setAttribute("x2", at);
        cursor.setAttribute("visibility", "visible");
        tip.replaceChildren();
        const head = document.createElement("div");
        head.textContent = timeFormat.format(new Date(time));
        tip.appendChild(head);
        config.series.forEach((s, index) => {
          const points = data.series[index];
          const value = days
            ? ((points.find(([t]) => Math.round((t - time) / DAY_MS) === 0) || [])[1] ?? null)
            : valueAt(points, time);
          const row = document.createElement("div");
          const dot = document.createElement("i");
          dot.style.background = this._colour(s, index);
          const temperatureRow = s.axis === "temperature";
          row.append(dot, `${s.name || s.entity}: ${figure(value, locale, temperatureRow ? 1 : 2)}${
            temperatureRow && value !== null ? ` ${config.temperature_unit || "°C"}` : ""}`);
          tip.appendChild(row);
        });
        tip.hidden = false;
        const left = (at / scale) + 12;
        const room = box.width - tip.offsetWidth - 4;
        tip.style.left = `${Math.max(0, left > room ? at / scale - tip.offsetWidth - 12 : left)}px`;
        tip.style.top = `${PAD.top / scale}px`;
      };
      const hide = () => {
        cursor.setAttribute("visibility", "hidden");
        tip.hidden = true;
      };
      chart.addEventListener("pointermove", show);
      chart.addEventListener("pointerdown", show);
      chart.addEventListener("pointerleave", hide);
    }

    _colour(series, index) {
      if (series.axis === "temperature") return TEMPERATURE_COLOUR;
      const n = this._config.series.slice(0, index).filter((s) => s.axis !== "temperature").length;
      return COLOURS[n % COLOURS.length];
    }

    /** The names under the chart, each with its latest figure. */
    _legend(data) {
      const legend = document.createElement("div");
      legend.className = "legend";
      const locale = this._locale;
      this._config.series.forEach((s, index) => {
        const item = document.createElement("span");
        const temperature = s.axis === "temperature";
        if (temperature || !this._days) item.className = "line";
        const swatch = document.createElement("i");
        swatch.style.background = this._colour(s, index);
        const points = data.series[index].filter(([, v]) => v !== null);
        const last = points.length ? points[points.length - 1][1] : null;
        const value = document.createElement("b");
        value.textContent = `${figure(last, locale, temperature ? 1 : 2)}${
          temperature && last !== null ? ` ${this._config.temperature_unit || "°C"}` : ""}`;
        item.append(swatch, s.name || s.entity, value);
        legend.appendChild(item);
      });
      return legend;
    }
  }

  if (!customElements.get("ctc-ecozenith-cop-chart")) {
    customElements.define("ctc-ecozenith-cop-chart", CtcEcoZenithCopChart);
  }
})();
