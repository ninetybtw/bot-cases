// Мини-приложение «Кейсы CS2». Всё случайное решает сервер, клиент только показывает результат.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TIER_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret", "divine", "exclusive"];
  const DROP_TIERS = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret"];
  const WEAR = { FN: "Factory New", MW: "Minimal Wear", FT: "Field-Tested", WW: "Well-Worn", BS: "Battle-Scarred" };
  const rank = (t) => TIER_ORDER.indexOf(t);
  const api = window.Api;

  let C = null;   // GET /config
  let M = null;   // GET /me
  let INV = null; // GET /inventory
  let busy = false;
  const settings = loadSettings();
  const ui = {
    tab: "cases", cat: "all", caseId: null, count: 1,
    colFilter: "all", colSort: "new", colSearch: "", select: false, selected: new Set(),
    up: { items: [], coins: 0, mult: 2, target: null, targets: [], side: "stake", spinning: false },
    ct: { items: [], tier: "all" },
    page: null, market: "all", lotsTier: null, board: "capital", scope: "friends",
  };

  // ---------- утилиты ----------
  function loadSettings() {
    const d = { fast: false, nofx: false, noUpgrader: false };
    try { return Object.assign(d, JSON.parse(localStorage.getItem("settings") || "{}")); } catch (e) { return d; }
  }
  function saveSettings() { try { localStorage.setItem("settings", JSON.stringify(settings)); } catch (e) { /* без хранилища */ } }
  function money(cents, opts = {}) {
    const cur = (M && M.currency) || { rate: 1, decimals: 0, symbol: "₽" };
    const v = (cents / 100) * cur.rate * ((C && C.currency.scale) || 1);
    const d = cur.decimals;
    const fixed = d ? v.toFixed(d) : String(Math.round(v));
    const [a, b] = fixed.split(".");
    const text = a.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (b ? "," + b : "");
    return opts.bare ? text : text + " " + cur.symbol;
  }
  function pct(p, digits) {
    const d = digits != null ? digits : p >= 0.1 ? 1 : p >= 0.001 ? 2 : 3;
    return (p * 100).toFixed(d).replace(".", ",") + "%";
  }
  const num = (x) => String(x).replace(".", ",");
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function tc(t) { return `--tc: var(--t-${t})`; }
  function tier(t) { return (C && C.tiers[t]) || { name: t, icon: "" }; }
  function haptic(kind) {
    const t = api.tg();
    if (!t || !t.HapticFeedback) return;
    try {
      if (kind === "tick") t.HapticFeedback.selectionChanged();
      else if (["light", "medium", "heavy"].includes(kind)) t.HapticFeedback.impactOccurred(kind);
      else t.HapticFeedback.notificationOccurred(kind);
    } catch (e) { /* старый клиент */ }
  }
  let toastTimer;
  function toast(text, action) {
    $("toast-text").textContent = text;
    const btn = $("toast-action");
    btn.hidden = !action;
    if (action) { btn.textContent = action.label; btn.onclick = () => { $("toast").hidden = true; action.run(); }; }
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("toast").hidden = true), action ? 8000 : 2600);
  }
  const itemTitle = (it) => `${esc(it.weapon)}${it.finish ? " | " + esc(it.finish) : ""}`;
  const wearLine = (it) => [it.stattrak ? '<span class="st">ST™</span>' : "", it.wear || ""].filter(Boolean).join(" ");
  const caseById = (id) => C.cases.find((c) => c.id === id);
  const dayStr = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
  const findItem = (id) => INV && INV.items.find((x) => x.id === id);
  const usable = (i) => i.state === "free" && !i.locked && !i.frozen;
  const sellable = (i) => usable(i) && !(i.hold_until && i.hold_until > Date.now() / 1000);
  const toCents = (v) => Math.round(((parseFloat(String(v).replace(",", ".")) || 0) / M.currency.rate) * 100);
  const fromCents = (c) => ((c / 100) * M.currency.rate).toFixed(M.currency.decimals);

  // ---------- шторка и подтверждения ----------
  let sheetClose = null;
  function openSheet(html, onClose) {
    $("sheet-body").innerHTML = html;
    $("sheet").hidden = false;
    $("sheet-back").hidden = false;
    $("sheet").scrollTop = 0;
    sheetClose = onClose || null;
    return $("sheet-body");
  }
  function closeSheet() {
    $("sheet").hidden = true;
    $("sheet-back").hidden = true;
    const cb = sheetClose;
    sheetClose = null;
    if (cb) cb();
  }
  $("sheet-back").addEventListener("click", closeSheet);

  // Подтверждение без таймеров: одна кнопка «Отмена», одна — действие.
  function confirmSheet({ title, text, ok }) {
    return new Promise((resolve) => {
      const body = openSheet(`
        <h2>${esc(title)}</h2><p>${text}</p>
        <div class="actions"><button type="button" id="cf-no">Отмена</button>
        <button type="button" class="primary" id="cf-ok">${esc(ok)}</button></div>`,
        () => resolve(false));
      body.querySelector("#cf-no").onclick = () => closeSheet();
      body.querySelector("#cf-ok").onclick = () => { sheetClose = null; closeSheet(); resolve(true); };
    });
  }

  // Повторить запрос с confirmed: true, если сервер ответил CONFIRM_REQUIRED.
  async function withConfirm(fn, title, okLabel) {
    try {
      return await fn(false);
    } catch (err) {
      if (err.code !== "CONFIRM_REQUIRED") throw err;
      const ok = await confirmSheet({ title: title || "Подтвердить?", text: esc(err.message), ok: okLabel || "Подтвердить" });
      if (!ok) return null;
      return fn(true);
    }
  }

  function handleError(err) {
    haptic("error");
    if (err.code === "NOT_ENOUGH_COINS") {
      const body = openSheet(`<h2>${esc(err.message.split(".")[0])}</h2>
        <p class="lead">Монеты дают колесо дня, задания и продажа предметов в Инвентаре.</p>
        <div class="actions"><button type="button" id="ne-wheel">Колесо</button><button type="button" id="ne-inv">Продать скины</button></div>`);
      body.querySelector("#ne-wheel").onclick = () => openWheel();
      body.querySelector("#ne-inv").onclick = () => { closeSheet(); switchTab("inventory"); };
      return;
    }
    toast(err.message || "Что-то пошло не так. Попробуй ещё раз.");
  }

  // ---------- загрузка ----------
  async function refresh(withInventory = true) {
    const [me, inv] = await Promise.all([api.get("/me"), withInventory ? api.get("/inventory") : Promise.resolve(INV)]);
    M = me;
    INV = inv;
    renderAll();
  }

  function renderAll() {
    renderHeader();
    if (ui.tab === "cases") renderCases();
    if (ui.tab === "upgrade") renderUpgrade();
    if (ui.tab === "contract") renderContract();
    if (ui.tab === "inventory") renderInventory();
    if (ui.tab === "more" && !ui.page) renderMore();
  }

  function renderHeader() {
    $("balance-value").textContent = money(M.balance_cents);
    $("balance-label").textContent = M.currency.game_label || "";
    $("energy-value").textContent = `${M.energy.now + M.bonus_spins}/${M.energy.max}`;
    document.querySelector('#tabs [data-tab="upgrade"]').hidden = settings.noUpgrader;
  }

  async function loadLive() {
    try {
      const { feed } = await api.get("/feed");
      $("live").innerHTML = feed.slice(0, 14).map((f) => `
        <div class="live-cell" style="${tc(f.tier)}" title="${esc(f.owner_name)}: ${esc(f.name)}"><img src="${esc(f.image_url)}" alt="" loading="lazy"><small>${money(f.price_cents)}</small></div>`).join("");
      $("live").hidden = !feed.length;
    } catch (e) { /* лента не критична */ }
  }

  // ---------- КЕЙСЫ ----------
  function caseCard(c) {
    const eff = M.luck && M.luck.cases[c.id];
    const luck = eff && eff > 1 ? `<span class="tag luck">×${num(eff)}</span>` : "";
    return `<button type="button" class="case-card" data-case="${c.id}" style="${tc(c.top_tier)}">
      ${c.free ? '<span class="tag free">бесплатно</span>' : ""}${luck}
      <img src="${esc(c.image_url)}" alt="" loading="lazy">
      <span class="cn">${esc(c.name)}</span>
      <b>${c.free && M.energy.now + M.bonus_spins > 0 ? "⚡ " + (M.energy.now + M.bonus_spins) : money(c.price_cents)}</b></button>`;
  }

  function renderCases() {
    if (ui.caseId) return renderCaseView();
    $("case-list-view").hidden = false;
    $("case-view").hidden = true;
    const bn = C.banners;
    $("banners").hidden = !(bn && bn.list.length);
    if (bn && bn.list.length) {
      $("banners").innerHTML = bn.list.map((b) => `
        <button type="button" class="ad" data-ad="${esc(b.id)}" style="--ad:${esc(b.color || "#2f8cff")}">
          <span class="ad-label">${esc(bn.label)}${b.erid ? " · erid " + esc(b.erid) : ""}</span>
          <b>${esc(b.title)}</b><small>${esc(b.text)}</small><span class="ad-go">Открыть канал →</span></button>`).join("");
    }
    const energy = M.energy.now + M.bonus_spins;
    const wheel = M.today.wheel_daily ? "готово" : M.today.wheel_weekly_unlocked ? "недельное!" : "завтра";
    $("quick").innerHTML = `
      <button type="button" class="q ${energy ? "ready" : ""}" data-q="free"><i>⚡</i><span>${esc(caseById(C.free_case_id).name)}</span><small id="q-free">${energy ? energy + " спина" : "скоро"}</small></button>
      <button type="button" class="q ${M.today.wheel_daily || M.today.wheel_weekly_unlocked ? "ready" : ""}" data-q="wheel"><i>◎</i><span>Колесо</span><small>${wheel}</small></button>
      <button type="button" class="q" data-q="tasks"><i>✓</i><span>Задания</span><small>${M.today.quests_done}/3</small></button>
      ${M.luck ? `<button type="button" class="q ready" data-q="luck"><i>🍀</i><span>Удача ×${num(M.luck.mult)}</span><small>${M.luck.spins_left} спинов</small></button>` : ""}`;
    $("cat-chips").innerHTML = [{ id: "all", name: "Все" }].concat(C.categories).map((c) =>
      `<button type="button" class="chip ${ui.cat === c.id ? "on" : ""}" data-cat="${c.id}">${esc(c.name)}</button>`).join("");
    const cats = ui.cat === "all" ? C.categories : C.categories.filter((c) => c.id === ui.cat);
    $("case-groups").innerHTML = cats.map((cat) => {
      const list = C.cases.filter((c) => c.category === cat.id);
      return list.length ? `<h2 class="h-sub">${esc(cat.name)}</h2><div class="case-grid">${list.map(caseCard).join("")}</div>` : "";
    }).join("");
    tickTimer();
  }

  function openCase(id) {
    ui.caseId = id;
    ui.count = 1;
    haptic("tick");
    renderCases();
    $("screens").scrollTop = 0;
  }

  function renderCaseView() {
    $("case-list-view").hidden = true;
    const v = $("case-view");
    v.hidden = false;
    const c = caseById(ui.caseId);
    const pity = M.pity && M.pity[c.id];
    const eff = M.luck && M.luck.cases[c.id];
    const luckOn = eff && eff > 1;
    const energy = M.energy.now + M.bonus_spins;
    const lines = [];
    if (pity) lines.push(`Запрещённое и выше сейчас <b>${num(pity.epic_chance_pct)}%</b>, точно не позже чем через ${pity.epic_in}`);
    if (luckOn) lines.push(`Удача ×${num(M.luck.mult)}${eff < M.luck.mult ? ` (здесь ×${num(eff)}: средний возврат с удачей до ${Math.round(M.luck.max_return * 100)}%)` : ""}: дорогие скины чаще, ещё ${M.luck.spins_left} спинов`);
    else if (M.luck && !c.luck) lines.push(`Удача работает на кейсах до ${money(C.luck.max_case_cents)}`);
    v.innerHTML = `
      <button type="button" class="back" id="cv-back">← Все кейсы</button>
      <div class="case-hero" style="${tc(c.top_tier)}">
        <img src="${esc(c.image_url)}" alt="">
        <div><h1>${esc(c.name)}</h1><p class="stage-sub">Средний дроп ${money(c.ev_cents)} · ${c.items.length} предметов</p>
        <button type="button" class="link" id="cv-odds">Шансы и гарантии</button></div>
      </div>
      ${lines.length ? `<p class="pity">${lines.join(" · ")}</p>` : ""}
      <div class="spin-controls">
        <div class="mult" role="radiogroup" aria-label="Сколько кейсов">${C.spin.counts.map((n) => `<button type="button" data-count="${n}" class="${n === ui.count ? "on" : ""}">×${n}</button>`).join("")}</div>
        <button class="btn-spin" id="spin-btn" type="button"><span>ОТКРЫТЬ</span><em>${money(c.price_cents * ui.count)}</em></button>
        ${c.free ? `<div class="alt-spins">
          <button class="btn-free" id="free-btn" type="button" ${energy >= ui.count ? "" : "disabled"}>Бесплатно · <b>${energy}</b></button>
          ${M.attempts ? `<button class="btn-free" id="attempt-btn" type="button" ${M.attempts >= ui.count ? "" : "disabled"}>Попытка ⭐ · <b>${M.attempts}</b></button>` : ""}</div>` : ""}
        <p class="timer" id="spin-note"></p>
      </div>
      <h2 class="h-sub">Что внутри</h2>
      <div class="grid small">${c.items.map((i) => `<div class="tile" style="${tc(i.tier)}">
        <span class="chance">${pct(i.p)}</span><img src="${esc(i.image_url)}" alt="" loading="lazy">
        <span class="n">${esc(i.name)}</span><span class="p">${money(i.min_cents)}${i.max_cents > i.min_cents ? "+" : ""}</span></div>`).join("")}</div>
      <p class="note">Чем дороже скин, тем реже он выпадает: шанс обратно пропорционален цене. Цена кейса = средний дроп по рынку / 0,8.</p>`;
    v.querySelector("#cv-back").onclick = () => { ui.caseId = null; renderCases(); };
    v.querySelector("#cv-odds").onclick = () => openOdds(c.id);
    v.querySelector(".mult").onclick = (e) => { const b = e.target.closest("[data-count]"); if (b) { ui.count = +b.dataset.count; renderCaseView(); } };
    v.querySelector("#spin-btn").onclick = () => doSpin("coin");
    if (v.querySelector("#free-btn")) v.querySelector("#free-btn").onclick = () => doSpin("free");
    if (v.querySelector("#attempt-btn")) v.querySelector("#attempt-btn").onclick = () => doSpin("attempt");
    tickTimer();
  }

  $("banners").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-ad]");
    if (!b) return;
    try {
      const res = await api.post(`/banners/${b.dataset.ad}/click`);
      const t = api.tg();
      if (t && !window.SKINS_DEMO && t.openTelegramLink && res.url.startsWith("https://t.me/")) t.openTelegramLink(res.url);
      else if (!window.SKINS_DEMO) window.open(res.url, "_blank", "noopener");
      else toast("В Telegram откроется канал " + res.url.replace("https://", ""));
    } catch (err) { handleError(err); }
  });
  $("quick").addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    ({ free: () => openCase(C.free_case_id), wheel: () => openWheel(), tasks: openTasks, luck: () => toast(`Удача: скины дороже кейса выпадают чаще. Работает на кейсах до ${money(C.luck.max_case_cents)}, один спин удачи на кейс`) })[b.dataset.q]();
  });
  $("cat-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-cat]"); if (b) { ui.cat = b.dataset.cat; renderCases(); } });
  $("case-groups").addEventListener("click", (e) => { const b = e.target.closest("[data-case]"); if (b) openCase(b.dataset.case); });

  let meAt = Date.now();
  function tickTimer() {
    if (!M) return;
    const parts = [];
    const c = ui.caseId && caseById(ui.caseId);
    if (!c || c.free) {
      if (M.energy.now >= M.energy.max) parts.push("Бесплатные спины накоплены");
      else if (M.energy.next_at) {
        const left = Math.max(0, Math.round(M.energy.next_at - Date.now() / 1000));
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
        parts.push(`Бесплатный через ${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`);
      }
    }
    if (c) parts.push(`За монеты сегодня ещё ${M.coin_spins_left}`);
    const el = $("spin-note");
    if (el) el.textContent = parts.join(" · ");
    const q = $("q-free");
    if (q && !(M.energy.now + M.bonus_spins) && parts[0]) q.textContent = parts[0].replace("Бесплатный через ", "");
  }
  setInterval(() => {
    tickTimer();
    if (M && M.energy.next_at && Date.now() / 1000 > M.energy.next_at + 1 && Date.now() - meAt > 5000) {
      meAt = Date.now();
      refresh(false).catch(() => {});
    }
  }, 1000);
  setInterval(() => { if (ui.tab === "cases") loadLive(); }, 30000);

  async function doSpin(source) {
    if (busy) return;
    busy = true;
    const btn = $("spin-btn");
    if (btn) btn.disabled = true;
    try {
      const body = { case_id: ui.caseId, source, count: ui.count };
      const res = await withConfirm((c) => api.post("/spin", { ...body, confirmed: c }), `Открыть ×${ui.count}?`, "Открыть");
      if (!res) return;
      haptic("medium");
      const best = res.drops.reduce((a, b) => (rank(b.items[0].tier) > rank(a.items[0].tier) || (b.items[0].tier === a.items[0].tier && b.items[0].price_cents > a.items[0].price_cents) ? b : a));
      await playReel(best);
      await refresh();
      showResults(res.drops.map((d) => d.items[0]));
      api.post("/me/last-drop/ack").catch(() => {});
      loadLive();
      if (M.break_reminder) maybeBreak();
    } catch (err) {
      handleError(err);
    } finally {
      busy = false;
      const b2 = $("spin-btn");
      if (b2) b2.disabled = false;
    }
  }

  // Лента: соседи пришли с сервера (по шансам кейса), остановка там, где решил сервер.
  function playReel(drop) {
    return new Promise((resolve) => {
      const reels = $("reels");
      reels.innerHTML = `<div class="reel"><div class="strip">${drop.reel.map((c) =>
        `<div class="cell" style="${tc(c.tier)}"><img src="${esc(c.image_url)}" alt=""><span>${esc(c.name)}</span></div>`).join("")}</div></div>`;
      $("reel-overlay").hidden = false;
      const strip = reels.querySelector(".strip");
      const cellW = strip.firstElementChild.getBoundingClientRect().width;
      const reelW = reels.firstElementChild.getBoundingClientRect().width;
      const duration = settings.fast ? 600 : 5000;
      const target = -(drop.stop_index * cellW + cellW / 2 - reelW / 2) + (Math.random() - 0.5) * cellW * 0.7;
      let done = false, raf = 0, last = -1;
      const finish = () => {
        if (done) return;
        done = true;
        cancelAnimationFrame(raf);
        strip.style.transition = "none";
        strip.style.transform = `translateX(${target}px)`;
        const t = drop.items[0].tier;
        haptic(rank(t) >= rank("legendary") ? "success" : rank(t) >= rank("epic") ? "heavy" : "light");
        setTimeout(() => { $("reel-overlay").hidden = true; resolve(); }, settings.fast ? 150 : 400);
      };
      $("reel-skip").onclick = finish;
      requestAnimationFrame(() => {
        strip.style.transition = `transform ${duration}ms cubic-bezier(.1,.66,.12,1)`;
        strip.style.transform = `translateX(${target}px)`;
        const watch = () => {
          if (done) return;
          const x = new DOMMatrixReadOnly(getComputedStyle(strip).transform).m41;
          const cell = Math.floor((-x + reelW / 2) / cellW);
          if (cell !== last) { last = cell; haptic("tick"); }
          raf = requestAnimationFrame(watch);
        };
        raf = requestAnimationFrame(watch);
      });
      setTimeout(finish, duration + 50);
    });
  }

  function showResults(items) {
    items = items.slice().sort((a, b) => rank(b.tier) - rank(a.tier) || b.price_cents - a.price_cents);
    const total = items.reduce((s, i) => s + i.price_cents, 0);
    const sellSum = items.reduce((s, i) => s + i.sell_cents, 0);
    const best = items[0];
    const one = items.length === 1;
    const body = openSheet(`
      <div class="tier-label" style="${tc(best.tier)}">${tier(best.tier).icon} ${esc(tier(best.tier).name)}<small>${one ? "шанс 1 из " + best.one_in : "лучший из " + items.length}</small></div>
      <div class="item-hero ${rank(best.tier) >= rank("epic") ? "flash" : ""}" style="${tc(best.tier)}"><img src="${esc(best.image_url)}" alt=""></div>
      ${one ? `<div class="item-name"><b>${best.stattrak ? "StatTrak™ " : ""}${itemTitle(best)}</b><span>${best.wear ? WEAR[best.wear] : ""}${best.float != null ? " · " + best.float.toFixed(4) : ""}</span></div>` : ""}
      <p class="lead center">Дроп на <b>${money(total)}</b></p>
      ${one ? "" : `<div class="results">${items.map((i) => tileHtml(i)).join("")}</div>`}
      <div class="actions">
        <button type="button" id="r-keep">Забрать</button>
        <button type="button" class="primary" id="r-sell">Продать · ${money(sellSum)}</button>
        ${ui.caseId ? `<button type="button" class="wide" id="r-again">Ещё ×${ui.count}</button>` : ""}
      </div>`);
    const res = body.querySelector(".results");
    if (res) res.addEventListener("click", (e) => {
      const t = e.target.closest("[data-item]");
      if (t) showItem(findItem(+t.dataset.item) || items.find((x) => x.id === +t.dataset.item));
    });
    body.querySelector("#r-keep").onclick = closeSheet;
    body.querySelector("#r-sell").onclick = () => sellIds(items.map((i) => i.id), sellSum, true);
    const again = body.querySelector("#r-again");
    if (again) again.onclick = () => { closeSheet(); doSpin("coin"); };
  }

  function tileHtml(it, extra = "") {
    const badges = [it.locked ? "🔒" : "", it.state === "market" ? "⇅" : "", it.overflow_until ? "⚠" : "", it.hold_until ? "⏳" : ""].join("");
    return `<button type="button" class="tile ${extra}" data-item="${it.id}" style="${tc(it.tier)}">
      <span class="badges">${badges}</span>
      <img src="${esc(it.image_url)}" alt="" loading="lazy">
      <span class="w">${wearLine(it) || "&nbsp;"}</span>
      <span class="n">${itemTitle(it)}</span>
      <span class="p">${money(it.price_cents)}</span></button>`;
  }

  // ---------- карточка предмета ----------
  function chartSvg(points) {
    if (!points || points.length < 2) return '<p class="note">График появится, когда наберётся несколько дней цен.</p>';
    const w = 300, h = 70, vals = points.map((p) => p.cents);
    const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
    const xy = points.map((p, i) => [(i / (points.length - 1)) * w, h - 6 - ((p.cents - lo) / span) * (h - 12)]);
    const line = xy.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
    const last = xy[xy.length - 1];
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="Цена за 30 дней">
      <path d="${line} L${w},${h} L0,${h} Z" fill="var(--accent-soft)"/><path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2"/>
      <circle cx="${last[0]}" cy="${last[1]}" r="3.5" fill="var(--accent)"/></svg>
      <div class="chart-axis"><span>${points[0].day.slice(5).split("-").reverse().join(".")}</span><span>${money(lo)} – ${money(hi)}</span><span>сегодня</span></div>`;
  }

  function originLabel(it) {
    if (it.origin.startsWith("case:")) { const c = caseById(it.origin.slice(5)); return c ? "Кейс «" + c.name + "»" : "Кейс"; }
    return { upgrade: "Апгрейды", contract: "Контракт", wheel: "Колесо", raffle: "Розыгрыш", referral: "Награда за друга" }[it.origin] || "Рынок или обмен";
  }

  async function showItem(it, opts = {}) {
    if (!it) return;
    const t = tier(it.tier);
    const growth = it.drop_price_cents ? (it.price_cents - it.drop_price_cents) / it.drop_price_cents : 0;
    const big = rank(it.tier) >= rank("epic");
    const free = usable(it);
    const isMine = !!findItem(it.id);
    const body = openSheet(`
      <div class="tier-label" style="${tc(it.tier)}">${t.icon} ${esc(t.name)}<small>${esc(it.game_rarity)}${it.one_in ? " · 1 из " + it.one_in : ""}</small></div>
      <div class="item-hero ${opts.fresh && big ? "flash" : ""}" style="${tc(it.tier)}"><img src="${esc(it.image_url)}" alt="${esc(it.name)}"></div>
      <div class="item-name"><b>${it.stattrak ? "StatTrak™ " : ""}${itemTitle(it)}</b><span>${it.wear ? WEAR[it.wear] : "Без износа"}</span></div>
      ${it.float != null ? `<div class="floatbar" aria-label="float ${it.float}"><i style="left:calc(${(it.float * 100).toFixed(2)}% - 1px)"></i></div>` : ""}
      ${isMine ? `<div class="actions">
        <button type="button" class="primary" id="i-sell" ${free ? "" : "disabled"}>Продать · ${money(it.sell_cents)}</button>
        <button type="button" id="i-up" ${free && !settings.noUpgrader ? "" : "disabled"}>Апгрейд</button>
        <button type="button" id="i-ct" ${free ? "" : "disabled"}>В контракт</button>
        <button type="button" id="i-market" ${free && !it.hold_until && !it.overflow_until ? "" : "disabled"}>На рынок</button>
      </div>` : ""}
      <div class="stats">
        <div><small>Цена по рынку</small><b>${money(it.price_cents)}</b></div>
        <div><small>С момента дропа</small><b class="${growth > 0 ? "up" : growth < 0 ? "down" : ""}">${growth >= 0 ? "+" : ""}${(growth * 100).toFixed(0)}%</b></div>
        <div><small>Float</small><b>${it.float != null ? it.float.toFixed(6) : "—"}</b></div>
        <div><small>Узор</small><b>${it.pattern}</b></div>
        <div><small>Откуда</small><b class="txt">${esc(originLabel(it))}</b></div>
        <div><small>Получен</small><b>${dayStr(it.created_at)}</b></div>
      </div>
      <div id="i-chart"></div>
      ${it.overflow_until ? `<p class="banner">В переполнении до ${new Date(it.overflow_until * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}. Освободи место, иначе предмет продастся системе по 60%.</p>` : ""}
      ${it.hold_until ? '<p class="note">На удержании 24 часа после покупки или обмена.</p>' : ""}
      ${isMine ? `<div class="actions">
        <button type="button" id="i-show">${(M.showcase || []).includes(it.id) ? "Убрать с витрины" : "На витрину"}</button>
        <button type="button" id="i-lock" ${it.state !== "free" ? "disabled" : ""}>${it.locked ? "Снять замок" : "Замок 🔒"}</button>
        <button type="button" id="i-share" class="wide">Показать другу</button>
      </div>
      <p class="note">Продажа системе — 60% цены, отменить можно 10 минут. Замок защищает от продажи, апгрейда и контракта.</p>` : ""}`);
    if (isMine) {
      body.querySelector("#i-sell").onclick = () => sellIds([it.id], it.sell_cents, rank(it.tier) >= rank("epic"));
      body.querySelector("#i-market").onclick = () => openListLot(it);
      body.querySelector("#i-up").onclick = () => { closeSheet(); Object.assign(ui.up, { items: [it.id], coins: 0, target: null, side: "target" }); switchTab("upgrade"); loadTargets(); };
      body.querySelector("#i-ct").onclick = () => { closeSheet(); if (!ui.ct.items.includes(it.id)) ui.ct.items.push(it.id); switchTab("contract"); };
      body.querySelector("#i-show").onclick = () => toggleShowcase(it);
      body.querySelector("#i-lock").onclick = () => toggleLock(it);
      body.querySelector("#i-share").onclick = () => share(it);
      api.get(`/items/${it.id}`).then((card) => {
        const el = body.querySelector("#i-chart");
        if (el) el.innerHTML = `<h3 class="h-sub">Цена за 30 дней</h3>${chartSvg(card.chart)}`;
      }).catch(() => {});
    }
  }

  async function toggleLock(it) {
    try {
      if (it.locked) {
        const ok = await confirmSheet({ title: "Снять замок?", text: "Предмет снова можно будет продать, поставить в апгрейд или контракт.", ok: "Снять" });
        if (!ok) return;
        await api.post(`/items/${it.id}/unlock`, { confirm: true });
      } else {
        await api.post(`/items/${it.id}/lock`);
      }
      await refresh();
      showItem(findItem(it.id));
    } catch (err) { handleError(err); }
  }

  async function toggleShowcase(it) {
    let ids = (M.showcase || []).slice();
    if (ids.includes(it.id)) ids = ids.filter((x) => x !== it.id);
    else { if (ids.length >= 3) ids.shift(); ids.push(it.id); }
    try {
      await api.post("/showcase", { item_ids: ids });
      await refresh(false);
      toast("Витрина обновлена");
      showItem(findItem(it.id));
    } catch (err) { handleError(err); }
  }

  // Продажа: подтверждение только для групп и для Запрещённого и выше.
  async function sellIds(ids, total, ask) {
    if (!ids.length) return toast("Продавать нечего");
    if (ask || ids.length > 1) {
      const ok = await confirmSheet({ title: ids.length > 1 ? `Продать ${ids.length} шт.?` : "Продать?", text: `Получишь <b>${money(total)}</b>. Отменить можно 10 минут.`, ok: "Продать" });
      if (!ok) return;
    }
    try {
      const res = await api.post("/items/sell", { item_ids: ids, confirmed: true });
      haptic("success");
      closeSheet();
      ui.selected.clear();
      ui.ct.items = ui.ct.items.filter((x) => !ids.includes(x));
      ui.up.items = ui.up.items.filter((x) => !ids.includes(x));
      await refresh();
      toast(`+${money(res.total_cents)}`, { label: "Отменить", run: () => undoSale(res.sale_id) });
    } catch (err) { handleError(err); }
  }

  async function undoSale(id) {
    try {
      await api.post(`/sales/${id}/undo`);
      await refresh();
      toast("Продажа отменена, предметы вернулись");
    } catch (err) { handleError(err); }
  }

  async function share(it) {
    try {
      const res = await api.post(`/share/${it.id}`);
      const t = api.tg();
      if (t && !window.SKINS_DEMO) {
        if (res.image_url && t.shareToStory) {
          t.shareToStory(res.image_url, { text: res.text, widget_link: res.link ? { url: res.link, name: "Открыть" } : undefined });
        } else {
          t.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(res.link || "")}&text=${encodeURIComponent(res.text)}`);
        }
      } else {
        toast("В Telegram откроется сторис с карточкой дропа");
      }
    } catch (err) { handleError(err); }
  }

  function maybeBreak() {
    const key = "break-" + new Date().toDateString();
    try { if (localStorage.getItem(key)) return; localStorage.setItem(key, "1"); } catch (e) { return; }
    openSheet(`<h2>Перерыв?</h2><p>Сегодня ты открыл уже больше 50 кейсов. Бесплатные спины копятся, пока тебя нет.</p>
      <div class="actions"><button type="button" class="primary wide" id="br-ok">Понятно</button></div>`).querySelector("#br-ok").onclick = closeSheet;
  }

  // ---------- АПГРЕЙДЫ ----------
  function upStake() {
    ui.up.items = ui.up.items.filter((id) => { const i = findItem(id); return i && usable(i); });
    const items = ui.up.items.map(findItem);
    return { items, sum: items.reduce((s, i) => s + i.price_cents, 0) + ui.up.coins };
  }
  function upChance(stake, target) {
    if (!target || target.price_cents <= stake || stake <= 0) return 0;
    return Math.min(C.upgrader.max_chance_pct / 100, (stake / target.price_cents) * C.upgrader.k * (1 + (M.upgrade_bonus_pct || 0) / 100));
  }

  function renderUpgrade() {
    if (!INV) return;
    const { items, sum } = upStake();
    const box = $("up-stake-items");
    box.className = "up-items" + (items.length > 1 ? " many" : "");
    box.innerHTML = items.length
      ? items.map((i) => `<img src="${esc(i.image_url)}" alt="">`).join("") + (items.length === 1 ? `<span class="nm">${itemTitle(items[0])}</span>` : "") + (ui.up.coins ? `<span class="nm">+ ${money(ui.up.coins)}</span>` : "")
      : ui.up.coins ? `<span class="nm">${money(ui.up.coins)} монетами</span>` : '<span class="plus">+</span>';
    $("up-stake-sum").textContent = money(sum);
    const t = ui.up.target;
    $("up-target-items").innerHTML = t ? `<img src="${esc(t.image_url)}" alt=""><span class="nm">${t.stattrak ? "ST™ " : ""}${esc(t.name)}${t.wear ? " · " + t.wear : ""}</span>` : '<span class="plus">?</span>';
    $("up-target-sum").textContent = t ? money(t.price_cents) : "—";
    const chance = upChance(sum, t);
    $("up-chance").textContent = pct(chance, 2);
    $("up-arc").setAttribute("stroke-dasharray", `${chance * 1000} 1000`);
    const bonus = M.upgrade_bonus_pct ? ` × ${(1 + M.upgrade_bonus_pct / 100).toFixed(2).replace(".", ",")} бонус` : "";
    $("up-formula").innerHTML = t ? `<b>${money(sum)}</b> ÷ <b>${money(t.price_cents)}</b> × 0,8${bonus} = <b>${pct(chance, 2)}</b>`
      : "Шанс = ставка ÷ цель × 0,8, не больше 80%. Выбери ставку, потом цель.";
    document.querySelectorAll("#up-seg button").forEach((b) => b.classList.toggle("on", b.dataset.side === ui.up.side));
    $("up-stake-pane").hidden = ui.up.side !== "stake";
    $("up-target-pane").hidden = ui.up.side !== "target";
    if (ui.up.side === "stake") {
      const coins = $("up-coins");
      if (document.activeElement !== coins) coins.value = ui.up.coins ? fromCents(ui.up.coins) : "";
      const list = INV.items.filter(usable).sort((a, b) => b.price_cents - a.price_cents);
      $("up-inv").innerHTML = list.length ? list.map((i) => tileHtml(i, ui.up.items.includes(i.id) ? "sel" : "")).join("")
        : '<p class="empty">Свободных предметов нет — поставь монеты.</p>';
    } else {
      $("up-mults").innerHTML = C.upgrader.multipliers.map((m) => `<button type="button" data-m="${m}" class="${ui.up.mult === m ? "on" : ""}">×${num(m)}</button>`).join("");
      $("up-targets").innerHTML = sum > 0 ? (ui.up.targets.length ? ui.up.targets.map((x, i) => `<button type="button" class="tile ${t && t.catalog_item_id === x.catalog_item_id && t.wear === x.wear && t.stattrak === x.stattrak ? "sel" : ""}" data-t="${i}" style="${tc(x.tier)}">
        <span class="chance">${pct(upChance(sum, x), 1)}</span><img src="${esc(x.image_url)}" alt="" loading="lazy"><span class="w">${x.stattrak ? '<span class="st">ST™</span> ' : ""}${x.wear || ""}</span><span class="n">${esc(x.name)}</span><span class="p">${money(x.price_cents)}</span></button>`).join("")
        : '<p class="empty">Подбираем цели…</p>') : '<p class="empty">Сначала выбери ставку.</p>';
    }
    $("up-go").disabled = !(chance > 0) || ui.up.spinning || M.upgrades_left <= 0;
    $("up-note").textContent = `Сегодня ещё ${M.upgrades_left} из ${C.upgrader.per_day}. При неудаче ставка сгорает, возвращаются запчасти на 10%, а следующий апгрейд получает +3% к шансу.`;
  }

  let targetsReq = 0;
  async function loadTargets() {
    const { sum } = upStake();
    const req = ++targetsReq;
    if (sum <= 0) { ui.up.targets = []; ui.up.target = null; renderUpgrade(); return; }
    try {
      const res = await api.get(`/upgrades/targets?stake_cents=${sum}&mult=${ui.up.mult}`);
      if (req !== targetsReq) return;
      ui.up.targets = res.targets;
      if (!ui.up.target || ui.up.target.price_cents <= sum) ui.up.target = res.targets[0] || null;
    } catch (err) { handleError(err); }
    renderUpgrade();
  }

  $("up-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-side]"); if (!b) return; ui.up.side = b.dataset.side; renderUpgrade(); });
  $("up-stake").addEventListener("click", () => { ui.up.side = "stake"; renderUpgrade(); });
  $("up-target").addEventListener("click", () => { ui.up.side = "target"; if (!ui.up.targets.length) loadTargets(); renderUpgrade(); });
  $("up-inv").addEventListener("click", (e) => {
    const t = e.target.closest("[data-item]");
    if (!t) return;
    const id = +t.dataset.item;
    const k = ui.up.items.indexOf(id);
    if (k >= 0) ui.up.items.splice(k, 1);
    else if (ui.up.items.length < C.upgrader.max_items) ui.up.items.push(id);
    else return toast(`В ставку до ${C.upgrader.max_items} предметов`);
    haptic("tick");
    ui.up.target = null;
    renderUpgrade();
    loadTargets();
  });
  let coinsTimer;
  $("up-coins").addEventListener("input", (e) => {
    ui.up.coins = Math.max(0, Math.min(toCents(e.target.value), M.balance_cents));
    ui.up.target = null;
    clearTimeout(coinsTimer);
    coinsTimer = setTimeout(loadTargets, 300);
    renderUpgrade();
  });
  $("up-coins-max").addEventListener("click", () => { ui.up.coins = M.balance_cents; ui.up.target = null; renderUpgrade(); loadTargets(); });
  $("up-mults").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (!b) return; ui.up.mult = +b.dataset.m; ui.up.target = null; loadTargets(); });
  $("up-targets").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (!b) return; ui.up.target = ui.up.targets[+b.dataset.t]; haptic("tick"); renderUpgrade(); });

  $("up-go").addEventListener("click", async () => {
    const { sum } = upStake();
    const t = ui.up.target;
    if (!t) return;
    const target = { catalog_item_id: t.catalog_item_id, wear: t.wear, stattrak: t.stattrak };
    ui.up.spinning = true;
    renderUpgrade();
    try {
      const quote = await api.post("/upgrades/quote", { stake_item_ids: ui.up.items, stake_coins_cents: ui.up.coins, target });
      if (quote.confirm_required) {
        const ok = await confirmSheet({ title: "Точно?", text: `Ставка ${money(sum)} сгорит с шансом ${pct(1 - quote.chance_pct / 100, 1)}.`, ok: "Апгрейд" });
        if (!ok) return;
      }
      const res = await api.post("/upgrades", { stake_item_ids: ui.up.items, stake_coins_cents: ui.up.coins, target, quote_chance_pct: quote.chance_pct, confirmed: true });
      await spinNeedle(res.roll / 100);
      haptic(res.success ? "success" : "error");
      Object.assign(ui.up, { items: [], coins: 0, target: null, targets: [], side: "stake" });
      await refresh();
      if (res.success) showItem(res.item, { fresh: true });
      else openSheet(`<h2>Не получилось</h2><p>Шанс был ${num(res.chance_pct)}%, стрелка остановилась на ${num(res.roll.toFixed(1))}%.</p>
        <p>Запчасти: <b>+${money(res.parts_cents)}</b>. Следующий апгрейд получит +${res.next_bonus_pct}% к шансу.</p>
        <div class="actions"><button type="button" class="primary wide" id="up-ok">Понятно</button></div>`).querySelector("#up-ok").onclick = closeSheet;
    } catch (err) {
      handleError(err);
    } finally {
      ui.up.spinning = false;
      renderUpgrade();
    }
  });

  function spinNeedle(roll) {
    return new Promise((resolve) => {
      const n = $("up-needle");
      n.style.transition = "none";
      n.style.transform = "rotate(0deg)";
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const ms = settings.fast ? 500 : 3200;
        n.style.transition = `transform ${ms}ms cubic-bezier(.15,.7,.15,1)`;
        n.style.transform = `rotate(${360 * 4 + roll * 360}deg)`;
        setTimeout(resolve, ms + 200);
      }));
    });
  }

  // ---------- КОНТРАКТЫ ----------
  function contractRange(total) {
    const t = C.contracts.table;
    return { min: Math.round(total * Math.min(...t.map((x) => x.mult))), max: Math.round(total * Math.max(...t.map((x) => x.mult))),
      ev: Math.round(total * t.reduce((s, x) => s + x.mult * x.p, 0)) };
  }

  function renderContract() {
    if (!INV) return;
    const cc = C.contracts;
    const pool = INV.items.filter(usable);
    ui.ct.items = ui.ct.items.filter((id) => pool.some((i) => i.id === id));
    const chosen = ui.ct.items.map((id) => pool.find((i) => i.id === id));
    $("ct-count").textContent = `${chosen.length} / ${cc.max_items}`;
    $("ct-slots").innerHTML = Array.from({ length: cc.max_items }, (_, k) => chosen[k]
      ? `<button type="button" class="ct-slot full" data-rm="${chosen[k].id}" style="${tc(chosen[k].tier)}"><img src="${esc(chosen[k].image_url)}" alt=""></button>`
      : `<div class="ct-slot ${k < cc.min_items ? "need" : ""}"><span class="plus small">+</span></div>`).join("");
    const total = chosen.reduce((s, i) => s + i.price_cents, 0);
    const r = contractRange(total);
    const floats = chosen.filter((i) => i.float != null).map((i) => i.float);
    const avg = floats.length ? floats.reduce((s, x) => s + x, 0) / floats.length : null;
    const st = chosen.length && chosen.every((i) => i.stattrak);
    const tooMuch = total > cc.max_total_cents;
    $("ct-sum").innerHTML = chosen.length ? `
      <div class="stats">
        <div><small>Вклад</small><b>${money(total)}</b></div>
        <div><small>Результат</small><b>${money(r.min)} – ${money(r.max)}</b></div>
        <div><small>В среднем</small><b>${money(r.ev)}</b></div>
        <div><small>Средний float</small><b>${avg != null ? avg.toFixed(4) : "—"}${st ? " · ST™" : ""}</b></div>
      </div>${tooMuch ? `<p class="banner">Контракт дороже ${money(cc.max_total_cents)} не собрать: убери самый дорогой предмет.</p>` : ""}`
      : '<p class="lead">Выбери предметы ниже — любой редкости, хоть ширпотреб с ножом.</p>';
    const tiers = DROP_TIERS.filter((t) => pool.some((i) => i.tier === t));
    $("ct-chips").innerHTML = [["all", "Все"]].concat(tiers.map((t) => [t, tier(t).name])).map(([id, label]) =>
      `<button type="button" class="chip ${ui.ct.tier === id ? "on" : ""}" data-ct="${id}" ${id !== "all" ? `style="${tc(id)}"` : ""}>${id !== "all" ? `<i>${tier(id).icon}</i>` : ""}${esc(label)}</button>`).join("");
    const list = pool.filter((i) => ui.ct.tier === "all" || i.tier === ui.ct.tier).sort((a, b) => b.price_cents - a.price_cents);
    $("ct-grid").innerHTML = list.length ? list.map((i) => tileHtml(i, ui.ct.items.includes(i.id) ? "sel" : "")).join("") : '<p class="empty">Свободных предметов нет. Открой кейсы.</p>';
    $("ct-go").disabled = chosen.length < cc.min_items || tooMuch;
    $("ct-note").innerHTML = `Цена результата = вклад × множитель: ${cc.table.map((x) => `×${num(x.mult)} ${pct(x.p, 1)}`).join(", ")}. Float результата — средний float вклада, StatTrak™ — если весь вклад StatTrak™.`;
  }
  $("ct-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-ct]"); if (!b) return; ui.ct.tier = b.dataset.ct; renderContract(); });
  $("ct-clear").addEventListener("click", () => { ui.ct.items = []; renderContract(); });
  $("ct-slots").addEventListener("click", (e) => { const b = e.target.closest("[data-rm]"); if (!b) return; ui.ct.items = ui.ct.items.filter((x) => x !== +b.dataset.rm); renderContract(); });
  $("ct-grid").addEventListener("click", (e) => {
    const t = e.target.closest("[data-item]");
    if (!t) return;
    const id = +t.dataset.item;
    const i = ui.ct.items.indexOf(id);
    if (i >= 0) ui.ct.items.splice(i, 1);
    else if (ui.ct.items.length < C.contracts.max_items) ui.ct.items.push(id);
    else return toast(`В контракт до ${C.contracts.max_items} предметов`);
    haptic("tick");
    renderContract();
  });
  $("ct-go").addEventListener("click", async () => {
    try {
      const res = await withConfirm((c) => api.post("/contracts", { item_ids: ui.ct.items, confirmed: c }), "Подписать контракт?", "Подписать");
      if (!res) return;
      haptic(res.mult >= 1 ? "success" : "warning");
      ui.ct.items = [];
      await refresh();
      await showItem(res.item, { fresh: true });
      toast(`Контракт ×${num(res.mult)}: ${money(res.total_cents)} → ${money(res.item.price_cents)}`);
    } catch (err) { handleError(err); }
  });

  // ---------- ИНВЕНТАРЬ ----------
  function renderInventory() {
    if (!INV) return;
    const s = INV.summary;
    $("col-slots").textContent = `${s.used} / ${s.total}`;
    $("col-value").textContent = money(s.value_cents);
    $("col-week").textContent = s.week_change_cents ? `${s.week_change_cents > 0 ? "+" : ""}${money(s.week_change_cents)} за неделю` : "";
    $("col-week").className = s.week_change_cents > 0 ? "up" : s.week_change_cents < 0 ? "down" : "";
    $("col-select").textContent = ui.select ? "Готово" : "Выбрать";
    const ob = $("overflow-banner");
    ob.hidden = !s.overflow;
    ob.textContent = s.overflow ? `Переполнение: ${s.overflow} из ${C.inventory.overflow_slots}. Освободи место за 48 часов, иначе эти предметы продадутся системе по 60%.` : "";
    const items = INV.items;
    const groups = C.sell_quick.map((g) => {
      const list = items.filter((i) => sellable(i) && (g.id === "all" || i.tier === g.id));
      return { ...g, n: list.length, sum: list.reduce((a, i) => a + i.sell_cents, 0) };
    }).filter((g) => g.n);
    $("sell-quick").hidden = !groups.length;
    $("sell-quick").innerHTML = groups.length ? `<span class="sq-label">Быстрая продажа</span>` + groups.map((g) =>
      `<button type="button" class="sq ${g.id === "all" ? "all" : ""}" data-sg="${g.id}" ${g.id !== "all" ? `style="${tc(g.id)}"` : ""}><b>${esc(g.label)}</b><small>${g.n} шт. · ${money(g.sum)}</small></button>`).join("") : "";
    const chips = [{ id: "all", label: "Все" }]
      .concat(DROP_TIERS.filter((t) => items.some((i) => i.tier === t)).map((t) => ({ id: t, label: tier(t).name, icon: tier(t).icon, tier: t })))
      .concat(items.some((i) => i.stattrak) ? [{ id: "st", label: "StatTrak™" }] : [])
      .concat(items.some((i) => i.locked) ? [{ id: "locked", label: "🔒 Под замком" }] : [])
      .concat(items.some((i) => i.state === "market") ? [{ id: "market", label: "⇅ На рынке" }] : [])
      .concat(items.some((i) => i.overflow_until) ? [{ id: "overflow", label: "⚠ Переполнение" }] : []);
    $("col-chips").innerHTML = chips.map((c) => `<button type="button" class="chip ${ui.colFilter === c.id ? "on" : ""}" data-f="${c.id}" ${c.tier ? `style="${tc(c.tier)}"` : ""}>${c.icon ? `<i>${c.icon}</i>` : ""}${esc(c.label)}</button>`).join("");
    let list = items.filter((i) => ui.colFilter === "all" || i.tier === ui.colFilter || (ui.colFilter === "st" && i.stattrak)
      || (ui.colFilter === "locked" && i.locked) || (ui.colFilter === "market" && i.state === "market") || (ui.colFilter === "overflow" && i.overflow_until));
    const q = ui.colSearch.trim().toLowerCase();
    if (q) list = list.filter((i) => i.name.toLowerCase().includes(q));
    const growth = (i) => (i.price_cents - i.drop_price_cents) / (i.drop_price_cents || 1);
    const sorters = { new: (a, b) => b.id - a.id, price: (a, b) => b.price_cents - a.price_cents, cheap: (a, b) => a.price_cents - b.price_cents,
      tier: (a, b) => rank(b.tier) - rank(a.tier) || b.price_cents - a.price_cents, growth: (a, b) => growth(b) - growth(a) };
    list.sort(sorters[ui.colSort]);
    for (const id of [...ui.selected]) if (!items.some((i) => i.id === id && usable(i))) ui.selected.delete(id);
    $("col-grid").innerHTML = list.map((i) => tileHtml(i, ui.select ? (ui.selected.has(i.id) ? "sel" : usable(i) ? "" : "dim") : "")).join("");
    $("col-empty").hidden = items.length > 0;
    renderSelbar();
  }

  function renderSelbar() {
    const on = ui.tab === "inventory" && ui.select;
    $("selbar").hidden = !on;
    if (!on) return;
    const sel = [...ui.selected].map(findItem).filter(Boolean);
    $("sel-info").innerHTML = `<b>${sel.length} шт.</b><small>${money(sel.reduce((s, i) => s + i.sell_cents, 0))}</small>`;
    ["sel-ct", "sel-up", "sel-sell"].forEach((id) => ($(id).disabled = !sel.length));
  }

  $("col-select").addEventListener("click", () => { ui.select = !ui.select; ui.selected.clear(); renderInventory(); });
  $("col-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-f]"); if (b) { ui.colFilter = b.dataset.f; renderInventory(); } });
  $("col-search").addEventListener("input", (e) => { ui.colSearch = e.target.value; renderInventory(); });
  $("col-sort").addEventListener("change", (e) => { ui.colSort = e.target.value; renderInventory(); });
  $("col-grid").addEventListener("click", (e) => {
    const t = e.target.closest("[data-item]");
    if (!t) return;
    const it = findItem(+t.dataset.item);
    if (!ui.select) return showItem(it);
    if (!usable(it)) return toast("Этот предмет сейчас занят или под замком");
    if (ui.selected.has(it.id)) ui.selected.delete(it.id); else ui.selected.add(it.id);
    haptic("tick");
    t.classList.toggle("sel", ui.selected.has(it.id));
    renderSelbar();
  });
  $("sell-quick").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sg]");
    if (!b) return;
    try {
      const q = await api.post("/items/sell-group/quote", { group: b.dataset.sg });
      const label = C.sell_quick.find((g) => g.id === b.dataset.sg).label;
      if (!q.item_ids.length) return toast("Продавать нечего");
      const ok = await confirmSheet({ title: `${label}: продать ${q.count} шт.?`, text: `Получишь <b>${money(q.total_cents)}</b> (60% цены). Предметы под замком не продаются. Отменить можно 10 минут.`, ok: "Продать" });
      if (!ok) return;
      const res = await api.post("/items/sell", { item_ids: q.item_ids, confirmed: true });
      haptic("success");
      await refresh();
      toast(`+${money(res.total_cents)}`, { label: "Отменить", run: () => undoSale(res.sale_id) });
    } catch (err) { handleError(err); }
  });
  $("sel-sell").addEventListener("click", () => {
    const sel = [...ui.selected].map(findItem).filter(Boolean);
    sellIds(sel.map((i) => i.id), sel.reduce((s, i) => s + i.sell_cents, 0), true).then(() => { if (!ui.selected.size) { ui.select = false; renderInventory(); } });
  });
  $("sel-ct").addEventListener("click", () => {
    const ids = [...ui.selected].slice(0, C.contracts.max_items);
    ui.ct.items = ids;
    ui.select = false;
    ui.selected.clear();
    switchTab("contract");
  });
  $("sel-up").addEventListener("click", () => {
    Object.assign(ui.up, { items: [...ui.selected].slice(0, C.upgrader.max_items), coins: 0, target: null, side: "target" });
    ui.select = false;
    ui.selected.clear();
    switchTab("upgrade");
    loadTargets();
  });

  // ---------- колёса, задания, розыгрыш ----------
  function wheelSvg(sectors) {
    const n = sectors.length, R = 120, cx = 130, cy = 130;
    return sectors.map((s, i) => {
      const a0 = (i / n) * 2 * Math.PI - Math.PI / 2, a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2;
      const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0), x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
      const am = (a0 + a1) / 2, lx = cx + R * 0.62 * Math.cos(am), ly = cy + R * 0.62 * Math.sin(am);
      const rare = s.p <= 2;
      return `<path d="M${cx},${cy} L${x0},${y0} A${R},${R} 0 0 1 ${x1},${y1} Z" fill="${rare ? "var(--accent)" : i % 2 ? "var(--bg)" : "var(--surface-2)"}" stroke="var(--line)"/>
        <text x="${lx}" y="${ly}" fill="var(--fg)" font-size="11" font-weight="700" text-anchor="middle" dominant-baseline="middle" transform="rotate(${(am * 180) / Math.PI + 90} ${lx} ${ly})">${esc(s.label)}</text>`;
    }).join("");
  }

  function openWheel(kind) {
    kind = kind === "weekly" ? "weekly" : kind === "daily" ? "daily" : (M.today.wheel_daily || !M.today.wheel_weekly_unlocked ? "daily" : "weekly");
    const sectors = C.wheel[kind];
    const available = kind === "daily" ? M.today.wheel_daily : M.today.wheel_weekly_unlocked;
    const prize = (s) => (s.cases ? ` · ${money(Math.round(C.unit_cents * s.cases))}` : "");
    const body = openSheet(`
      <div class="seg small"><button type="button" data-k="daily" class="${kind === "daily" ? "on" : ""}">Колесо дня</button>
        <button type="button" data-k="weekly" class="${kind === "weekly" ? "on" : ""}">Недельное</button></div>
      <p class="lead">${kind === "daily" ? "Один бесплатный поворот в сутки, пустых секторов нет." : `Открывается, если задания выполнены в ${C.wheel.weekly_unlock_days} днях из 7. Сейчас: ${M.today.quest_days_week} из 7.`}</p>
      <div class="wheel-wrap"><span class="pin"></span><svg viewBox="0 0 260 260"><g id="wheel-g">${wheelSvg(sectors)}</g><circle cx="130" cy="130" r="16" fill="var(--surface)" stroke="var(--line)"/></svg></div>
      <button type="button" class="btn-spin small" id="w-go" ${available ? "" : "disabled"}><span>${available ? "КРУТИТЬ" : kind === "daily" ? "ЗАВТРА СНОВА" : "ПОКА ЗАКРЫТО"}</span></button>
      <table class="odds-table"><tr><th>Приз</th><th>Шанс</th></tr>${sectors.map((s) => `<tr><td>${esc(s.label)}${prize(s)}</td><td>${s.p}%</td></tr>`).join("")}</table>`);
    body.querySelectorAll("[data-k]").forEach((b) => (b.onclick = () => openWheel(b.dataset.k)));
    body.querySelector("#w-go").onclick = async (e) => {
      e.currentTarget.disabled = true;
      try {
        const res = await api.post(`/wheel/${kind}`);
        const idx = sectors.findIndex((s) => s.id === res.sector);
        const deg = 360 * 5 + (360 - (idx + 0.5) * (360 / sectors.length));
        const g = body.querySelector("#wheel-g");
        g.style.transition = `transform ${settings.fast ? 500 : 3500}ms cubic-bezier(.15,.7,.15,1)`;
        g.style.transform = `rotate(${deg}deg)`;
        setTimeout(async () => {
          haptic("success");
          await refresh();
          if (res.item) showItem(res.item, { fresh: true });
          else toast(res.coins_cents ? `+${money(res.coins_cents)}` : res.spins ? `+${res.spins} бесплатных спина` : `Новая рамка: ${C.cosmetics[res.cosmetic].title}`);
        }, settings.fast ? 550 : 3600);
      } catch (err) { handleError(err); }
    };
  }

  async function openTasks() {
    try {
      const q = await api.get("/quests");
      const row = (t) => `<div class="task"><b>${esc(t.title)}</b>
        <small>${t.progress}/${t.target} · ${money(t.reward_cents)}${t.tickets ? ` + ${t.tickets} билет` : ""}${t.cosmetic ? " + рамка" : ""}</small>
        <div class="bar"><i style="width:${(t.progress / t.target) * 100}%"></i></div>
        <button type="button" data-q="${t.code}" ${t.claimed || t.progress < t.target ? "disabled" : ""}>${t.claimed ? "Получено" : "Забрать"}</button></div>`;
      const body = openSheet(`
        <h2>Задания</h2>
        <p class="lead">Дневные обновляются в полночь по Москве, каждое даёт билет в розыгрыш. Задания в ${q.weekly_wheel_unlock_days} днях из 7 открывают недельное колесо: сейчас ${q.week_days} из 7.</p>
        <h3 class="h-sub">Сегодня</h3>${q.daily.map(row).join("")}
        <h3 class="h-sub">Неделя</h3>${q.weekly.map(row).join("")}`);
      body.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-q]");
        if (!b) return;
        try {
          const res = await api.post(`/quests/${b.dataset.q}/claim`);
          haptic("success");
          toast(`+${money(res.coins_cents)}${res.tickets ? " и билет в розыгрыш" : ""}`);
          await refresh(false);
          openTasks();
        } catch (err) { handleError(err); }
      });
    } catch (err) { handleError(err); }
  }

  async function openRaffle() {
    try {
      const r = await api.get("/raffle/today");
      const prev = r.previous;
      openSheet(`
        <h2>Розыгрыш</h2>
        <p class="lead">Участие бесплатное: билеты только за задания, купить их нельзя. Каждый день в ${r.draw_hour_msk}:00 по Москве призы среди всех, у кого есть билеты.</p>
        <div class="stats"><div><small>Мои билеты</small><b>${r.my_tickets}</b></div><div><small>Всего билетов</small><b>${r.total_tickets}</b></div></div>
        <table class="odds-table"><tr><th>Приз</th><th>Сколько</th></tr>${r.prizes.map((p) => `<tr><td>${esc(p.label)}</td><td>${p.count}</td></tr>`).join("")}</table>
        <p class="note">Хеш зерна опубликован заранее:<br><span class="mono">${esc(r.seed_hash)}</span><br>После розыгрыша зерно раскрывается, победителей можно пересчитать.</p>
        ${prev ? `<h3 class="h-sub">Вчера</h3><p class="note">Зерно: <span class="mono">${esc(prev.seed)}</span></p>
          <ol class="board">${prev.winners.map((w) => `<li><span>${esc(w.name)}</span><b>${esc(w.prize)}</b></li>`).join("") || "<li>Билетов не было</li>"}</ol>` : ""}`);
    } catch (err) { handleError(err); }
  }

  // ---------- ЕЩЁ: профиль и плитки ----------
  const MENU = [
    ["market", "⇅", "Рынок", "лоты игроков"],
    ["trades", "⇄", "Обмены", "с друзьями"],
    ["duels", "⚔", "Дуэли", "без ставок"],
    ["friends", "☺", "Друзья", "награды за друзей"],
    ["rating", "★", "Рейтинг", "друзья и все"],
    ["wheel", "◎", "Колесо", "раз в день"],
    ["tasks", "✓", "Задания", "монеты и билеты"],
    ["raffle", "✦", "Розыгрыш", "каждый вечер"],
    ["shop", "⭐", "Магазин", "удача ×2…×10, VIP"],
    ["fair", "⚖", "Честная игра", "проверь бросок"],
    ["odds", "%", "Шансы", "кейсы и износ"],
    ["cosmetics", "◇", "Рамки", "косметика"],
    ["country", "⌖", "Страна", "валюта показа"],
    ["settings", "⚙", "Настройки", "анимации"],
    ["terms", "§", "Условия", "что можно"],
  ];

  function renderMore() {
    $("more-home").hidden = false;
    $("more-page").hidden = true;
    const u = M.user;
    const xpPct = Math.min(100, ((u.xp - u.xp_prev) / Math.max(1, u.xp_next - u.xp_prev)) * 100);
    const show = (M.showcase || []).map(findItem).filter(Boolean);
    $("profile").innerHTML = `
      <div class="pc-head ${u.frame ? "framed" : ""}" style="${u.frame ? tc(C.cosmetics[u.frame].tier) : ""}">
        <div><b>${esc(u.name || "Игрок")}</b><small>ID ${u.id}${u.vip_until ? " · VIP" : ""} · ${esc(C.currency.countries[M.country].name)}</small></div>
        <div class="lvl"><b>Ур. ${u.level}</b><div class="bar"><i style="width:${xpPct}%"></i></div><small>${u.xp} / ${u.xp_next} XP</small></div>
      </div>
      <div class="pc-stats"><div><small>Кейсов</small><b>${M.total_spins}</b></div><div><small>Предметов</small><b>${INV ? INV.summary.used : 0}</b></div><div><small>Инвентарь</small><b>${INV ? money(INV.summary.value_cents) : "—"}</b></div></div>
      ${show.length ? `<div class="showcase">${show.map((i) => tileHtml(i)).join("")}</div>` : ""}`;
    $("more-menu").innerHTML = MENU.map(([id, ico, t, s]) => `<button type="button" data-open="${id}"><i>${ico}</i><b>${t}</b><small>${s}</small></button>`).join("");
  }
  $("profile").addEventListener("click", (e) => { const t = e.target.closest("[data-item]"); if (t) showItem(findItem(+t.dataset.item)); });
  $("more-menu").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    haptic("tick");
    const pages = { market: renderMarket, trades: renderTrades, duels: renderDuels, friends: renderFriends, rating: renderRating };
    if (pages[b.dataset.open]) { ui.page = b.dataset.open; pages[ui.page](); $("screens").scrollTop = 0; return; }
    ({ wheel: () => openWheel(), tasks: openTasks, raffle: openRaffle, shop: openShop, fair: openFair, odds: () => openOdds(ui.caseId || C.free_case_id),
      cosmetics: openCosmetics, country: openCountry, settings: openSettings, terms: openTerms })[b.dataset.open]();
  });

  function page(title, html) {
    $("more-home").hidden = true;
    const p = $("more-page");
    p.hidden = false;
    p.innerHTML = `<button type="button" class="back" id="pg-back">← Ещё</button><h1 class="pg-title">${esc(title)}</h1>${html}`;
    p.querySelector("#pg-back").onclick = () => { ui.page = null; renderMore(); };
    return p;
  }

  // ---------- рынок ----------
  function lotRow(l, action) {
    const it = l.item;
    return `<div class="lot" style="${tc(it.tier)}">
      <img src="${esc(it.image_url)}" alt="" loading="lazy">
      <div class="lot-main"><b>${it.stattrak ? "ST™ " : ""}${itemTitle(it)}</b>
        <small>${it.wear || ""} · ${esc(tier(it.tier).name)} · ${esc(l.seller_name)}</small>
        <small>${l.price_pct}% справочной</small></div>
      <div class="lot-side"><em>${money(l.price_cents)}</em>${action}</div></div>`;
  }

  async function renderMarket() {
    const views = [["all", "Витрина"], ["mine", "Мои лоты"], ["orders", "Заявки"], ["deals", "Сделки"]];
    const p = page("Рынок", `
      <div class="seg small" id="lots-seg">${views.map(([id, l]) => `<button type="button" data-view="${id}" class="${ui.market === id ? "on" : ""}">${l}</button>`).join("")}</div>
      ${ui.market === "all" ? `<div class="chips" id="lots-chips">${[["all", "Все"]].concat(DROP_TIERS.map((t) => [t, tier(t).name])).map(([id, label]) =>
        `<button type="button" class="chip ${(ui.lotsTier || "all") === id ? "on" : ""}" data-t="${id}" ${id !== "all" ? `style="${tc(id)}"` : ""}>${esc(label)}</button>`).join("")}</div>` : ""}
      <div id="lots-list" class="lots"><p class="empty">Загрузка…</p></div>
      <p class="note">Цена лота от 50% до 150% справочной, комиссия 10% (у VIP 5%) уходит из игры. Купленный предмет 24 часа нельзя перепродать. Выставить предмет — из его карточки.</p>`);
    p.querySelector("#lots-seg").onclick = (e) => { const b = e.target.closest("[data-view]"); if (b) { ui.market = b.dataset.view; renderMarket(); } };
    const chips = p.querySelector("#lots-chips");
    if (chips) chips.onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) { ui.lotsTier = b.dataset.t === "all" ? null : b.dataset.t; renderMarket(); } };
    const list = p.querySelector("#lots-list");
    try {
      if (ui.market === "orders") {
        const { orders } = await api.get("/market/orders");
        list.innerHTML = orders.length ? orders.map((o) => `<div class="lot" style="${tc(o.tier)}"><img src="${esc(o.image_url)}" alt="">
          <div class="lot-main"><b>${o.stattrak ? "ST™ " : ""}${esc(o.name)}</b><small>${o.wear || ""} · ${o.status === "active" ? "ждёт продавца" : o.status === "filled" ? "исполнена" : "отменена"}</small></div>
          <div class="lot-side"><em>до ${money(o.max_price_cents)}</em>${o.status === "active" ? `<button type="button" class="mini" data-cancel-order="${o.id}">Снять</button>` : ""}</div></div>`).join("")
          : '<p class="empty">Заявок нет. Открой лот на витрине и нажми «Хочу дешевле».</p>';
      } else if (ui.market === "deals") {
        const { deals } = await api.get("/market/deals");
        list.innerHTML = deals.length ? deals.map((d) => lotRow(d, `<small class="${d.side === "sell" ? "up" : ""}">${d.side === "sell" ? "продал" : "купил"} ${dayStr(d.sold_at)}</small>`)).join("")
          : '<p class="empty">Сделок пока не было.</p>';
      } else {
        const mine = ui.market === "mine";
        const { lots } = await api.get("/market/lots" + (mine ? "?mine=1" : `?sort=new${ui.lotsTier ? "&tier=" + ui.lotsTier : ""}`));
        list.innerHTML = lots.length ? lots.map((l) => lotRow(l, mine ? `<button type="button" class="mini" data-cancel="${l.id}">Снять</button>`
          : l.seller_id === M.user.id ? "<small>твой лот</small>" : `<button type="button" class="mini primary" data-lot="${l.id}">Купить</button>`)).join("")
          : `<p class="empty">${mine ? "Лотов нет. Выставить предмет можно из его карточки в Инвентаре." : "На витрине пока пусто."}</p>`;
        list.querySelectorAll("[data-lot]").forEach((b) => (b.onclick = () => openLot(lots.find((l) => l.id === +b.dataset.lot))));
      }
    } catch (err) { handleError(err); }
    list.addEventListener("click", async (e) => {
      const c = e.target.closest("[data-cancel]");
      const o = e.target.closest("[data-cancel-order]");
      try {
        if (c) { await api.del(`/market/lots/${c.dataset.cancel}`); toast("Лот снят"); await refresh(); renderMarket(); }
        if (o) { await api.del(`/market/orders/${o.dataset.cancelOrder}`); toast("Заявка снята, монеты вернулись"); await refresh(false); renderMarket(); }
      } catch (err) { handleError(err); }
    });
  }

  function openLot(l) {
    const it = l.item;
    const body = openSheet(`
      <div class="tier-label" style="${tc(it.tier)}">${tier(it.tier).icon} ${esc(tier(it.tier).name)}<small>продаёт ${esc(l.seller_name)}</small></div>
      <div class="item-hero" style="${tc(it.tier)}"><img src="${esc(it.image_url)}" alt=""></div>
      <div class="item-name"><b>${it.stattrak ? "StatTrak™ " : ""}${itemTitle(it)}</b><span>${it.wear ? WEAR[it.wear] : ""}${it.float != null ? " · " + it.float.toFixed(4) : ""}</span></div>
      <div class="stats"><div><small>Цена лота</small><b>${money(l.price_cents)}</b></div><div><small>Справочная</small><b>${money(it.price_cents)}</b></div></div>
      <div class="actions"><button type="button" class="primary" id="lot-buy">Купить за ${money(l.price_cents)}</button><button type="button" id="lot-order">Хочу дешевле</button></div>
      <p class="note">После покупки предмет 24 часа на удержании.</p>`);
    body.querySelector("#lot-buy").onclick = async () => {
      const ok = await confirmSheet({ title: "Купить?", text: `${itemTitle(it)} за <b>${money(l.price_cents)}</b>.`, ok: "Купить" });
      if (!ok) return;
      try {
        await api.post(`/market/lots/${l.id}/buy`);
        haptic("success");
        await refresh();
        toast("Куплено: предмет в Инвентаре");
        if (ui.page === "market") renderMarket();
      } catch (err) { handleError(err); }
    };
    body.querySelector("#lot-order").onclick = () => openOrder(it, l.price_cents);
  }

  function priceInput(label, cents, lo, hi) {
    return `<label class="lead" for="price-v">${esc(label)}, ${esc(M.currency.symbol)} (от ${money(lo)} до ${money(hi)})</label>
      <input class="field" id="price-v" type="number" step="any" inputmode="decimal" value="${fromCents(cents)}">`;
  }
  const readPrice = (body) => toCents(body.querySelector("#price-v").value);

  function openOrder(it, lotPrice) {
    const lo = Math.ceil((it.price_cents * C.market.band_pct[0]) / 100), hi = Math.floor((it.price_cents * C.market.band_pct[1]) / 100);
    const body = openSheet(`<h2>Заявка на покупку</h2>
      <p class="lead">Монеты заблокируются сразу. Как только кто-то выставит ${esc(it.name)} ${it.wear || ""} не дороже твоей цены, предмет купится сам.</p>
      ${priceInput("Готов заплатить", Math.max(lo, Math.round(lotPrice * 0.9)), lo, hi)}
      <button type="button" class="btn-spin small" id="ord-go"><span>СОЗДАТЬ ЗАЯВКУ</span></button>`);
    body.querySelector("#ord-go").onclick = async () => {
      try {
        await api.post("/market/orders", { catalog_item_id: it.catalog_id, wear: it.wear, stattrak: it.stattrak, max_price_cents: readPrice(body) });
        closeSheet();
        await refresh(false);
        toast("Заявка создана");
      } catch (err) { handleError(err); }
    };
  }

  function openListLot(it) {
    const lo = Math.ceil((it.price_cents * C.market.band_pct[0]) / 100), hi = Math.floor((it.price_cents * C.market.band_pct[1]) / 100);
    const fee = M.user.vip_until ? C.market.fee_vip_pct : C.market.fee_pct;
    const body = openSheet(`<h2>На рынок</h2>
      <p class="lead">${itemTitle(it)} ${it.wear || ""}. Справочная цена ${money(it.price_cents)}. Комиссия ${fee}% уходит из игры, лот висит ${C.market.lot_days} дня.</p>
      ${priceInput("Цена лота", it.price_cents, lo, hi)}
      <p class="formula" id="lot-net"></p>
      <button type="button" class="btn-spin small" id="lot-go"><span>ВЫСТАВИТЬ</span></button>`);
    const upd = () => { const p = readPrice(body); body.querySelector("#lot-net").innerHTML = `Получишь <b>${money(p - Math.floor((p * fee) / 100))}</b>`; };
    body.querySelector("#price-v").addEventListener("input", upd);
    upd();
    body.querySelector("#lot-go").onclick = async () => {
      try {
        await api.post("/market/lots", { item_id: it.id, price: readPrice(body), currency: "coins" });
        closeSheet();
        await refresh();
        toast("Лот выставлен");
      } catch (err) { handleError(err); }
    };
  }

  function pickerGrid(items, selected, max, onDone, extra = "") {
    const sel = new Set(selected);
    const body = openSheet(`${extra}
      ${items.length ? `<div class="grid" id="pick">${items.map((i) => tileHtml(i, sel.has(i.id) ? "sel" : "")).join("")}</div>` : '<p class="empty">Подходящих предметов нет.</p>'}
      <button type="button" class="btn-spin small" id="pick-ok"><span>ГОТОВО</span></button>`);
    const grid = body.querySelector("#pick");
    if (grid) grid.addEventListener("click", (e) => {
      const t = e.target.closest("[data-item]");
      if (!t) return;
      const id = +t.dataset.item;
      if (sel.has(id)) sel.delete(id);
      else if (sel.size < max) sel.add(id);
      else return toast(`Не больше ${max}`);
      t.classList.toggle("sel", sel.has(id));
    });
    body.querySelector("#pick-ok").onclick = () => onDone([...sel], body);
    return body;
  }
  const freeItems = () => INV.items.filter(usable).sort((a, b) => b.price_cents - a.price_cents);

  // ---------- обмены ----------
  async function renderTrades() {
    const p = page("Обмены", `<button class="btn-ghost wide" id="trade-new" type="button">Предложить обмен</button>
      <div id="trades-list" class="lots"><p class="empty">Загрузка…</p></div>
      <p class="note">Обмены открываются через 24 часа после первого входа. Разница в цене не больше 50%, с одним игроком не больше 3 обменов в неделю.</p>`);
    p.querySelector("#trade-new").onclick = newTrade;
    const list = p.querySelector("#trades-list");
    try {
      const { trades } = await api.get("/trades");
      const mini = (items) => items.map((i) => `<img src="${esc(i.image_url)}" alt="" title="${esc(i.name)}">`).join("");
      list.innerHTML = trades.length ? trades.map((t) => `
        <div class="trade">
          <div class="trade-h"><b>${t.incoming ? esc(t.from_name) + " предлагает" : "Ты → " + esc(t.to_name)}</b><small>${{ pending: "ждёт ответа", accepted: "принят", declined: "отклонён", cancelled: "отменён", expired: "истёк" }[t.status]}</small></div>
          <div class="trade-sides"><div><small>${t.incoming ? "Отдаёт" : "Отдаёшь"} · ${money(t.value_from_cents)}</small><div class="mini-items">${mini(t.give)}</div></div>
            <div><small>${t.incoming ? "Просит" : "Просишь"} · ${money(t.value_to_cents)}</small><div class="mini-items">${mini(t.take) || "—"}</div></div></div>
          ${t.status === "pending" ? (t.incoming ? `<div class="actions"><button type="button" class="primary" data-tr="${t.id}" data-a="accept">Принять</button><button type="button" data-tr="${t.id}" data-a="decline">Отклонить</button></div>`
            : `<div class="actions"><button type="button" class="wide" data-tr="${t.id}" data-a="cancel">Отменить</button></div>`) : ""}
        </div>`).join("") : '<p class="empty">Обменов пока не было.</p>';
    } catch (err) { handleError(err); }
    list.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-tr]");
      if (!b) return;
      if (b.dataset.a === "accept" && !(await confirmSheet({ title: "Принять обмен?", text: "Предметы сразу поменяются владельцами и 24 часа будут на удержании.", ok: "Принять" }))) return;
      try {
        await api.post(`/trades/${b.dataset.tr}/${b.dataset.a}`);
        await refresh();
        renderTrades();
      } catch (err) { handleError(err); }
    });
  }

  async function newTrade() {
    let friends = [];
    try { friends = (await api.get("/friends")).invited; } catch (e) { /* без друзей */ }
    const body = openSheet(`<h2>Новый обмен</h2>
      <p class="lead">С кем меняемся? Выбери друга или введи ID игрока (он есть в профиле на вкладке «Ещё»).</p>
      ${friends.length ? `<div class="chips wrap">${friends.map((f) => `<button type="button" class="chip" data-fr="${f.id}">${esc(f.name)}</button>`).join("")}</div>` : ""}
      <input class="field" id="tr-user" type="number" inputmode="numeric" placeholder="ID игрока">
      <button type="button" class="btn-spin small" id="tr-next"><span>ДАЛЬШЕ</span></button>`);
    body.querySelectorAll("[data-fr]").forEach((b) => (b.onclick = () => (body.querySelector("#tr-user").value = b.dataset.fr)));
    body.querySelector("#tr-next").onclick = async () => {
      const uid = parseInt(body.querySelector("#tr-user").value, 10);
      if (!uid) return toast("Введи ID игрока");
      try {
        const other = await api.get(`/users/${uid}`);
        const give = freeItems().filter((i) => !i.hold_until && !i.overflow_until);
        pickerGrid(give, [], C.trades.max_items, (giveIds) => {
          if (!giveIds.length) return toast("Выбери хотя бы один предмет");
          pickerGrid(other.items, [], C.trades.max_items, async (takeIds) => {
            try {
              await api.post("/trades", { to_user: uid, give: giveIds, take: takeIds });
              closeSheet();
              await refresh();
              toast("Предложение отправлено");
              if (ui.page === "trades") renderTrades();
            } catch (err) { handleError(err); }
          }, `<h2>Что просишь у ${esc(other.name || "игрока")}</h2><p class="lead">Можно ничего не просить: тогда это подарок.</p>`);
        }, "<h2>Что отдаёшь</h2>");
      } catch (err) { handleError(err); }
    };
  }

  // ---------- друзья, рейтинг, дуэли ----------
  async function renderFriends() {
    const p = page("Друзья", `<div class="panel"><p id="ref-text">Загрузка…</p><div class="ref-steps" id="ref-steps"></div>
      <button class="btn-spin small" id="invite-btn" type="button"><span>ПОЗВАТЬ ДРУГА</span></button></div><div id="friends-list" class="lots"></div>`);
    p.querySelector("#invite-btn").onclick = () => {
      const link = M.app_link || M.invite_link || "";
      const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Открывай со мной кейсы CS2 в Telegram. Тебе 3 бесплатных спина!")}`;
      const t = api.tg();
      if (t && t.openTelegramLink && !window.SKINS_DEMO) t.openTelegramLink(url);
      else toast("В Telegram откроется выбор чата со ссылкой-приглашением");
    };
    try {
      const f = await api.get("/friends");
      p.querySelector("#ref-text").textContent = `Когда друг откроет ${f.qualify_spins} кейсов, он засчитается. Друзей в игре: ${f.qualified}.`;
      p.querySelector("#ref-steps").innerHTML = f.tiers.map((t) => `<div class="${t.granted ? "done" : ""}"><b>${t.friends}</b><small>${t.spins ? t.spins + " спина" : ""}${t.cases ? money(Math.round(C.unit_cents * t.cases)) : ""}${t.cosmetic ? (t.spins || t.cases ? " + " : "") + C.cosmetics[t.cosmetic].title.toLowerCase() : ""}</small></div>`).join("");
      p.querySelector("#friends-list").innerHTML = f.invited.map((x) => `<div class="lot"><div class="lot-main"><b>${esc(x.name)}</b><small>${x.qualified ? "в игре" : `открыл ${x.spins} из ${f.qualify_spins}`}</small></div></div>`).join("");
    } catch (err) { handleError(err); }
  }

  async function renderRating() {
    const boards = [["capital", "Капитал"], ["rarest", "Самый дорогой"], ["level", "Уровень"], ["collection", "Коллекция"]];
    const p = page("Рейтинг", `
      <div class="seg small" id="board-seg">${boards.map(([id, l]) => `<button type="button" data-board="${id}" class="${ui.board === id ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="seg small" id="scope-seg"><button type="button" data-scope="friends" class="${ui.scope === "friends" ? "on" : ""}">Друзья</button><button type="button" data-scope="global" class="${ui.scope === "global" ? "on" : ""}">Все</button></div>
      <ol class="board" id="board"></ol>`);
    p.querySelector("#board-seg").onclick = (e) => { const b = e.target.closest("[data-board]"); if (b) { ui.board = b.dataset.board; renderRating(); } };
    p.querySelector("#scope-seg").onclick = (e) => { const b = e.target.closest("[data-scope]"); if (b) { ui.scope = b.dataset.scope; renderRating(); } };
    try {
      const r = await api.get(`/leaderboard?board=${ui.board}&scope=${ui.scope}`);
      const fmt = (v) => (ui.board === "level" ? `${v} XP` : ui.board === "collection" ? `${v} видов` : money(v));
      p.querySelector("#board").innerHTML = r.rows.map((x) => `<li class="${x.me ? "me" : ""}"><span>${x.place}. ${esc(x.name)} <small>ур. ${x.level}</small></span><b>${fmt(x.value)}</b></li>`).join("")
        || "<li>Пока пусто. Позови друзей, чтобы было с кем сравниться.</li>";
    } catch (err) { handleError(err); }
  }

  async function renderDuels() {
    const p = page("Дуэли", `<p class="lead">Дуэль без ставок: каждый выбирает 3 предмета, сила тройки — сумма цен со случайным множителем 0,85–1,15. Победителю монеты от игры, у проигравшего ничего не забирают.</p>
      <button class="btn-spin small" id="duel-new" type="button"><span>НОВАЯ ДУЭЛЬ</span></button><div id="duels-list" class="lots"></div>`);
    const pick3 = (title, done) => pickerGrid(INV.items.filter((i) => i.state === "free" && !i.overflow_until).sort((a, b) => b.price_cents - a.price_cents), [], 3, (ids) => {
      if (ids.length !== 3) return toast("Нужно ровно 3 предмета");
      done(ids);
    }, `<h2>Твоя тройка</h2><p class="lead">${title}</p>`);
    p.querySelector("#duel-new").onclick = () => pick3("Предметы не теряются: после дуэли вернутся в Инвентарь.", async (ids) => {
      try { await api.post("/duels", { item_ids: ids }); closeSheet(); await refresh(); toast("Дуэль создана: ждём соперника"); renderDuels(); } catch (err) { handleError(err); }
    });
    const list = p.querySelector("#duels-list");
    try {
      const d = await api.get("/duels");
      list.innerHTML = (d.duels.length ? d.duels.map((x) => {
        const mine = x.p1 === M.user.id;
        const status = x.status === "open" ? (mine ? "ждёт соперника" : "можно принять") : x.status === "done" ? (x.winner === M.user.id ? "победа" : "поражение") : "закрыта";
        return `<div class="trade"><div class="trade-h"><b>${esc(x.p1_name)}${x.p2 ? " vs " + esc(x.p2_name) : ""}</b><small class="${status === "победа" ? "up" : ""}">${status}</small></div>
          <div class="mini-items">${x.p1_items.map((i) => `<img src="${esc(i.image_url)}" alt="">`).join("")}</div>
          ${x.status === "done" ? `<small class="note">Сила: ${money(x.p1_power)} против ${money(x.p2_power)}</small>` : ""}
          ${x.status === "open" ? `<div class="actions"><button type="button" class="${mine ? "wide" : "primary wide"}" data-duel="${x.id}" data-a="${mine ? "cancel" : "join"}">${mine ? "Отменить" : "Принять дуэль"}</button></div>` : ""}</div>`;
      }).join("") : "") + `<p class="note">Сегодня осталось дуэлей: ${d.left_today}.</p>`;
    } catch (err) { handleError(err); }
    list.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-duel]");
      if (!b) return;
      if (b.dataset.a === "cancel") {
        try { await api.post(`/duels/${b.dataset.duel}/cancel`); await refresh(); renderDuels(); } catch (err) { handleError(err); }
        return;
      }
      pick3("Выбери 3 предмета против тройки соперника.", async (ids) => {
        try {
          const res = await api.post(`/duels/${b.dataset.duel}/join`, { item_ids: ids });
          haptic(res.you_won ? "success" : "warning");
          await refresh();
          openSheet(`<h2>${res.you_won ? "Победа!" : "Соперник сильнее"}</h2>
            <div class="stats"><div><small>Соперник</small><b>${money(res.p1_power)}</b></div><div><small>Ты</small><b>${money(res.p2_power)}</b></div></div>
            <p class="lead">${res.you_won ? `+${money(res.prize_cents)} от игры.` : "Предметы остались у тебя."}</p>`);
          renderDuels();
        } catch (err) { handleError(err); }
      });
    });
  }

  // ---------- магазин, честная игра, шансы, косметика, страна, настройки ----------
  async function openShop() {
    try {
      const s = await api.get("/shop");
      const body = openSheet(`<h2>Магазин ⭐</h2>
        ${s.enabled ? "" : '<p class="banner">Покупки за Stars откроются после юридической проверки. Пока можно посмотреть, что будет.</p>'}
        <p class="lead">За Stars только попытки, удача, VIP и места. Монеты и предметы за Stars не продаются. Траты за месяц: ${s.month_spent} из ${s.month_cap} ⭐.</p>
        ${s.warn ? '<p class="banner">Ты потратил больше 80% месячного лимита.</p>' : ""}
        <div class="shop">${s.products.map((p) => `<div class="product"><div><b>${esc(p.title)}</b>${p.reason && s.enabled ? `<small>${esc(p.reason)}</small>` : ""}</div>
          <button type="button" class="star-btn" data-p="${p.code}" ${s.enabled && p.available ? "" : "disabled"}>${p.stars} ⭐</button></div>`).join("")}</div>
        <p class="note">Удача ×N: на 10 следующих кейсах до ${money(C.luck.max_case_cents)} скины дороже кейса выпадают до N раз чаще. Средний возврат кейса с удачей не больше ${Object.entries(C.luck.max_return).map(([m, r]) => `${Math.round(r * 100)}% при ×${num(m)}`).join(", ")}: если на кейсе вышло бы больше, множитель там меньше, и страница кейса это показывает. Удачи не складываются. Неиспользованное можно вернуть через /paysupport.</p>`);
      body.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-p]");
        if (!b) return;
        const p = s.products.find((x) => x.code === b.dataset.p);
        const ok = await confirmSheet({ title: p.title, text: `Цена ${p.stars} ⭐. Оплата через Telegram Stars. После выдачи покупка окончательная.`, ok: `Купить за ${p.stars} ⭐` });
        if (!ok) return;
        try {
          const inv = await api.post("/shop/invoice", { product: p.code });
          const t = api.tg();
          if (inv.demo) { await refresh(false); toast("Демо: покупка выдана без оплаты"); return; }
          t.openInvoice(inv.invoice_link, async (status) => {
            if (status === "paid") { haptic("success"); await refresh(false); toast("Покупка выдана"); }
          });
        } catch (err) { handleError(err); }
      });
    } catch (err) { handleError(err); }
  }

  async function hmacHex(serverSeedHex, msg) {
    const key = await crypto.subtle.importKey("raw", new Uint8Array(serverSeedHex.match(/../g).map((h) => parseInt(h, 16))), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
    return Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function rollSummary(res) {
    if (res.tiers) return res.tiers.map((t) => tier(t).name).join(", ");
    if (res.sector) return "сектор " + res.sector;
    if (res.mult !== undefined) return `контракт ×${num(res.mult)}`;
    if (res.success !== undefined) return res.success ? "успех" : "неудача";
    if (res.p1 !== undefined) return `сила ${money(res.p1)} против ${money(res.p2)}`;
    return "";
  }

  async function openFair(revealed) {
    try {
      const f = await api.get("/fair");
      const kinds = { spin: "Кейс", upgrade: "Апгрейд", contract: "Контракт", wheel_daily: "Колесо дня", wheel_weekly: "Недельное колесо", duel: "Дуэль" };
      const body = openSheet(`
        <h2>Честная игра</h2>
        <p class="lead">До броска сервер показывает хеш своего ключа. Каждое число броска = первые 52 бита HMAC-SHA256(ключ сервера, «твой ключ:nonce:i») / 2^52. После смены пары ключ раскрывается, и любой бросок можно пересчитать прямо здесь.</p>
        <div class="stats one">
          <div><small>Хеш ключа сервера (SHA-256)</small><b class="mono">${esc(f.server_seed_hash)}</b></div>
          <div><small>Следующий nonce</small><b>${f.nonce}</b></div>
        </div>
        <label class="lead" for="fair-seed">Твой ключ</label>
        <input class="field" id="fair-seed" value="${esc(f.client_seed)}" maxlength="64">
        ${revealed ? `<div class="stats one"><div><small>Раскрытый ключ сервера</small><b class="mono">${esc(revealed.server_seed)}</b></div></div>` : ""}
        <button type="button" class="btn-spin small" id="fair-go"><span>СМЕНИТЬ ПАРУ</span></button>
        <h3 class="h-sub">Последние броски</h3>
        <div class="rolls">${f.rolls.map((r, n) => `<div class="roll"><div><b>${kinds[r.kind] || r.kind}</b><small>${new Date(r.ts * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })} · nonce ${r.nonce} · ${esc(rollSummary(r.result))}</small></div>
          ${r.server_seed ? `<button type="button" class="mini" data-verify="${n}">Проверить</button>` : '<small class="note">ключ ещё скрыт</small>'}</div>`).join("") || '<p class="note">Бросков пока нет.</p>'}</div>`);
      body.querySelector("#fair-go").onclick = async () => {
        try {
          const res = await api.post("/fair/rotate", { client_seed: body.querySelector("#fair-seed").value });
          openFair(res.revealed);
        } catch (err) { handleError(err); }
      };
      body.querySelectorAll("[data-verify]").forEach((b) => (b.onclick = async () => {
        const r = f.rolls[+b.dataset.verify];
        try {
          const nums = [];
          for (let i = 0; i < Math.min(r.numbers, 6); i++) {
            const h = await hmacHex(r.server_seed, `${r.client_seed}:${r.nonce}:${i}`);
            nums.push((parseInt(h.slice(0, 13), 16) / 2 ** 52).toFixed(6));
          }
          const hashOk = await crypto.subtle.digest("SHA-256", new Uint8Array(r.server_seed.match(/../g).map((h) => parseInt(h, 16))))
            .then((d) => Array.from(new Uint8Array(d), (x) => x.toString(16).padStart(2, "0")).join("") === r.seed_hash);
          b.outerHTML = `<small class="${hashOk ? "up" : "down"}">${hashOk ? "хеш совпал" : "хеш НЕ совпал"} · u = ${nums.join(", ")}${r.numbers > 6 ? "…" : ""}</small>`;
        } catch (e) { toast("Браузер не поддерживает проверку"); }
      }));
    } catch (err) { handleError(err); }
  }

  function openOdds(caseId) {
    const c = caseById(caseId) || C.cases[0];
    const rows = Object.entries(c.tier_odds).sort((a, b) => rank(b[0]) - rank(a[0])).map(([t, p]) =>
      `<tr style="${tc(t)}"><td><span style="color:var(--tc)">${tier(t).icon}</span> ${esc(tier(t).name)}</td><td>${pct(p, 2)}${p < 0.1 && p > 0 ? ` · 1 из ${Math.round(1 / p)}` : ""}</td></tr>`).join("");
    const wears = C.wear.map((w) => `<tr><td>${WEAR[w[0]]}</td><td>${num(w[1])}–${num(w[2])}</td><td>${w[3]}%</td></tr>`).join("");
    const body = openSheet(`
      <h2>Шансы</h2>
      <p class="lead">Шансы одинаковы для всех и совпадают с теми, по которым бросает сервер.</p>
      <label class="lead" for="odds-case">Кейс</label>
      <select class="field" id="odds-case">${C.cases.map((x) => `<option value="${x.id}" ${x.id === c.id ? "selected" : ""}>${esc(x.name)} · ${money(x.price_cents)}</option>`).join("")}</select>
      <table class="odds-table"><tr><th>Редкость</th><th>Шанс</th></tr>${rows}</table>
      <p class="lead">Внутри редкости скин выпадает тем реже, чем он дороже (шанс каждого — на странице кейса). Гарантии у каждого кейса свои: Запрещённое и выше не позже 60-го открытия подряд, Засекреченное и выше не позже 300-го; шанс растёт заранее, с 51-го и 281-го. Цена кейса = средний дроп по рынку с гарантиями / 0,8.</p>
      <h3 class="h-sub">Удача</h3>
      <p class="lead">Удача ×N: скины дороже кейса выпадают до N раз чаще, вместе не больше ${pct(C.luck.max_boosted_share, 0)}, средний возврат с удачей не больше ${Object.entries(C.luck.max_return).map(([m, r]) => `${Math.round(r * 100)}% при ×${num(m)}`).join(", ")}. Работает на кейсах до ${money(C.luck.max_case_cents)}.</p>
      <h3 class="h-sub">Контракт</h3>
      <table class="odds-table"><tr><th>Множитель</th><th>Шанс</th></tr>${C.contracts.table.map((x) => `<tr><td>×${num(x.mult)}</td><td>${pct(x.p, 1)}</td></tr>`).join("")}</table>
      <h3 class="h-sub">Износ</h3>
      <table class="odds-table"><tr><th>Износ</th><th>Float</th><th>Шанс</th></tr>${wears}</table>
      <p class="lead">StatTrak™ — 10%, если у скина он бывает.</p>`);
    body.querySelector("#odds-case").onchange = (e) => openOdds(e.target.value);
  }

  async function openCosmetics() {
    try {
      const c = await api.get("/cosmetics");
      const body = openSheet(`<h2>Рамки и значки</h2><p class="lead">Косметика ничего не меняет в шансах и ценах. Её дают колесо недели, задания и друзья.</p>
        <div class="shop">${c.items.map((x) => `<div class="product" style="${tc(x.tier)}"><div><b style="color:var(--tc)">${esc(x.title)}</b><small>${x.kind === "frame" ? "рамка профиля" : x.kind === "badge" ? "значок" : "эффект"}${x.owned ? "" : " · пока нет"}</small></div>
          ${x.owned && x.kind === "frame" ? `<button type="button" class="mini ${x.equipped ? "" : "primary"}" data-eq="${x.equipped ? "" : x.code}">${x.equipped ? "Снять" : "Надеть"}</button>` : ""}</div>`).join("")}</div>`);
      body.addEventListener("click", async (e) => {
        const b = e.target.closest("[data-eq]");
        if (!b) return;
        try { await api.post("/cosmetics/equip", { code: b.dataset.eq || null }); await refresh(false); openCosmetics(); } catch (err) { handleError(err); }
      });
    } catch (err) { handleError(err); }
  }

  function openCountry(first) {
    const list = Object.entries(C.currency.countries);
    const body = openSheet(`<h2>${first === true ? "Где ты играешь?" : "Страна и валюта"}</h2>
      <p class="lead">Страна меняет только то, как показываются суммы. Цены, шансы и баланс одни для всех. Менять можно раз в 30 дней.</p>
      ${M.country_change_after ? `<p class="banner">Следующая смена после ${new Date(M.country_change_after * 1000).toLocaleDateString("ru-RU")}.</p>` : ""}
      <div class="country-list">${list.map(([code, c]) => `<button type="button" class="country ${code === M.country ? "on" : ""}" data-c="${code}"><span>${esc(c.name)}</span><b>${esc(c.symbol)}</b></button>`).join("")}</div>`);
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-c]");
      if (!b) return;
      try {
        await api.put("/me/country", { country: b.dataset.c });
        closeSheet();
        await refresh(false);
        toast(`Суммы теперь в ${M.currency.symbol}`);
      } catch (err) { handleError(err); }
    });
  }

  function openSettings() {
    const body = openSheet(`
      <h2>Настройки</h2>
      <label class="toggle" for="s-fast"><span>Всегда быстро<br><small class="lead">лента и колёса без долгой анимации</small></span><input type="checkbox" id="s-fast" ${settings.fast ? "checked" : ""}></label>
      <label class="toggle" for="s-nofx"><span>Без эффектов<br><small class="lead">без вспышек и свечения</small></span><input type="checkbox" id="s-nofx" ${settings.nofx ? "checked" : ""}></label>
      <label class="toggle" for="s-noup"><span>Без апгрейдов<br><small class="lead">вкладка «Апгрейды» скрыта</small></span><input type="checkbox" id="s-noup" ${settings.noUpgrader ? "checked" : ""}></label>`);
    body.addEventListener("change", () => {
      settings.fast = body.querySelector("#s-fast").checked;
      settings.nofx = body.querySelector("#s-nofx").checked;
      settings.noUpgrader = body.querySelector("#s-noup").checked;
      saveSettings();
      document.body.classList.toggle("no-fx", settings.nofx);
      renderHeader();
    });
  }

  function openTerms() {
    openSheet(`<h2>Условия</h2>
      <p>Игровые рубли есть только внутри бота. Их нельзя купить за деньги, вывести или обменять на рубли, Stars или настоящие скины. Валюта твоей страны на экране — пересчёт по игровому курсу.</p>
      <p>Предметы — игровые копии скинов CS2 с ценами рынка. Вывода в Steam нет.</p>
      <p>За Stars продаются только попытки, удача, VIP и места, месячный лимит 500 ⭐.</p>
      <p>Баннеры с пометкой «Реклама» — реклама Telegram-каналов. За переход и подписку ничего не начисляется.</p>
      <p>Шансы открыты на экране «Шансы», каждый бросок проверяется на экране «Честная игра».</p>
      <p>Уведомлений не больше 2 в день, ночью с 21:00 до 8:00 их нет.</p>`);
  }

  // ---------- навигация ----------
  function switchTab(tab) {
    if (tab === "upgrade" && settings.noUpgrader) tab = "cases";
    ui.tab = tab;
    if (tab !== "inventory") ui.select = false;
    document.querySelectorAll(".screen[data-tab]").forEach((s) => (s.hidden = s.dataset.tab !== tab));
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    $("live").hidden = tab !== "cases" || !$("live").innerHTML;
    $("screens").scrollTop = 0;
    if (tab === "upgrade" && !ui.up.targets.length && upStake().sum > 0) loadTargets();
    if (tab === "more") ui.page = null;
    renderAll();
    renderSelbar();
  }
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    haptic("tick");
    if (b.dataset.tab === "cases" && ui.tab === "cases") ui.caseId = null; // повторное нажатие — к списку кейсов
    switchTab(b.dataset.tab);
  });
  $("balance").addEventListener("click", () => openSheet(`
    <h2>${money(M.balance_cents)} ${esc(M.currency.game_label || "")}</h2>
    <p class="lead">Игровые рубли: 1 игровой рубль = 1 рубль рыночной цены скина (курс ЦБ ${num(C.usd_rub)} ₽ за $). За настоящие деньги не продаются и не выводятся.</p>
    <div class="stats"><div><small>Как получить</small><b class="txt">продажа скинов, колесо, задания, дуэли, розыгрыш</b></div>
    <div><small>Куда потратить</small><b class="txt">кейсы, апгрейды, рынок</b></div></div>`));
  $("energy").addEventListener("click", () => { switchTab("cases"); openCase(C.free_case_id); });

  // ---------- старт ----------
  async function boot() {
    await api.ready();
    const t = api.tg();
    if (t) {
      try { t.ready(); t.expand(); t.setHeaderColor("#121317"); t.setBackgroundColor("#121317"); } catch (e) { /* вне Telegram */ }
    }
    document.body.classList.toggle("no-fx", settings.nofx);
    try {
      C = await api.get("/config");
      await refresh();
      loadLive();
      if (!M.country_confirmed) openCountry(true);
      else if (M.last_drop) {
        const items = M.last_drop.item_ids.map(findItem).filter(Boolean);
        if (items.length) { toast("Твой последний дроп сохранился"); showResults(items); }
        api.post("/me/last-drop/ack").catch(() => {});
      }
    } catch (err) {
      $("screens").innerHTML = `<p class="empty">${esc(err.message)}<br><br><button class="btn-ghost" onclick="location.reload()">Повторить</button></p>`;
    }
  }
  boot();
})();
