/*
 * EV Charge Director card
 * Wordt automatisch geladen door de integratie ev_charge_director.
 *
 *   type: custom:ev-charge-director-card
 *   entry_id: <optioneel, alleen bij meerdere laadpunten>
 *   title: CHARGE CONTROL        # optioneel
 *
 * Breed (panel-weergave): drie kolommen met nu / slim laden & prijs / historie.
 * Smal: alles onder elkaar. Alles is altijd zichtbaar; klik op een waarde
 * om de bijbehorende entiteit te openen.
 */
const DOMAIN = "ev_charge_director";
const VERSION = "0.2.0";

const CAT = {
  disconnected: "#9ca3af",
  connected: "#60a5fa",
  charging: "#22c55e",
  paused: "#f59e0b",
  finished: "#60a5fa",
  error: "#ef4444",
  unknown: "#6b7280",
};
const CAR_COLORS = ["#22c55e", "#60a5fa", "#f59e0b", "#a78bfa", "#f472b6", "#2dd4bf"];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (v) => v !== null && v !== undefined && v !== "" && !isNaN(v);
const num = (v, d = 2) => (isNum(v) ? Number(v).toFixed(d) : "–");
const eur = (v, d = 2) => (isNum(v) ? "€" + Number(v).toFixed(d) : "–");
const hm = (t) => {
  if (!t) return "–";
  const d = new Date(t);
  return isNaN(d) ? "–" : d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
};
const dayHm = (t) => {
  if (!t) return "–";
  const d = new Date(t);
  if (isNaN(d)) return "–";
  const today = new Date();
  const tomorrow = new Date(Date.now() + 864e5);
  const yesterday = new Date(Date.now() - 864e5);
  if (d.toDateString() === today.toDateString()) return `vandaag ${hm(d)}`;
  if (d.toDateString() === tomorrow.toDateString()) return `morgen ${hm(d)}`;
  if (d.toDateString() === yesterday.toDateString()) return `gisteren ${hm(d)}`;
  return d.toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" }) + " " + hm(d);
};
const dur = (min) => {
  if (!isNum(min)) return "–";
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h > 0 ? `${h}u ${m}m` : `${m} min`;
};
const ymOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const monthShort = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("nl-NL", { month: "short" }).replace(".", "");
};
const monthLong = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("nl-NL", { month: "long", year: "numeric" });
};
const parseLocal = (s) => {
  if (!s) return null;
  const d = new Date(String(s).replace(" ", "T"));
  return isNaN(d) ? null : d;
};

class EVChargeDirectorCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._error = null;
    this._unsub = null;
    this._lastMsg = 0;
    this._pendingRender = false;
    this._cols = 1;
    this._hist = { now: null, nowKey: null, nowFetched: 0, last: null, lastKey: null };
    this._bind();
  }

  static getStubConfig() {
    return {};
  }
  static getConfigElement() {
    return document.createElement("ev-charge-director-card-editor");
  }
  setConfig(config) {
    this._config = { title: "CHARGE CONTROL", ...(config || {}) };
    if (this._unsub) this._resubscribe();
  }
  getCardSize() {
    return 12;
  }
  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || (this._lastMsg && Date.now() - this._lastMsg > 120000)) this._resubscribe();
    // autogegevens komen rechtstreeks uit de states: bij elke update verversen
    if (this._shell && this._data) this._setSection("carbox", this._carBox(this._data));
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._resubscribe();
    if (!this._ro) this._ro = new ResizeObserver(() => this._layout());
    this._ro.observe(this);
    clearInterval(this._timer);
    this._timer = setInterval(() => this._refreshGraphs(), 60000);
  }
  disconnectedCallback() {
    this._unsubscribe();
    this._ro?.disconnect();
    clearInterval(this._timer);
  }

  _layout() {
    const w = this.clientWidth || 0;
    const cols = w >= 1150 ? 3 : w >= 760 ? 2 : 1;
    if (cols !== this._cols) {
      this._cols = cols;
      const card = this.shadowRoot.querySelector("ha-card");
      if (card) card.dataset.cols = String(cols);
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      const u = this._unsub;
      this._unsub = null;
      Promise.resolve(u).then((fn) => fn && fn()).catch(() => {});
    }
  }
  _resubscribe() {
    if (!this._hass?.connection) return;
    this._unsubscribe();
    this._lastMsg = Date.now();
    const msg = { type: `${DOMAIN}/subscribe` };
    if (this._config?.entry_id) msg.entry_id = this._config.entry_id;
    this._unsub = this._hass.connection
      .subscribeMessage((data) => {
        this._lastMsg = Date.now();
        this._error = null;
        if (!this._data) this._shell = null;
        this._data = data;
        this._render();
        this._refreshGraphs();
      }, msg)
      .catch((err) => {
        this._unsub = null;
        this._error = err?.message || "Kan EV Charge Director niet bereiken";
        this._render();
      });
  }

  _call(service, data = {}) {
    if (!this._hass || !this._data) return;
    return this._hass
      .callService(DOMAIN, service, { config_entry_id: this._data.entry_id, ...data })
      .catch((e) => this._toast(e?.message || String(e)));
  }
  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }
  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }
  _e(key) {
    return this._data?.entities?.[key] || "";
  }
  _src(key) {
    return this._data?.sources?.[key] || "";
  }

  // ------------------------------------------------------------- history
  async _fetchHistory(entityId, start, end) {
    if (!entityId || !this._hass?.callWS) return [];
    const res = await this._hass.callWS({
      type: "history/history_during_period",
      start_time: start.toISOString(),
      end_time: end.toISOString(),
      entity_ids: [entityId],
      minimal_response: true,
      no_attributes: true,
      significant_changes_only: false,
    });
    const unit = String(this._hass.states?.[entityId]?.attributes?.unit_of_measurement || "").toLowerCase();
    const f = unit === "w" ? 0.001 : 1;
    return (res?.[entityId] || [])
      .map((p) => ({ t: (p.lu ?? p.lc) * 1000, v: parseFloat(p.s) * f }))
      .filter((p) => isNum(p.v) && isNum(p.t));
  }

  _powerEntity() {
    return this._src("power") || this._e("power");
  }

  async _refreshGraphs() {
    const d = this._data;
    if (!d || !this._shell || this._fetching) return;
    this._fetching = true;
    try {
      const ent = this._powerEntity();
      const s = d.session;
      const start = s.active && s.start ? new Date(s.start) : new Date(Date.now() - 6 * 3600e3);
      const nowKey = s.active ? s.start : "6h";
      if (nowKey !== this._hist.nowKey || Date.now() - this._hist.nowFetched > 55000) {
        this._hist.nowKey = nowKey;
        this._hist.nowFetched = Date.now();
        let pts = [];
        try {
          pts = await this._fetchHistory(ent, start, new Date());
        } catch (e) {}
        this._hist.now = { pts, t0: start.getTime(), t1: Date.now() };
        this._setSection("nowgraph", this._nowGraph());
      }
      const l = d.last_session;
      const lastKey = l ? `${l.start}|${l.end}` : null;
      if (lastKey && lastKey !== this._hist.lastKey) {
        this._hist.lastKey = lastKey;
        const a = new Date(l.start);
        const b = new Date(l.end);
        let pts = [];
        try {
          pts = await this._fetchHistory(ent, a, b);
        } catch (e) {}
        this._hist.last = { pts, t0: a.getTime(), t1: b.getTime() };
        this._setSection("lastgraph", this._lastGraph());
      }
    } finally {
      this._fetching = false;
    }
  }

  // --------------------------------------------------------------- render
  _setSection(k, html) {
    const s = this._shell?.[k];
    if (s && s.html !== html) {
      s.el.innerHTML = html;
      s.html = html;
    }
  }

  _render() {
    const root = this.shadowRoot;
    if (this._error && !this._data) {
      this._shell = null;
      root.innerHTML = `${this._styles()}<ha-card><div class="err">${esc(this._error)}<br><small>Is de integratie EV Charge Director ingesteld?</small></div></ha-card>`;
      return;
    }
    if (!this._data) {
      this._shell = null;
      root.innerHTML = `${this._styles()}<ha-card><div class="err">Laden…</div></ha-card>`;
      return;
    }
    if (!this._shell) {
      root.innerHTML = `${this._styles()}
        <ha-card data-cols="${this._cols}">
          <div class="charge-bg"></div>
          <div class="cols">
            <div class="col">
              <div data-s="hero"></div><div data-s="power"></div><div data-s="tiles"></div>
              <div data-s="buttons"></div>
              <div data-s="carbox"></div>
              <div class="bh">Vermogen <span data-s="nowlabel"></span></div><div data-s="nowgraph"></div>
            </div>
            <div class="col">
              <div data-s="prices"></div>
              <div data-s="plan"></div>
            </div>
            <div class="col">
              <div data-s="last"></div><div data-s="lastgraph"></div>
              <div data-s="months"></div>
              <div data-s="sessions"></div>
            </div>
          </div>
        </ha-card>`;
      this._shell = {};
      root.querySelectorAll("[data-s]").forEach((el) => (this._shell[el.dataset.s] = { el, html: null }));
      this._hist.nowKey = null;
      this._hist.lastKey = null;
      requestAnimationFrame(() => this._layout());
    }
    const d = this._data;
    const cat = d.status?.category || "unknown";
    root.querySelector("ha-card").style.setProperty("--accent", CAT[cat] || CAT.unknown);

    const editing = root.activeElement && root.activeElement.tagName === "INPUT";
    this._pendingRender = editing;
    const sections = {
      hero: this._hero(d),
      power: this._powerState(d),
      tiles: this._tiles(d),
      buttons: this._buttons(d),
      carbox: this._carBox(d),
      nowlabel: d.session.active ? `· sessie sinds ${hm(d.session.start)}` : "· laatste 6 uur",
      nowgraph: this._nowGraph(),
      prices: this._prices(d),
      last: this._last(d),
      lastgraph: this._lastGraph(),
      months: this._months(d),
      sessions: this._sessions(d),
    };
    if (!editing) sections.plan = this._plan(d);
    for (const [k, html] of Object.entries(sections)) this._setSection(k, html);
  }

  _hero(d) {
    const car = d.cars.find((c) => c.name === d.car);
    const soc = car && isNum(car.soc) ? `<div class="soc">${Math.round(car.soc)}%</div>` : "";
    return `
      <div class="hero">
        <div class="title">${esc(this._config.title)}</div>
        <div class="ringwrap" data-entity="${esc(this._e("car"))}">
          <div class="charge-ring"></div>
          <ha-icon icon="mdi:car-electric" class="caricon"></ha-icon>
          <div class="carname">${esc(d.car)}</div>
          ${soc}
        </div>
      </div>`;
  }

  _powerState(d) {
    const p = d.power_kw;
    const width = isNum(p) ? Math.min((p / (d.max_power_kw || 11)) * 100, 100) : 0;
    return `<div class="hero">
        <div class="power" data-entity="${esc(this._e("power"))}">${num(p, 2)} <span>kW</span></div>
        <div class="powerbar"><div class="powerfill" style="width:${width}%"></div></div>
        <div class="state" data-entity="${esc(this._e("status"))}">● ${esc(d.status?.label)}</div>
      </div>`;
  }

  _tile(name, value, entity, cls = "") {
    return `<div class="tile ${cls} ${entity ? "click" : ""}" ${entity ? `data-entity="${esc(entity)}"` : ""}>
      <div class="tn">${name}</div><div class="tv">${value}</div></div>`;
  }

  _tiles(d) {
    const s = d.session;
    return `
      <div class="grid2">
        ${this._tile("Starttijd", s.active ? hm(s.start) : "–", this._e("session_start"))}
        ${this._tile("Prijs", isNum(d.price) ? eur(d.price, 3) + "/kWh" : "–", this._e("price"))}
        ${this._tile("Opgeladen", num(s.kwh, 2) + " kWh", this._e("session_energy"))}
        ${this._tile("Sessiekosten", eur(s.cost), this._e("session_cost"))}
        ${this._tile("Duur", s.active ? dur(s.duration_min) : "–", this._e("session_active"))}
        ${this._tile("Gem. prijs", s.active && s.kwh > 0 ? eur(s.cost / s.kwh, 3) + "/kWh" : "–", this._e("session_cost"))}
      </div>`;
  }

  _buttons(d) {
    const startOk = d.has_start && d.can_start;
    const stopOk = d.has_stop && d.can_stop;
    return `
      <div class="buttons">
        <button class="btn" data-act="start" ${startOk ? "" : "disabled"}><ha-icon icon="mdi:play-circle-outline"></ha-icon>Start</button>
        <button class="btn" data-act="car"><ha-icon icon="mdi:car-cog"></ha-icon>Auto</button>
        <button class="btn" data-act="stop" ${stopOk ? "" : "disabled"}><ha-icon icon="mdi:stop-circle-outline"></ha-icon>Stop</button>
      </div>`;
  }

  // ------------------------------------------------------------ auto-vak
  _fmtState(st) {
    if (!st) return "–";
    try {
      if (this._hass.formatEntityState) return this._hass.formatEntityState(st);
    } catch (e) {}
    const u = st.attributes?.unit_of_measurement;
    return `${st.state}${u ? " " + u : ""}`;
  }
  _shortName(st, carName) {
    let n = st?.attributes?.friendly_name || st?.entity_id || "";
    const re = new RegExp("^" + carName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[\\s:_-]*", "i");
    n = n.replace(re, "");
    return n.charAt(0).toUpperCase() + n.slice(1);
  }
  _carBox(d) {
    const car = d.cars.find((c) => c.name === d.car);
    if (!car || (!car.soc_entity && !(car.extra || []).length)) return "";
    const states = this._hass?.states || {};
    let soc = "";
    if (car.soc_entity) {
      const st = states[car.soc_entity];
      const v = st ? parseFloat(st.state) : NaN;
      const target = d.smart?.target_soc;
      soc = `<div class="socbox click" data-entity="${esc(car.soc_entity)}">
          <div class="socval">${isNum(v) ? Math.round(v) + "%" : "–"}</div>
          <div class="socbar"><div class="socfill" style="width:${isNum(v) ? Math.min(v, 100) : 0}%"></div>
            ${car.capacity_kwh && isNum(target) ? `<div class="soctarget" style="left:${target}%" title="laaddoel ${target}%"></div>` : ""}</div>
          <div class="rs">${
            car.capacity_kwh && isNum(v) ? `≈ ${num((v / 100) * car.capacity_kwh, 1)} van ${num(car.capacity_kwh, 0)} kWh` : "accu"
          }</div>
        </div>`;
    }
    const extras = (car.extra || [])
      .map((eid) => {
        const st = states[eid];
        const icon = st?.attributes?.icon || "";
        return `<div class="xrow click" data-entity="${esc(eid)}">
          ${icon ? `<ha-icon icon="${esc(icon)}"></ha-icon>` : ""}
          <span class="xn">${esc(st ? this._shortName(st, car.name) : eid)}</span>
          <span class="xv">${esc(st ? this._fmtState(st) : "niet gevonden")}</span></div>`;
      })
      .join("");
    return `<div class="bh big">${esc(car.name)}</div><div class="carbox">${soc}${extras ? `<div class="xlist">${extras}</div>` : ""}</div>`;
  }

  // ----------------------------------------------------------- slim laden
  _plan(d) {
    const sm = d.smart;
    const p = d.plan || {};
    const car = d.cars.find((c) => c.name === d.car) || {};
    const socMode = isNum(car.soc) && car.capacity_kwh;
    const target = socMode
      ? `<div class="stepper" data-kind="soc"><button data-step="-5">−</button><span data-entity="${esc(
          this._e("target_soc")
        )}">${sm.target_soc}%</span><button data-step="5">+</button></div>`
      : `<div class="stepper" data-kind="kwh"><button data-step="-1">−</button><span data-entity="${esc(
          this._e("target_energy")
        )}">${num(sm.target_kwh, 1)} kWh</span><button data-step="1">+</button></div>`;

    const toggle = `
      <div class="bh big">Slim laden</div>
      <div class="row">
        <div class="click" data-entity="${esc(this._e("smart_charging"))}"><div class="rs">${
          sm.enabled
            ? "Aan – laadt in de goedkoopste blokken vóór de deadline"
            : "Uit – laden start direct bij aansluiten. De grafiek laat zien wat de planner zou doen."
        }</div></div>
        <button class="switch ${sm.enabled ? "on" : ""}" data-act="toggle-smart"><span></span></button>
      </div>`;

    const settings = `
      <div class="grid2">
        <div class="tile"><div class="tn click" data-entity="${esc(this._e("ready_time"))}">Klaar om</div>
          <input type="time" class="timein" value="${esc(sm.ready_time)}"></div>
        <div class="tile"><div class="tn">${socMode ? "Laden tot" : "Erbij laden"}</div>${target}</div>
      </div>`;

    const reasons = {
      cheap_now: ["Nu laden: goedkoop blok", "ok"],
      deadline_close: ["Nu laden: deadline komt dichtbij", "ok"],
      deadline_passed: ["Deadline verstreken – doorladen tot het doel", ""],
      no_prices: ["Geen prijsdata – direct laden", ""],
      override_on: ["Handmatig gestart – laadt door", ""],
      override_off: ["Handmatig gestopt", ""],
      waiting_cheap: ["Wacht op goedkoper blok", ""],
    };
    let msg = "";
    let cls = "";
    if (!sm.enabled) msg = "";
    else if (p.mode === "waiting") msg = "Sluit de auto aan om te plannen";
    else if (p.mode === "done") [msg, cls] = ["Laaddoel bereikt ✓", "ok"];
    else if (reasons[p.reason]) [msg, cls] = reasons[p.reason];
    const override = sm.override ? `<button class="link" data-act="resume">Planning hervatten</button>` : "";

    const facts = `
      <div class="grid3">
        ${this._tile(p.preview ? "Zou starten" : "Volgende start", p.next_start ? dayHm(p.next_start) : "–", this._e("plan_next_start"))}
        ${this._tile(
          "Nog nodig",
          isNum(p.needed_kwh) ? `${num(p.needed_kwh, 1)} kWh<small>${dur(p.needed_minutes)}</small>` : "–",
          this._e("plan_needed_energy")
        )}
        ${this._tile("Verwacht", isNum(p.expected_cost) ? eur(p.expected_cost) : "–", this._e("plan_expected_cost"))}
      </div>
      ${p.insufficient ? `<div class="warn">Niet genoeg tijd of prijsdata voor het hele doel; er wordt zo veel mogelijk geladen.</div>` : ""}
      ${p.deadline ? `<div class="deadline click" data-entity="${esc(this._e("plan_status"))}">Klaar ${dayHm(p.deadline)}</div>` : ""}`;

    return `${toggle}${settings}
      ${msg || override ? `<div class="planmsg ${cls}">${esc(msg)} ${override}</div>` : ""}
      ${p.mode === "done" ? "" : facts}`;
  }

  _prices(d) {
    const p = d.plan || {};
    const slots = p.slots || [];
    const head = `<div class="bh big click" data-entity="${esc(this._e("price"))}">Stroomprijs <span>· nu ${eur(d.price, 3)}/kWh</span></div>`;
    if (!slots.length)
      return `${head}<div class="rs center">Geen prijsvoorspelling gevonden. Koppel een sensor met prijzen in de opties.</div>`;
    const W = 600;
    const H = 190;
    const pad = 18;
    const prices = slots.map((s) => s.price);
    const max = Math.max(...prices, 0.0001);
    const min = Math.min(...prices, 0);
    const range = max - min || 1;
    const bw = W / slots.length;
    const y0 = H - pad - ((0 - min) / range) * (H - pad - 8);
    const now = Date.now();
    const planned = d.smart.enabled && !p.preview ? "gepland" : "zou laden";
    let bars = "";
    let labels = "";
    let lastLabel = null;
    slots.forEach((s, i) => {
      const x = i * bw;
      const y = H - pad - ((s.price - min) / range) * (H - pad - 8);
      const st = new Date(s.start);
      const isNow = st.getTime() <= now && now < new Date(s.end).getTime();
      let fill = s.after_deadline ? "#1f2937" : "#374151";
      if (isNow) fill = "#9ca3af";
      if (s.selected) fill = d.smart.enabled ? "var(--green)" : "url(#hatch)";
      bars += `<rect x="${x + 0.6}" y="${Math.min(y, y0)}" width="${Math.max(bw - 1.2, 0.8)}" height="${Math.max(
        Math.abs(y0 - y),
        1
      )}" rx="1.5" fill="${fill}"><title>${dayHm(s.start)} · ${eur(s.price, 3)}/kWh${s.selected ? " · " + planned : ""}</title></rect>`;
      const hr = st.getHours();
      if (st.getMinutes() === 0 && hr % 3 === 0 && hr !== lastLabel) {
        lastLabel = hr;
        labels += `<text x="${x + 1}" y="${H - 3}">${String(hr).padStart(2, "0")}</text>`;
      }
    });
    const first = new Date(slots[0].start).getTime();
    const last = new Date(slots[slots.length - 1].end).getTime();
    const xOf = (t) => ((t - first) / (last - first)) * W;
    let marks = "";
    if (p.deadline) {
      const t = new Date(p.deadline).getTime();
      if (t >= first && t <= last)
        marks += `<line x1="${xOf(t)}" x2="${xOf(t)}" y1="0" y2="${H - pad}" class="dl"/><text x="${Math.min(
          xOf(t) + 3,
          W - 40
        )}" y="10" class="dlt">klaar ${hm(t)}</text>`;
    }
    const cheapest = slots.reduce((a, b) => (b.price < a.price ? b : a));
    const sel = slots.filter((s) => s.selected);
    const window =
      sel.length > 0
        ? `${hm(sel.reduce((a, b) => (a.start < b.start ? a : b)).start)} – ${hm(
            sel.reduce((a, b) => (a.end > b.end ? a : b)).end
          )}`
        : "";
    return `${head}
      <div class="chart">
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
          <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="rgba(34,197,94,.35)"/><line x1="0" y1="0" x2="0" y2="6" stroke="#22c55e" stroke-width="3"/></pattern></defs>
          ${bars}${marks}<g class="lbl">${labels}</g></svg>
        <div class="legend">
          <span><i style="background:#9ca3af"></i>nu</span>
          <span><i style="background:${d.smart.enabled ? "var(--green)" : "repeating-linear-gradient(45deg,#22c55e 0 3px,rgba(34,197,94,.35) 3px 6px)"}"></i>${planned}${
      window ? " " + window : ""
    }</span>
          <span>goedkoopst ${dayHm(cheapest.start)} · ${eur(cheapest.price, 3)}</span>
          <span>${eur(min, 2)} – ${eur(max, 2)}</span>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------ grafieken
  _lineChart(h, color) {
    if (!h) return `<div class="graph empty">Laden…</div>`;
    const W = 600;
    const H = 120;
    const t0 = h.t0;
    const t1 = Math.max(h.t1, t0 + 60000);
    const pts = h.pts.filter((p) => p.t <= t1);
    const before = pts.filter((p) => p.t <= t0).pop();
    const inside = pts.filter((p) => p.t > t0);
    if (!before && !inside.length) return `<div class="graph empty">Geen vermogensdata voor deze periode</div>`;
    const lastV = (inside.length ? inside[inside.length - 1] : before).v;
    const series = [{ t: t0, v: before ? before.v : inside[0].v }, ...inside, { t: t1, v: lastV }];
    const maxV = Math.max(1, ...series.map((p) => p.v));
    const x = (t) => ((t - t0) / (t1 - t0)) * W;
    const y = (v) => H - 14 - (v / maxV) * (H - 24);
    let path = `M ${x(series[0].t)} ${y(series[0].v)}`;
    for (let i = 1; i < series.length; i++) path += ` L ${x(series[i].t)} ${y(series[i - 1].v)} L ${x(series[i].t)} ${y(series[i].v)}`;
    const area = `${path} L ${W} ${H - 14} L 0 ${H - 14} Z`;
    let labels = "";
    const span = t1 - t0;
    const step = span > 12 * 3600e3 ? 3 * 3600e3 : span > 4 * 3600e3 ? 3600e3 : span > 1.5 * 3600e3 ? 1800e3 : 900e3;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) labels += `<text x="${Math.min(x(t), W - 28)}" y="${H - 2}">${hm(t)}</text>`;
    const peak = Math.max(...series.map((p) => p.v));
    return `<div class="graph"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" class="axis"/>
        <path d="${area}" fill="${color}" opacity=".18"/>
        <path d="${path}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>
        <g class="lbl">${labels}</g>
      </svg><div class="legend"><span>${hm(t0)} – ${hm(t1)}</span><span>piek ${num(peak, 2)} kW</span></div></div>`;
  }

  _nowGraph() {
    return `<div class="click" data-entity="${esc(this._e("power"))}">${this._lineChart(this._hist.now, "var(--accent)")}</div>`;
  }
  _lastGraph() {
    if (!this._data?.last_session) return "";
    return `<div class="click" data-entity="${esc(this._e("last_energy"))}">${this._lineChart(this._hist.last, "#60a5fa")}</div>`;
  }

  // -------------------------------------------------------------- historie
  _last(d) {
    const l = d.last_session;
    if (!l) return `<div class="bh big">Vorige sessie</div><div class="rs center">Nog geen afgeronde sessie</div>`;
    return `<div class="bh big">Vorige sessie <span>· ${esc(l.car)} · ${dayHm(l.start)}</span></div>
      <div class="grid2">
        ${this._tile("Opgeladen tijd", dur(l.duration_min), this._e("last_duration"))}
        ${this._tile("Opgeladen", num(l.kwh, 2) + " kWh", this._e("last_energy"))}
        ${this._tile("Sessiekosten", eur(l.cost), this._e("last_cost"))}
        ${this._tile("Gem. kWh prijs", eur(l.avg_price, 3) + "/kWh", this._e("last_avg_price"))}
      </div>`;
  }

  _months(d) {
    const months = d.months || {};
    const cars = d.cars.map((c) => c.name);
    Object.keys(months).forEach((c) => cars.includes(c) || cars.push(c));
    const now = new Date();
    const thisYm = ymOf(now);
    const prevYm = ymOf(new Date(now.getFullYear(), now.getMonth() - 1, 1));
    const yms = [];
    for (let i = 11; i >= 0; i--) yms.push(ymOf(new Date(now.getFullYear(), now.getMonth() - i, 1)));
    const total = (ym) =>
      cars.reduce(
        (a, c) => {
          const m = months[c]?.[ym];
          if (m) {
            a.kwh += m.kwh;
            a.cost += m.cost;
            a.sessions += m.sessions;
          }
          return a;
        },
        { kwh: 0, cost: 0, sessions: 0 }
      );
    const tThis = total(thisYm);
    const tPrev = total(prevYm);
    const W = 600;
    const H = 150;
    const maxK = Math.max(1, ...yms.map((ym) => total(ym).kwh));
    const bw = W / yms.length;
    const slug = (c) => d.car_slugs?.[c] || c.toLowerCase().replace(/\s+/g, "_");
    let bars = "";
    yms.forEach((ym, i) => {
      let yTop = H - 16;
      cars.forEach((c, ci) => {
        const m = months[c]?.[ym];
        if (!m || !m.kwh) return;
        const h = (m.kwh / maxK) * (H - 30);
        yTop -= h;
        bars += `<rect x="${i * bw + bw * 0.15}" y="${yTop}" width="${bw * 0.7}" height="${h}" rx="2" fill="${
          CAR_COLORS[ci % CAR_COLORS.length]
        }" data-entity="${esc(this._e("month_" + slug(c)))}"><title>${esc(c)} ${monthLong(ym)}: ${num(m.kwh, 1)} kWh · ${eur(
          m.cost
        )} · ${m.sessions} sessies</title></rect>`;
      });
      bars += `<text x="${i * bw + bw / 2}" y="${H - 3}" text-anchor="middle" class="${ym === thisYm ? "cur" : ""}">${monthShort(ym)}</text>`;
    });
    const legend = cars.map((c, ci) => `<span><i style="background:${CAR_COLORS[ci % CAR_COLORS.length]}"></i>${esc(c)}</span>`).join("");
    const rows = cars
      .map((c, ci) => {
        const a = months[c]?.[thisYm];
        const b = months[c]?.[prevYm];
        const hasNotify = (d.cars.find((x) => x.name === c) || {}).has_notify;
        return `<div class="lrow click" data-entity="${esc(this._e("month_" + slug(c)))}">
          <div><b><i class="dot" style="background:${CAR_COLORS[ci % CAR_COLORS.length]}"></i>${esc(c)}</b>
            ${hasNotify ? `<button class="link" data-act="report" data-car="${esc(c)}">rapport</button>` : ""}</div>
          <div class="r">${num(a?.kwh ?? 0, 1)} kWh · ${eur(a?.cost ?? 0)}<small>vorige: ${num(b?.kwh ?? 0, 1)} kWh · ${eur(
          b?.cost ?? 0
        )} · ${b?.sessions ?? 0}×</small></div></div>`;
      })
      .join("");
    const sum = (t) =>
      `${num(t.kwh, 1)} kWh<small>${eur(t.cost)} · ${t.sessions} sessies${t.kwh ? " · " + eur(t.cost / t.kwh, 3) : ""}</small>`;
    return `<div class="bh big">Maanden</div>
      <div class="grid2">
        ${this._tile("Deze maand · " + monthShort(thisYm), sum(tThis), "")}
        ${this._tile("Vorige maand · " + monthShort(prevYm), sum(tPrev), "")}
      </div>
      <div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="months">${bars}</svg>
        <div class="legend">${legend}<span>max ${num(maxK, 0)} kWh</span></div></div>
      <div class="list">${rows}</div>`;
  }

  _sessions(d) {
    const rows = (d.sessions || [])
      .slice(0, 8)
      .map((s) => {
        const st = parseLocal(s.start);
        return `<div class="lrow"><div><b>${esc(s.car)}</b><small>${st ? dayHm(st) : esc(s.start)}${
          s.duration_min ? " · " + dur(s.duration_min) : ""
        }</small></div><div class="r">${num(s.kwh, 1)} kWh<small>${eur(s.cost)} · ${eur(s.avg_price, 3)}</small></div></div>`;
      })
      .join("");
    return rows ? `<div class="bh big">Recente sessies</div><div class="list">${rows}</div>` : "";
  }

  // ---------------------------------------------------------------- events
  _bind() {
    const r = this.shadowRoot;
    r.addEventListener("click", (ev) => {
      const path = ev.composedPath();
      const el = path.find((n) => n instanceof Element && (n.dataset?.act || n.dataset?.step || n.dataset?.entity));
      if (!el || !this._data) return;
      const d = this._data;
      if (el.dataset.step) {
        const kind = el.closest(".stepper").dataset.kind;
        const step = Number(el.dataset.step);
        const sm = d.smart;
        if (kind === "soc") {
          sm.target_soc = Math.min(100, Math.max(10, Number(sm.target_soc) + step));
          this._call("set_smart_charging", { target_soc: sm.target_soc });
        } else {
          sm.target_kwh = Math.min(150, Math.max(0, Number(sm.target_kwh) + step));
          this._call("set_smart_charging", { target_kwh: sm.target_kwh });
        }
        return this._render();
      }
      if (el.dataset.act) {
        ev.stopPropagation();
        switch (el.dataset.act) {
          case "start":
            return this._call("start_charging");
          case "stop":
            return this._call("stop_charging");
          case "car":
            return this._call("next_car");
          case "toggle-smart":
            return this._call("set_smart_charging", { enabled: !d.smart.enabled });
          case "resume":
            return this._call("clear_override");
          case "report":
            this._toast(`Rapport voor ${el.dataset.car} verstuurd`);
            return this._call("send_monthly_report", { car: el.dataset.car });
        }
        return;
      }
      if (el.dataset.entity) this._moreInfo(el.dataset.entity);
    });
    r.addEventListener("change", (ev) => {
      const t = ev.composedPath()[0];
      if (t?.classList?.contains("timein") && t.value) this._call("set_smart_charging", { ready_time: t.value + ":00" });
    });
    r.addEventListener("focusout", () => {
      if (this._pendingRender) setTimeout(() => this._render(), 0);
    });
  }

  _styles() {
    return `<style>
      :host { --green:#22c55e; display:block; }
      ha-card { display:block; background:#111827; border-radius:28px; padding:20px 16px; overflow:hidden; position:relative; color:white; border:none; }
      .err { padding:24px; text-align:center; color:#9ca3af; }
      .charge-bg { position:absolute; left:0; top:0; width:100%; height:520px; background:radial-gradient(circle at 50% 180px, var(--accent), transparent 30%);
        filter:blur(35px); opacity:.25; animation:glow 4s infinite alternate; pointer-events:none; }
      ha-card[data-cols="2"] .charge-bg { background:radial-gradient(circle at 25% 180px, var(--accent), transparent 22%); }
      ha-card[data-cols="3"] .charge-bg { background:radial-gradient(circle at 16% 180px, var(--accent), transparent 20%); }
      @keyframes glow { from { opacity:.15 } to { opacity:.35 } }
      .cols { display:grid; grid-template-columns:1fr; gap:28px; position:relative; }
      ha-card[data-cols="2"] .cols { grid-template-columns:1fr 1fr; }
      ha-card[data-cols="2"] .col:nth-child(3) { grid-column:1 / -1; }
      ha-card[data-cols="3"] .cols { grid-template-columns:1fr 1.15fr 1fr; }
      ha-card[data-cols="2"], ha-card[data-cols="3"] { padding:24px; }
      .col { min-width:0; }
      .hero { display:flex; flex-direction:column; align-items:center; position:relative; padding:4px 0; }
      .title { font-size:20px; font-weight:600; letter-spacing:1px; }
      .ringwrap { margin-top:16px; width:190px; height:190px; display:flex; justify-content:center; align-items:center; position:relative; cursor:pointer; }
      .charge-ring { width:170px; height:170px; border-radius:50%; border:4px solid var(--accent);
        box-shadow:0 0 35px var(--accent), inset 0 0 25px var(--accent); animation:pulse 3s infinite alternate; }
      @keyframes pulse { from { transform:scale(.97) } to { transform:scale(1.04) } }
      .caricon { position:absolute; top:52px; --mdc-icon-size:58px; color:white; }
      .carname { position:absolute; top:122px; font-size:15px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }
      .soc { position:absolute; top:144px; font-size:13px; color:#9ca3af; }
      .power { font-size:40px; font-weight:600; margin-top:10px; cursor:pointer; }
      .power span { font-size:20px; color:#9ca3af; }
      .powerbar { width:260px; max-width:90%; height:7px; border-radius:10px; background:#374151; overflow:hidden; margin-top:12px; }
      .powerfill { height:100%; border-radius:10px; background:var(--accent); box-shadow:0 0 12px var(--accent); transition:width .6s; }
      .state { margin-top:14px; color:var(--accent); font-size:18px; font-weight:500; cursor:pointer; }
      .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }
      .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-top:12px; }
      .tile { background:rgba(255,255,255,.05); border-radius:14px; padding:12px; text-align:center; }
      .click { cursor:pointer; }
      .tile.click:hover, .lrow.click:hover, .xrow:hover, .socbox:hover { background:rgba(255,255,255,.09); }
      .tn { font-size:12px; color:#9ca3af; }
      .tv { font-size:16px; font-weight:600; margin-top:4px; }
      .tv small, .lrow small { display:block; font-size:11px; color:#9ca3af; font-weight:400; margin-top:2px; }
      .bh { font-size:13px; color:#9ca3af; margin:20px 0 6px; }
      .bh.big { font-size:17px; color:white; font-weight:600; }
      .col > div:first-child > .bh.big:first-child { margin-top:4px; }
      .bh span { color:#9ca3af; font-weight:400; font-size:13px; }
      .row { display:flex; align-items:center; justify-content:space-between; margin-top:8px; gap:12px; }
      .rs { font-size:12px; color:#9ca3af; margin-top:2px; }
      .center { text-align:center; padding:16px 0 4px; }
      .switch { width:48px; height:28px; border-radius:14px; border:none; background:#374151; position:relative; cursor:pointer; flex:none; }
      .switch span { position:absolute; top:3px; left:3px; width:22px; height:22px; border-radius:50%; background:white; transition:left .2s; }
      .switch.on { background:var(--green); }
      .switch.on span { left:23px; }
      .timein { background:none; border:none; color:white; font:inherit; font-size:16px; font-weight:600; text-align:center; margin-top:4px; color-scheme:dark; width:100%; }
      .stepper { display:flex; align-items:center; justify-content:center; gap:10px; margin-top:4px; }
      .stepper span { font-size:16px; font-weight:600; min-width:64px; cursor:pointer; }
      .stepper button { width:26px; height:26px; border-radius:50%; border:none; background:rgba(255,255,255,.1); color:white; font-size:16px; cursor:pointer; }
      .planmsg { margin-top:14px; text-align:center; font-size:14px; color:#f59e0b; }
      .planmsg.ok { color:var(--green); }
      .link { background:none; border:none; color:#60a5fa; font:inherit; font-size:12px; cursor:pointer; padding:0 0 0 8px; }
      .warn { margin-top:10px; font-size:12px; color:#f59e0b; text-align:center; }
      .deadline { margin-top:8px; font-size:12px; color:#9ca3af; text-align:center; }
      .chart, .graph { margin-top:6px; background:rgba(255,255,255,.03); border-radius:14px; padding:10px 10px 6px; }
      .chart svg { width:100%; height:190px; display:block; }
      .chart svg.months { height:150px; }
      .chart svg.months rect { cursor:pointer; }
      .graph svg { width:100%; height:120px; display:block; }
      .graph.empty { color:#6b7280; font-size:12px; text-align:center; padding:40px 0; }
      svg text { fill:#6b7280; font-size:10px; }
      svg text.cur { fill:white; }
      svg .axis { stroke:#374151; stroke-width:1; }
      svg .dl { stroke:#60a5fa; stroke-width:1.5; }
      svg .dlt { fill:#60a5fa; }
      .legend { display:flex; flex-wrap:wrap; gap:4px 14px; justify-content:space-between; font-size:11px; color:#9ca3af; margin-top:4px; }
      .legend i, .dot { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
      .list { margin-top:10px; background:rgba(255,255,255,.04); border-radius:14px; overflow:hidden; }
      .lrow { display:flex; justify-content:space-between; align-items:center; padding:10px 12px; font-size:14px; border-bottom:1px solid rgba(255,255,255,.05); }
      .lrow:last-child { border-bottom:none; }
      .lrow .r { text-align:right; }
      .carbox { background:rgba(255,255,255,.04); border-radius:14px; overflow:hidden; }
      .socbox { padding:12px; }
      .socval { font-size:28px; font-weight:600; }
      .socbar { position:relative; height:10px; border-radius:6px; background:#374151; margin:8px 0 4px; }
      .socfill { height:100%; border-radius:6px; background:linear-gradient(90deg,#22c55e,#4ade80); box-shadow:0 0 10px rgba(34,197,94,.6); }
      .soctarget { position:absolute; top:-3px; width:2px; height:16px; background:white; border-radius:1px; }
      .xlist { border-top:1px solid rgba(255,255,255,.05); }
      .xrow { display:flex; align-items:center; gap:10px; padding:9px 12px; font-size:14px; cursor:pointer; border-bottom:1px solid rgba(255,255,255,.05); --mdc-icon-size:18px; color:#d1d5db; }
      .xrow:last-child { border-bottom:none; }
      .xn { flex:1; color:#9ca3af; }
      .xv { font-weight:600; color:white; text-align:right; }
      .buttons { display:grid; grid-template-columns:1fr 1fr 1fr; margin-top:16px; }
      .btn { background:none; border:none; color:white; font:inherit; font-size:14px; display:flex; flex-direction:column; align-items:center;
        gap:4px; cursor:pointer; padding:8px 0 0; --mdc-icon-size:38px; }
      .btn[disabled] { opacity:.45; pointer-events:none; }
    </style>`;
  }
}

class EVChargeDirectorCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._entries && hass?.connection) {
      this._entries = [];
      hass.connection
        .sendMessagePromise({ type: `${DOMAIN}/list` })
        .then((e) => {
          this._entries = e;
          this._render();
        })
        .catch(() => {});
    }
  }
  _render() {
    if (!this._config) return;
    const entries = this._entries || [];
    this.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;padding:8px 0">
        <label>Titel<br><input data-k="title" style="width:100%" value="${esc(this._config.title ?? "CHARGE CONTROL")}"></label>
        <label>Laadpunt<br><select data-k="entry_id" style="width:100%">
          <option value="">Eerste laadpunt</option>
          ${entries
            .map((e) => `<option value="${esc(e.entry_id)}" ${e.entry_id === this._config.entry_id ? "selected" : ""}>${esc(e.name)}</option>`)
            .join("")}
        </select></label>
        <div style="font-size:12px;opacity:.7">Tip: zet de weergave op <b>Paneel</b> voor het brede overzicht.</div>
      </div>`;
    this.querySelectorAll("[data-k]").forEach((el) =>
      el.addEventListener("change", () => {
        const cfg = { ...this._config };
        if (el.value) cfg[el.dataset.k] = el.value;
        else delete cfg[el.dataset.k];
        this._config = cfg;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: cfg }, bubbles: true, composed: true }));
      })
    );
  }
}

if (!customElements.get("ev-charge-director-card")) {
  customElements.define("ev-charge-director-card", EVChargeDirectorCard);
  customElements.define("ev-charge-director-card-editor", EVChargeDirectorCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "ev-charge-director-card",
    name: "EV Charge Director",
    description: "Laadstatus, slim laden, prijsgrafiek, historie en maandoverzicht",
    preview: false,
  });
  console.info(`%c EV-CHARGE-DIRECTOR %c ${VERSION} `, "background:#22c55e;color:#111;font-weight:700", "background:#111827;color:#fff");
}
