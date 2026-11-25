/*
 * EV Charge Director card
 * Wordt automatisch geladen door de integratie ev_charge_director.
 *
 *   type: custom:ev-charge-director-card
 *   entry_id: <optioneel, alleen bij meerdere laadpunten>
 *   title: CHARGE CONTROL        # optioneel
 *   tab: plan | history | months # optioneel, start-tabblad
 */
const DOMAIN = "ev_charge_director";
const VERSION = "0.1.0";

const CAT = {
  disconnected: { color: "#9ca3af" },
  connected: { color: "#60a5fa" },
  charging: { color: "#22c55e" },
  paused: { color: "#f59e0b" },
  finished: { color: "#60a5fa" },
  error: { color: "#ef4444" },
  unknown: { color: "#6b7280" },
};

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => (v === null || v === undefined || isNaN(v) ? "–" : Number(v).toFixed(d));
const eur = (v, d = 2) => (v === null || v === undefined || isNaN(v) ? "–" : "€" + Number(v).toFixed(d));
const hm = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso);
  return isNaN(d) ? "–" : d.toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" });
};
const dayHm = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d)) return "–";
  const today = new Date();
  const tomorrow = new Date(Date.now() + 864e5);
  const t = hm(iso);
  if (d.toDateString() === today.toDateString()) return `vandaag ${t}`;
  if (d.toDateString() === tomorrow.toDateString()) return `morgen ${t}`;
  return d.toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" }) + " " + t;
};
const dur = (min) => {
  if (min === null || min === undefined || isNaN(min)) return "–";
  const h = Math.floor(min / 60);
  const m = Math.round(min % 60);
  return h > 0 ? `${h}u ${m}m` : `${m} min`;
};
const monthName = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("nl-NL", { month: "long", year: "numeric" });
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
    this._tab = null;
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
    if (!this._tab) this._tab = this._config.tab || "plan";
    if (this._unsub) this._resubscribe();
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || (this._lastMsg && Date.now() - this._lastMsg > 120000)) this._resubscribe();
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._resubscribe();
  }

  disconnectedCallback() {
    this._unsubscribe();
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
      .catch((e) => {
        this._toast(e?.message || String(e));
      });
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  // --------------------------------------------------------------- render
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
        <ha-card>
          <div class="charge-bg"></div>
          <div data-s="hero"></div><div data-s="power"></div><div data-s="tiles"></div>
          <div data-s="tabs"></div><div data-s="body" class="tabbody"></div><div data-s="buttons"></div>
        </ha-card>`;
      this._shell = {};
      root.querySelectorAll("[data-s]").forEach((el) => (this._shell[el.dataset.s] = { el, html: null }));
    }
    const d = this._data;
    const cat = d.status?.category || "unknown";
    const accent = CAT[cat]?.color || CAT.unknown.color;
    root.querySelector("ha-card").style.setProperty("--accent", accent);

    const editing = root.activeElement && root.activeElement.tagName === "INPUT";
    this._pendingRender = editing;
    const sections = {
      hero: this._hero(d),
      power: this._powerState(d, cat),
      tiles: this._tiles(d),
      tabs: this._tabsBar(),
      buttons: this._buttons(d),
    };
    if (!editing) sections.body = this._tabBody(d);
    for (const [k, html] of Object.entries(sections)) {
      const s = this._shell[k];
      if (s.html !== html) {
        s.el.innerHTML = html;
        s.html = html;
      }
    }
  }

  _hero(d) {
    const car = d.cars.find((c) => c.name === d.car);
    const soc = car && car.soc !== null && car.soc !== undefined ? `<div class="soc">${Math.round(car.soc)}%</div>` : "";
    return `
      <div class="hero">
        <div class="title">${esc(this._config.title)}</div>
        <div class="ringwrap">
          <div class="charge-ring"></div>
          <ha-icon icon="mdi:car-electric" class="caricon"></ha-icon>
          <div class="carname">${esc(d.car)}</div>
          ${soc}
        </div>
      </div>`;
  }

  _powerState(d, cat) {
    let power = "";
    if (["charging", "paused"].includes(cat) || (d.power_kw ?? 0) > 0.05) {
      const p = d.power_kw;
      const width = p !== null && p !== undefined ? Math.min((p / (d.max_power_kw || 11)) * 100, 100) : 0;
      power = `
        <div class="power">${num(p, 2)} <span>kW</span></div>
        <div class="powerbar"><div class="powerfill" style="width:${width}%"></div></div>`;
    }
    return `<div class="hero">${power}<div class="state">● ${esc(d.status?.label)}</div></div>`;
  }

  _tile(name, value, action = "") {
    return `<div class="tile" ${action}><div class="tn">${name}</div><div class="tv">${value}</div></div>`;
  }

  _tiles(d) {
    const s = d.session;
    return `
      <div class="grid2">
        ${this._tile("Starttijd", s.active ? hm(s.start) : "–")}
        ${this._tile("Prijs", d.price !== null && d.price !== undefined ? eur(d.price, 3) + "/kWh" : "–")}
        ${this._tile("Opgeladen", num(s.kwh, 1) + " kWh")}
        ${this._tile("Sessiekosten", eur(s.cost))}
      </div>`;
  }

  _tabsBar() {
    const tabs = [
      ["plan", "mdi:clock-time-four-outline", "Slim laden"],
      ["history", "mdi:history", "Historie"],
      ["months", "mdi:calendar-month", "Maanden"],
    ];
    return `<div class="tabs">${tabs
      .map(
        ([k, icon, label]) =>
          `<button class="tab ${this._tab === k ? "on" : ""}" data-tab="${k}"><ha-icon icon="${icon}"></ha-icon>${label}</button>`
      )
      .join("")}</div>`;
  }

  _tabBody(d) {
    if (this._tab === "history") return this._history(d);
    if (this._tab === "months") return this._months(d);
    return this._plan(d);
  }

  // ----------------------------------------------------------- slim laden
  _plan(d) {
    const sm = d.smart;
    const p = d.plan || {};
    const car = d.cars.find((c) => c.name === d.car) || {};
    const socMode = car.soc !== null && car.soc !== undefined && car.capacity_kwh;
    const target = socMode
      ? `<div class="stepper" data-kind="soc">
           <button data-step="-5">−</button><span>${sm.target_soc}%</span><button data-step="5">+</button>
         </div>`
      : `<div class="stepper" data-kind="kwh">
           <button data-step="-1">−</button><span>${num(sm.target_kwh, 1)} kWh</span><button data-step="1">+</button>
         </div>`;

    const toggle = `
      <div class="row">
        <div><div class="rt">Slim laden</div><div class="rs">${
          sm.enabled ? "Laadt in de goedkoopste blokken" : "Uit – laden start direct"
        }</div></div>
        <button class="switch ${sm.enabled ? "on" : ""}" data-act="toggle-smart"><span></span></button>
      </div>`;
    if (!sm.enabled) return toggle;

    const settings = `
      <div class="grid2">
        <div class="tile"><div class="tn">Klaar om</div>
          <input type="time" class="timein" value="${esc(sm.ready_time)}"></div>
        <div class="tile"><div class="tn">${socMode ? "Laden tot" : "Erbij laden"}</div>${target}</div>
      </div>`;

    let msg = "";
    let cls = "";
    const reasons = {
      cheap_now: "Nu laden: goedkoop blok",
      deadline_close: "Nu laden: deadline komt dichtbij",
      deadline_passed: "Deadline verstreken – doorladen tot het doel",
      no_prices: "Geen prijsdata – direct laden",
      override_on: "Handmatig gestart – laadt door",
      override_off: "Handmatig gestopt",
      waiting_cheap: "Wacht op goedkoper blok",
    };
    if (p.mode === "waiting") msg = "Sluit de auto aan om te plannen";
    else if (p.mode === "done") {
      msg = "Laaddoel bereikt ✓";
      cls = "ok";
    } else if (p.mode === "scheduled" || p.mode === "charging") {
      msg = reasons[p.reason] || "";
      if (p.reason === "cheap_now" || p.reason === "deadline_close") cls = "ok";
    }
    const override = sm.override
      ? `<button class="link" data-act="resume">Planning hervatten</button>`
      : "";

    let facts = "";
    if (p.mode === "scheduled" || p.mode === "charging") {
      facts = `
        <div class="grid3">
          ${this._tile("Volgende start", p.next_start ? dayHm(p.next_start) : "–")}
          ${this._tile("Nog nodig", `${num(p.needed_kwh, 1)} kWh<small>${dur(p.needed_minutes)}</small>`)}
          ${this._tile("Verwacht", eur(p.expected_cost))}
        </div>
        ${p.insufficient ? `<div class="warn">Niet genoeg tijd of prijsdata voor het hele doel; er wordt zo veel mogelijk geladen.</div>` : ""}
        <div class="deadline">Klaar ${dayHm(p.deadline)}</div>`;
    }
    const chart = sm.has_forecast ? this._chart(p.slots || [], p.deadline) : `<div class="rs center">Geen prijsvoorspelling gevonden. Koppel een sensor met prijzen in de opties.</div>`;

    return `${toggle}${settings}
      <div class="planmsg ${cls}">${esc(msg)} ${override}</div>
      ${facts}${chart}`;
  }

  _chart(slots, deadline) {
    if (!slots.length) return "";
    const W = 600;
    const H = 130;
    const pad = 18;
    const prices = slots.map((s) => s.price);
    const max = Math.max(...prices, 0.0001);
    const min = Math.min(...prices, 0);
    const range = max - min || 1;
    const bw = W / slots.length;
    const now = Date.now();
    const y0 = H - pad - ((0 - min) / range) * (H - pad - 8);
    let bars = "";
    let labels = "";
    let lastLabelHour = null;
    slots.forEach((s, i) => {
      const x = i * bw;
      const y = H - pad - ((s.price - min) / range) * (H - pad - 8);
      const top = Math.min(y, y0);
      const h = Math.max(Math.abs(y0 - y), 1);
      const fill = s.selected ? "var(--accent-green)" : s.after_deadline ? "#1f2937" : "#374151";
      const st = new Date(s.start);
      bars += `<rect x="${x + 0.6}" y="${top}" width="${Math.max(bw - 1.2, 0.8)}" height="${h}" rx="1.5" fill="${fill}"><title>${hm(
        s.start
      )} ${eur(s.price, 3)}${s.selected ? " · gepland" : ""}</title></rect>`;
      const hr = st.getHours();
      if (st.getMinutes() === 0 && hr % 3 === 0 && hr !== lastLabelHour) {
        lastLabelHour = hr;
        labels += `<text x="${x + 1}" y="${H - 3}">${String(hr).padStart(2, "0")}</text>`;
      }
    });
    const first = new Date(slots[0].start).getTime();
    const last = new Date(slots[slots.length - 1].end).getTime();
    const xOf = (t) => ((t - first) / (last - first)) * W;
    let marks = "";
    if (now >= first && now <= last) marks += `<line x1="${xOf(now)}" x2="${xOf(now)}" y1="0" y2="${H - pad}" class="now"/>`;
    if (deadline) {
      const t = new Date(deadline).getTime();
      if (t >= first && t <= last)
        marks += `<line x1="${xOf(t)}" x2="${xOf(t)}" y1="0" y2="${H - pad}" class="dl"/><text x="${Math.min(
          xOf(t) + 3,
          W - 40
        )}" y="10" class="dlt">klaar</text>`;
    }
    return `
      <div class="chart">
        <div class="legend"><span><i class="g"></i>gepland</span><span>${eur(min, 2)} – ${eur(max, 2)}/kWh</span></div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bars}${marks}<g class="lbl">${labels}</g></svg>
      </div>`;
  }

  // -------------------------------------------------------------- historie
  _history(d) {
    const l = d.last_session;
    const last = l
      ? `<div class="sub">Laatste sessie · ${esc(l.car)}</div>
         <div class="grid2">
           ${this._tile("Opgeladen tijd", dur(l.duration_min))}
           ${this._tile("Opgeladen", num(l.kwh, 1) + " kWh")}
           ${this._tile("Sessiekosten", eur(l.cost))}
           ${this._tile("Gem. kWh prijs", eur(l.avg_price, 3) + "/kWh")}
         </div>`
      : `<div class="rs center">Nog geen afgeronde sessie</div>`;
    const rows = (d.sessions || [])
      .slice(0, 12)
      .map(
        (s) => `<div class="lrow"><div><b>${esc(s.car)}</b><small>${esc(s.start)}${
          s.duration_min ? " · " + dur(s.duration_min) : ""
        }</small></div><div class="r">${num(s.kwh, 1)} kWh<small>${eur(s.cost)} · ${eur(s.avg_price, 3)}</small></div></div>`
      )
      .join("");
    return `${last}${rows ? `<div class="sub">Recente sessies</div><div class="list">${rows}</div>` : ""}`;
  }

  _months(d) {
    const cars = Object.keys(d.months || {});
    if (!cars.length) return `<div class="rs center">Nog geen maandgegevens</div>`;
    return cars
      .map((car) => {
        const months = Object.entries(d.months[car])
          .sort((a, b) => b[0].localeCompare(a[0]))
          .slice(0, 6);
        const rows = months
          .map(
            ([ym, m]) =>
              `<div class="lrow"><div><b>${esc(monthName(ym))}</b><small>${m.sessions} sessies</small></div><div class="r">${num(
                m.kwh,
                1
              )} kWh<small>${eur(m.cost)} · gem. ${eur(m.avg_price, 3)}</small></div></div>`
          )
          .join("");
        const hasNotify = (d.cars.find((c) => c.name === car) || {}).has_notify;
        return `<div class="sub">${esc(car)}${
          hasNotify ? `<button class="link" data-act="report" data-car="${esc(car)}">Rapport sturen</button>` : ""
        }</div><div class="list">${rows}</div>`;
      })
      .join("");
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

  // ---------------------------------------------------------------- events
  _bind() {
    const r = this.shadowRoot;
    r.addEventListener("click", (ev) => {
      const path = ev.composedPath();
      const el = path.find((n) => n instanceof HTMLElement && (n.dataset?.tab || n.dataset?.act || n.dataset?.step));
      if (!el || !this._data) return;
      const d = this._data;
      if (el.dataset.tab) {
        this._tab = el.dataset.tab;
        return this._render();
      }
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
      :host { --accent-green:#22c55e; }
      ha-card { display:block; background:#111827; border-radius:28px; padding:20px 16px; overflow:hidden; position:relative; color:white; border:none; }
      .err { padding:24px; text-align:center; color:#9ca3af; }
      .charge-bg { position:absolute; inset:0; background:radial-gradient(circle at 50% 22%, var(--accent), transparent 35%);
        filter:blur(35px); opacity:.25; animation:glow 4s infinite alternate; pointer-events:none; }
      @keyframes glow { from { transform:scale(.9); opacity:.15 } to { transform:scale(1.15); opacity:.35 } }
      .hero { display:flex; flex-direction:column; align-items:center; position:relative; padding:4px 0; }
      .title { font-size:20px; font-weight:600; letter-spacing:1px; }
      .ringwrap { margin-top:16px; width:190px; height:190px; display:flex; justify-content:center; align-items:center; position:relative; }
      .charge-ring { width:170px; height:170px; border-radius:50%; border:4px solid var(--accent);
        box-shadow:0 0 35px var(--accent), inset 0 0 25px var(--accent); animation:pulse 3s infinite alternate; }
      @keyframes pulse { from { transform:scale(.97) } to { transform:scale(1.04) } }
      .caricon { position:absolute; top:52px; --mdc-icon-size:58px; color:white; }
      .carname { position:absolute; top:122px; font-size:15px; font-weight:700; letter-spacing:1px; text-transform:uppercase; }
      .soc { position:absolute; top:144px; font-size:13px; color:#9ca3af; }
      .power { font-size:40px; font-weight:600; margin-top:10px; }
      .power span { font-size:20px; color:#9ca3af; }
      .powerbar { width:260px; max-width:90%; height:7px; border-radius:10px; background:#374151; overflow:hidden; margin-top:12px; }
      .powerfill { height:100%; border-radius:10px; background:var(--accent); box-shadow:0 0 12px var(--accent); transition:width .6s; }
      .state { margin-top:14px; color:var(--accent); font-size:18px; font-weight:500; }
      .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; position:relative; }
      .grid3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-top:12px; }
      .tile { background:rgba(255,255,255,.05); border-radius:14px; padding:12px; text-align:center; }
      .tn { font-size:12px; color:#9ca3af; }
      .tv { font-size:16px; font-weight:600; margin-top:4px; }
      .tv small, .lrow small { display:block; font-size:11px; color:#9ca3af; font-weight:400; margin-top:2px; }
      .tabs { display:flex; gap:6px; margin-top:16px; background:rgba(255,255,255,.04); border-radius:14px; padding:4px; position:relative; }
      .tab { flex:1; background:none; border:none; color:#9ca3af; padding:8px 4px; border-radius:10px; font:inherit; font-size:13px;
        display:flex; align-items:center; justify-content:center; gap:6px; cursor:pointer; --mdc-icon-size:18px; }
      .tab.on { background:rgba(255,255,255,.09); color:white; }
      .tabbody { position:relative; min-height:40px; }
      .row { display:flex; align-items:center; justify-content:space-between; margin-top:14px; gap:12px; }
      .rt { font-size:16px; font-weight:600; }
      .rs { font-size:12px; color:#9ca3af; margin-top:2px; }
      .center { text-align:center; padding:16px 0 4px; }
      .switch { width:48px; height:28px; border-radius:14px; border:none; background:#374151; position:relative; cursor:pointer; flex:none; }
      .switch span { position:absolute; top:3px; left:3px; width:22px; height:22px; border-radius:50%; background:white; transition:left .2s; }
      .switch.on { background:var(--accent-green); }
      .switch.on span { left:23px; }
      .timein { background:none; border:none; color:white; font:inherit; font-size:16px; font-weight:600; text-align:center; margin-top:4px;
        color-scheme:dark; width:100%; }
      .stepper { display:flex; align-items:center; justify-content:center; gap:10px; margin-top:4px; }
      .stepper span { font-size:16px; font-weight:600; min-width:64px; }
      .stepper button { width:26px; height:26px; border-radius:50%; border:none; background:rgba(255,255,255,.1); color:white; font-size:16px; cursor:pointer; }
      .planmsg { margin-top:14px; text-align:center; font-size:14px; color:#f59e0b; }
      .planmsg.ok { color:var(--accent-green); }
      .link { background:none; border:none; color:#60a5fa; font:inherit; font-size:13px; cursor:pointer; padding:0 0 0 8px; }
      .warn { margin-top:10px; font-size:12px; color:#f59e0b; text-align:center; }
      .deadline { margin-top:8px; font-size:12px; color:#9ca3af; text-align:center; }
      .chart { margin-top:12px; }
      .chart svg { width:100%; height:130px; display:block; }
      .chart .lbl text { fill:#6b7280; font-size:10px; }
      .chart .now { stroke:white; stroke-width:1.5; stroke-dasharray:3 3; }
      .chart .dl { stroke:#60a5fa; stroke-width:1.5; }
      .chart .dlt { fill:#60a5fa; font-size:10px; }
      .legend { display:flex; justify-content:space-between; font-size:11px; color:#9ca3af; margin-bottom:4px; }
      .legend i { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:4px; vertical-align:-1px; }
      .legend i.g { background:var(--accent-green); }
      .sub { margin-top:16px; font-size:15px; font-weight:500; display:flex; justify-content:space-between; align-items:center; }
      .list { margin-top:8px; background:rgba(255,255,255,.04); border-radius:14px; }
      .lrow { display:flex; justify-content:space-between; padding:10px 12px; font-size:14px; border-bottom:1px solid rgba(255,255,255,.05); }
      .lrow:last-child { border-bottom:none; }
      .lrow .r { text-align:right; }
      .buttons { display:grid; grid-template-columns:1fr 1fr 1fr; margin-top:16px; position:relative; }
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
        <label>Start-tabblad<br><select data-k="tab" style="width:100%">
          ${["plan", "history", "months"]
            .map((t) => `<option value="${t}" ${t === (this._config.tab || "plan") ? "selected" : ""}>${{ plan: "Slim laden", history: "Historie", months: "Maanden" }[t]}</option>`)
            .join("")}
        </select></label>
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
    description: "Laadstatus, slim laden, historie en maandoverzicht",
    preview: false,
  });
  console.info(`%c EV-CHARGE-DIRECTOR %c ${VERSION} `, "background:#22c55e;color:#111;font-weight:700", "background:#111827;color:#fff");
}
