// Мини-приложение «Кейсы CS2». Всё случайное решает сервер, клиент только показывает результат.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TIER_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret", "divine", "exclusive"];
  const CASE_TIERS = ["rare", "epic", "legendary", "mythic", "secret"];
  const WEAR = { FN: "Factory New", MW: "Minimal Wear", FT: "Field-Tested", WW: "Well-Worn", BS: "Battle-Scarred" };
  const rank = (t) => TIER_ORDER.indexOf(t);
  const api = window.Api;

  let C = null;   // GET /config
  let M = null;   // GET /me
  let INV = null; // GET /inventory
  let busy = false;
  const settings = loadSettings();
  const ui = {
    tab: "play", level: settings.level || "common", caseId: null, count: 1,
    colFilter: "all", colSort: "new", colSearch: "",
    market: "lots", lotsView: "all", lotsTier: null,
    friends: "invite", board: "capital", scope: "friends",
    up: { items: [], coins: 0, mult: 2, target: null, targets: [], spinning: false },
    ct: { tier: "rare", items: [] },
  };

  // ---------- утилиты ----------
  function loadSettings() {
    const d = { fast: false, nofx: false, noUpgrader: false, level: "common" };
    try { return Object.assign(d, JSON.parse(localStorage.getItem("settings") || "{}")); } catch (e) { return d; }
  }
  function saveSettings() { try { localStorage.setItem("settings", JSON.stringify(settings)); } catch (e) { /* без хранилища */ } }
  function money(cents, opts = {}) {
    const cur = (M && M.currency) || { rate: 1, decimals: 0, symbol: "₽" };
    const v = (cents / 100) * cur.rate * ((C && C.currency.scale) || 1);
    const d = cur.decimals;
    const fixed = d ? v.toFixed(d) : String(Math.round(v));
    const [a, b] = fixed.split(".");
    const text = a.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + (b ? "," + b : "");
    return opts.bare ? text : text + " " + cur.symbol;
  }
  function pct(p, digits) {
    const d = digits != null ? digits : p >= 0.1 ? 1 : 2;
    return (p * 100).toFixed(d).replace(".", ",") + "%";
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function tc(tier) { return `--tc: var(--t-${tier})`; }
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
  const selectedCase = () => C.cases.find((c) => c.id === ui.caseId);
  const selectedLevel = () => C.levels.find((l) => l.id === ui.level);
  const dayStr = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });

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

  function confirmSheet({ title, text, ok, pause = 0 }) {
    return new Promise((resolve) => {
      const body = openSheet(`
        <h2>${esc(title)}</h2><p>${text}</p>
        <div class="actions"><button type="button" id="cf-no">Отмена</button>
        <button type="button" class="primary" id="cf-ok" ${pause ? "disabled" : ""}>${esc(ok)}${pause ? ` <span class="count-down">${pause}</span>` : ""}</button></div>`,
        () => resolve(false));
      const btn = body.querySelector("#cf-ok");
      if (pause) {
        let left = pause;
        const iv = setInterval(() => {
          left -= 1;
          if (!document.body.contains(btn)) return clearInterval(iv);
          if (left <= 0) { clearInterval(iv); btn.disabled = false; btn.textContent = ok; }
          else btn.querySelector(".count-down").textContent = left;
        }, 1000);
      }
      body.querySelector("#cf-no").onclick = () => closeSheet();
      btn.onclick = () => { sheetClose = null; closeSheet(); resolve(true); };
    });
  }

  // Повторить запрос с confirmed: true, если сервер ответил CONFIRM_REQUIRED.
  async function withConfirm(fn, title, okLabel) {
    try {
      return await fn(false);
    } catch (err) {
      if (err.code !== "CONFIRM_REQUIRED") throw err;
      const ok = await confirmSheet({ title: title || "Подтвердить?", text: esc(err.message), ok: okLabel || "Подтвердить", pause: err.pause_s || 0 });
      if (!ok) return null;
      return fn(true);
    }
  }

  function handleError(err) {
    haptic("error");
    if (err.code === "NOT_ENOUGH_COINS") {
      const body = openSheet(`<h2>${esc(err.message.split(".")[0])}</h2>
        <p class="lead">Монеты дают колесо дня, задания и продажа предметов.</p>
        <div class="actions"><button type="button" id="ne-wheel">Колесо</button><button type="button" id="ne-tasks">Задания</button></div>`);
      body.querySelector("#ne-wheel").onclick = openWheel;
      body.querySelector("#ne-tasks").onclick = openTasks;
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
    renderPlay();
    renderCollection();
    if (ui.tab === "market") renderMarket();
    renderMore();
  }

  function renderHeader() {
    $("balance-value").textContent = money(M.balance_cents);
    $("balance-label").textContent = M.currency.game_label || "";
    $("energy-value").textContent = `${M.energy.now}/${M.energy.max}`;
  }

  // ---------- ИГРАТЬ ----------
  function levelCard(l) {
    const on = l.id === ui.level;
    if (l.id === "common") {
      return `<button type="button" class="level-chip ${on ? "on" : ""}" data-level="common">
        <span class="lv-name">${esc(l.name)}</span><span class="lv-what">${C.cases.length} кейсов</span>
        <span class="lv-goal">Covert+ ${pct(C.case_odds.filter((o) => rank(o.tier) >= rank("mythic")).reduce((s, o) => s + o.p, 0), 2)}</span>
        <b>от ${money(Math.min(...C.cases.map((c) => c.price_cents)))}</b></button>`;
    }
    return `<button type="button" class="level-chip ${on ? "on" : ""}" data-level="${l.id}" style="${tc(l.from)}">
      <span class="lv-name">${esc(l.name)}</span><span class="lv-what">${esc(l.what)}</span>
      <span class="lv-goal">${esc(l.goal.label)} ${pct(l.goal.p, 2)}</span><b>${money(l.price_cents)}</b></button>`;
  }

  function currentScale() {
    if (ui.level === "common") return C.case_odds;
    return selectedLevel().odds;
  }
  function currentPrice() {
    return ui.level === "common" ? selectedCase().price_cents : selectedLevel().price_cents;
  }

  function renderPlay() {
    $("level-rail").innerHTML = C.levels.map(levelCard).join("");
    const common = ui.level === "common";
    $("case-rail").hidden = !common;
    if (common) {
      $("case-rail").innerHTML = C.cases.map((c) => `
        <button type="button" class="case-chip ${c.id === ui.caseId ? "on" : ""}" data-case="${c.id}" role="option" aria-selected="${c.id === ui.caseId}">
          ${c.free ? '<span class="free-tag">бесплатный</span>' : ""}
          <img src="${esc(c.image_url)}" alt="" loading="lazy"><span>${esc(c.name.replace(/ Case$/, ""))}</span><b>${money(c.price_cents)}</b>
        </button>`).join("");
    }
    const c = selectedCase();
    const lvl = selectedLevel();
    if (common) {
      $("stage-pic").innerHTML = `<img src="${esc(c.image_url)}" alt="">`;
      $("case-name").textContent = c.name.replace(/ Case$/, "");
      $("stage-sub").textContent = `Средний дроп ${money(c.ev_cents)}`;
    } else {
      $("stage-pic").innerHTML = `<div class="level-badge" style="${tc(lvl.from)}"><i>${tier(lvl.from).icon}</i><span>${esc(tier(lvl.from).name)}</span><small>и выше</small></div>`;
      $("case-name").textContent = lvl.name;
      $("stage-sub").textContent = `${lvl.what}: все скины этих ступеней из ${C.cases.length} кейсов`;
    }
    $("contents-btn").hidden = !common;
    $("odds").innerHTML = currentScale().map((o) => {
      const t = tier(o.tier);
      return `<div class="odd" style="${tc(o.tier)}"><i>${t.icon}</i><b>${pct(o.p)}</b><small>${esc(t.name)}</small></div>`;
    }).join("");
    $("odds").style.gridTemplateColumns = `repeat(${currentScale().length}, 1fr)`;
    if (common) {
      const luck = M.luck ? ` · удача ×${String(M.luck.mult).replace(".", ",")} ещё ${M.luck.spins_left} спинов` : "";
      $("pity").innerHTML = `Шанс Эпика и выше сейчас <b>${String(M.pity.epic_chance_pct).replace(".", ",")}%</b> · гарантия не позже 60-го кейса${luck}`;
    } else {
      $("pity").innerHTML = "Уровни крутятся только за монеты: без гарантий, удачи и бесплатных спинов.";
    }
    $("mult").innerHTML = C.spin.counts.map((n) => `<button type="button" data-count="${n}" class="${n === ui.count ? "on" : ""}">×${n}</button>`).join("");
    $("spin-price").textContent = money(currentPrice() * ui.count);
    const freeLeft = M.energy.now + M.bonus_spins;
    const onFree = common && c.free;
    $("free-btn").hidden = !(onFree && freeLeft >= ui.count);
    $("free-count").textContent = freeLeft;
    $("attempt-btn").hidden = !(onFree && M.attempts >= ui.count);
    $("attempt-count").textContent = M.attempts;
    const quests = M.today.quests_done;
    $("tasks-status").textContent = `${quests}/3 сегодня`;
    $("wheel-status").textContent = M.today.wheel_daily ? "Доступно" : M.today.wheel_weekly_unlocked ? "Недельное!" : "Завтра";
    $("wheel-card").classList.toggle("ready", M.today.wheel_daily || M.today.wheel_weekly_unlocked);
    $("raffle-status").textContent = `Билетов: ${M.today.raffle_tickets}`;
    tickTimer();
    loadFeed();
  }

  async function loadFeed() {
    try {
      const { feed } = await api.get("/feed");
      $("feed").innerHTML = feed.length ? feed.slice(0, 8).map((f) => `
        <div class="feed-row" style="${tc(f.tier)}"><img src="${esc(f.image_url)}" alt="" loading="lazy">
          <div><b>${itemTitle(f)}</b><small>${esc(f.owner_name)} · ${esc(tier(f.tier).name)}${f.wear ? " · " + f.wear : ""}</small></div>
          <em>${money(f.price_cents)}</em></div>`).join("")
        : '<p class="empty">Здесь появятся Эпики и всё, что выше, у всех игроков.</p>';
    } catch (e) { /* лента не критична */ }
  }

  let meAt = Date.now();
  function tickTimer() {
    if (!M) return;
    const el = $("spin-note");
    const common = ui.level === "common";
    const parts = [];
    if (common && selectedCase() && selectedCase().free) {
      if (M.energy.now >= M.energy.max) parts.push("Бесплатные спины накоплены: 3 из 3");
      else if (M.energy.next_at) {
        const left = Math.max(0, Math.round(M.energy.next_at - Date.now() / 1000));
        const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
        parts.push(`Бесплатный спин через ${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`);
      }
    }
    parts.push(common ? `За монеты сегодня ещё ${M.coin_spins_left}` : `Уровни: ещё ${M.level_spins_left} сегодня`);
    el.textContent = parts.join(" · ");
  }
  setInterval(() => {
    tickTimer();
    if (M && M.energy.next_at && Date.now() / 1000 > M.energy.next_at + 1 && Date.now() - meAt > 5000) {
      meAt = Date.now();
      refresh(false).catch(() => {});
    }
  }, 1000);

  $("level-rail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-level]");
    if (!b) return;
    ui.level = b.dataset.level;
    settings.level = ui.level;
    saveSettings();
    haptic("tick");
    renderPlay();
  });
  $("case-rail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-case]");
    if (!b) return;
    ui.caseId = b.dataset.case;
    haptic("tick");
    renderPlay();
  });
  $("mult").addEventListener("click", (e) => {
    const b = e.target.closest("[data-count]");
    if (!b) return;
    ui.count = +b.dataset.count;
    renderPlay();
  });
  $("spin-btn").addEventListener("click", () => doSpin("coin"));
  $("free-btn").addEventListener("click", () => doSpin("free"));
  $("attempt-btn").addEventListener("click", () => doSpin("attempt"));
  $("contents-btn").addEventListener("click", openContents);
  $("odds-btn").addEventListener("click", () => openOdds(ui.level));
  $("wheel-card").addEventListener("click", openWheel);
  $("tasks-card").addEventListener("click", openTasks);
  $("raffle-card").addEventListener("click", openRaffle);

  function openContents() {
    const c = selectedCase();
    const blocks = CASE_TIERS.filter((t) => c.pools[t] && c.pools[t].length).map((t) => {
      const o = C.case_odds.find((x) => x.tier === t);
      const list = t === "secret"
        ? `<p class="lead">${c.pools[t].length} вариантов ножей и перчаток, например: ${c.pools[t].slice(0, 6).map((i) => esc(i.name)).join(", ")}…</p>`
        : `<div class="grid">${c.pools[t].map((i) => `<div class="tile" style="${tc(t)}"><img src="${esc(i.image_url)}" alt="" loading="lazy"><span class="n">${esc(i.name)}</span><span class="p">${money(i.min_cents)}–${money(i.max_cents)}</span></div>`).join("")}</div>`;
      return `<h3 class="tier-h" style="${tc(t)}">${tier(t).icon} ${esc(tier(t).name)} · ${pct(o.p)} · 1 из ${o.one_in}</h3>${list}`;
    }).join("");
    openSheet(`<h2>${esc(c.name)}</h2><p class="lead">Цена кейса ${money(c.price_cents)}: средний дроп ${money(c.ev_cents)} по ценам рынка, делённый на 0,8. Внутри ступени все предметы выпадают одинаково часто.</p>${blocks}`);
  }

  function needsConfirm(cost) {
    return cost >= C.spin.confirm_from_cents || cost > M.balance_cents * C.spin.confirm_balance_share;
  }

  async function doSpin(source) {
    if (busy) return;
    busy = true;
    $("spin-btn").disabled = true;
    try {
      const common = ui.level === "common";
      const cost = currentPrice() * ui.count;
      let confirmed = false;
      if (source === "coin" && cost <= M.balance_cents && needsConfirm(cost)) {
        const scale = currentScale().map((o) => `${tier(o.tier).name} ${pct(o.p)}`).join(", ");
        confirmed = await confirmSheet({ title: `Открыть ×${ui.count}?`, text: `Спишется <b>${money(cost)}</b>.<br><small class="lead">${scale}</small>`, ok: "Подтвердить", pause: C.spin.confirm_pause_s });
        if (!confirmed) return;
      }
      const body = { case_id: common ? ui.caseId : null, level: ui.level, source, count: ui.count, confirmed };
      const res = await withConfirm((c) => api.post("/spin", { ...body, confirmed: confirmed || c }), "Подтверди покупку");
      if (!res) return;
      haptic("medium");
      const best = res.drops.reduce((a, b) => (rank(b.items[0].tier) > rank(a.items[0].tier) ? b : a));
      await playReel(best);
      await refresh();
      showResults(res.drops.map((d) => d.items[0]));
      api.post("/me/last-drop/ack").catch(() => {});
      if (M.break_reminder) maybeBreak();
    } catch (err) {
      handleError(err);
    } finally {
      busy = false;
      $("spin-btn").disabled = false;
    }
  }

  // Лента: соседи пришли с сервера (по обычным шансам), остановка там, где решил сервер.
  function playReel(drop) {
    return new Promise((resolve) => {
      const reels = $("reels");
      reels.innerHTML = `<div class="reel"><div class="strip">${drop.reel.map((c) =>
        `<div class="cell" style="${tc(c.tier)}"><img src="${esc(c.image_url)}" alt=""><span>${esc(c.name)}</span></div>`).join("")}</div></div>`;
      $("reel-overlay").hidden = false;
      const strip = reels.querySelector(".strip");
      const cellW = strip.firstElementChild.getBoundingClientRect().width;
      const reelW = reels.firstElementChild.getBoundingClientRect().width;
      const duration = settings.fast ? 600 : 5600;
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
        setTimeout(() => { $("reel-overlay").hidden = true; resolve(); }, settings.fast ? 150 : 450);
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
    if (items.length === 1) return showItem(items[0], { fresh: true });
    items = items.slice().sort((a, b) => rank(b.tier) - rank(a.tier) || b.price_cents - a.price_cents);
    const total = items.reduce((s, i) => s + i.price_cents, 0);
    const blue = items.filter((i) => i.tier === "rare" && !i.overflow_until);
    const blueSum = blue.reduce((s, i) => s + i.sell_cents, 0);
    const best = items[0];
    const body = openSheet(`
      <div class="tier-label" style="${tc(best.tier)}">${tier(best.tier).icon} ${esc(tier(best.tier).name)}<small>лучший дроп из ${items.length}</small></div>
      <div class="item-hero ${rank(best.tier) >= rank("epic") ? "flash" : ""}" style="${tc(best.tier)}"><img src="${esc(best.image_url)}" alt=""></div>
      <p class="lead center">Дроп на ${money(total)}</p>
      <div class="results">${items.map((i) => tileHtml(i)).join("")}</div>
      <div class="actions">
        <button type="button" id="r-keep">Забрать всё</button>
        <button type="button" id="r-sell" ${blue.length ? "" : "disabled"}>Продать синие · ${money(blueSum)}</button>
      </div>`);
    body.querySelector(".results").addEventListener("click", (e) => {
      const t = e.target.closest("[data-item]");
      if (t) showItem(findItem(+t.dataset.item) || items.find((x) => x.id === +t.dataset.item));
    });
    body.querySelector("#r-keep").onclick = closeSheet;
    body.querySelector("#r-sell").onclick = () => sellItems(blue.map((i) => i.id), false);
  }

  function tileHtml(it, extra = "") {
    const badges = [it.locked ? "🔒" : "", it.state === "market" ? "⇅" : "", it.overflow_until ? "⚠" : "", it.hold_until ? "⏳" : ""].join("");
    return `<button type="button" class="tile ${extra}" data-item="${it.id}" style="${tc(it.tier)}">
      <span class="ico">${tier(it.tier).icon}</span><span class="badges">${badges}</span>
      <img src="${esc(it.image_url)}" alt="" loading="lazy">
      <span class="w">${wearLine(it) || "&nbsp;"}</span>
      <span class="n">${itemTitle(it)}</span>
      <span class="p">${money(it.price_cents)}</span></button>`;
  }
  const findItem = (id) => INV && INV.items.find((x) => x.id === id);

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

  async function showItem(it, opts = {}) {
    if (!it) return;
    const t = tier(it.tier);
    const growth = it.drop_price_cents ? (it.price_cents - it.drop_price_cents) / it.drop_price_cents : 0;
    const originCase = it.origin.startsWith("case:") ? (C.cases.find((c) => c.id === it.origin.slice(5)) || {}).name : null;
    const originLevel = it.origin.startsWith("level:") ? (C.levels.find((l) => l.id === it.origin.slice(6)) || {}).name : null;
    const origin = originCase ? "Кейс " + originCase : originLevel ? "Крутка " + originLevel
      : { upgrade: "Апгрейдер", contract: "5 в 1", wheel: "Колесо", raffle: "Розыгрыш", referral: "Награда" }[it.origin] || "Рынок или обмен";
    const big = rank(it.tier) >= rank("epic");
    const busyState = it.state !== "free";
    const isMine = !!findItem(it.id);
    const body = openSheet(`
      <div class="tier-label" style="${tc(it.tier)}">${t.icon} ${esc(t.name)}<small>${esc(it.game_rarity)} · шанс ступени в кейсе 1 из ${it.one_in}</small></div>
      <div class="item-hero ${opts.fresh && big ? "flash" : ""}" style="${tc(it.tier)}"><img src="${esc(it.image_url)}" alt="${esc(it.name)}"></div>
      <div class="item-name"><b>${it.stattrak ? "StatTrak™ " : ""}${itemTitle(it)}</b><span>${it.wear ? WEAR[it.wear] : "Без износа"}</span></div>
      ${it.float != null ? `<div class="floatbar" aria-label="float ${it.float}"><i style="left:calc(${(it.float * 100).toFixed(2)}% - 1px)"></i></div>` : ""}
      <div class="stats">
        <div><small>Цена по рынку</small><b>${money(it.price_cents)}</b></div>
        <div><small>С момента дропа</small><b class="${growth > 0 ? "up" : growth < 0 ? "down" : ""}">${growth >= 0 ? "+" : ""}${(growth * 100).toFixed(0)}%</b></div>
        <div><small>Float</small><b>${it.float != null ? it.float.toFixed(6) : "—"}</b></div>
        <div><small>Узор</small><b>${it.pattern}</b></div>
        <div><small>Откуда</small><b class="txt">${esc(origin)}</b></div>
        <div><small>Получен</small><b>${dayStr(it.created_at)}</b></div>
      </div>
      <div id="i-chart"></div>
      ${it.overflow_until ? `<p class="banner">В переполнении до ${new Date(it.overflow_until * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}. Освободи место, иначе предмет продастся системе по 60%.</p>` : ""}
      ${it.hold_until ? '<p class="note">На удержании 24 часа после покупки или обмена: на рынок и в обмен пока нельзя.</p>' : ""}
      ${isMine ? `<div class="actions">
        <button type="button" class="primary" id="i-sell" ${it.locked || busyState ? "disabled" : ""}>Продать · ${money(it.sell_cents)}</button>
        <button type="button" id="i-market" ${it.locked || busyState || it.hold_until || it.overflow_until ? "disabled" : ""}>На рынок</button>
        <button type="button" id="i-up" ${it.locked || busyState || settings.noUpgrader ? "disabled" : ""}>Улучшить</button>
        <button type="button" id="i-ct" ${it.locked || busyState || !["rare", "epic", "legendary", "mythic"].includes(it.tier) ? "disabled" : ""}>5 в 1</button>
        <button type="button" id="i-show">${(M.showcase || []).includes(it.id) ? "Убрать с витрины" : "На витрину"}</button>
        <button type="button" id="i-lock" ${busyState ? "disabled" : ""}>${it.locked ? "Снять замок" : "Замок 🔒"}</button>
        <button type="button" id="i-share" class="wide">Показать другу</button>
      </div>
      <p class="note">Быстрая продажа системе — 60% цены, отменить можно 10 минут. Замок защищает от продажи, апгрейда и 5 в 1.</p>` : ""}`);
    if (isMine) {
      body.querySelector("#i-sell").onclick = () => sellItems([it.id], true, it);
      body.querySelector("#i-market").onclick = () => openListLot(it);
      body.querySelector("#i-up").onclick = () => { closeSheet(); ui.up.items = [it.id]; ui.up.coins = 0; ui.up.target = null; switchTab("market", "upgrade"); loadTargets(); };
      body.querySelector("#i-ct").onclick = () => { closeSheet(); ui.ct.tier = it.tier; ui.ct.items = [it.id]; switchTab("market", "contract"); };
      body.querySelector("#i-show").onclick = () => toggleShowcase(it);
      body.querySelector("#i-lock").onclick = () => toggleLock(it);
      body.querySelector("#i-share").onclick = () => share(it);
      api.get(`/items/${it.id}`).then((card) => {
        const el = body.querySelector("#i-chart");
        if (el) el.innerHTML = `<h3 class="h-sub">Цена за 30 дней</h3>${chartSvg(card.chart)}<p class="note">Владельцев: ${card.owners} · событий в истории: ${card.history.length}</p>`;
      }).catch(() => {});
    }
  }

  async function toggleLock(it) {
    try {
      if (it.locked) {
        const ok = await confirmSheet({ title: "Снять замок?", text: "Предмет снова можно будет продать, улучшить или собрать в 5 в 1.", ok: "Снять" });
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

  async function sellItems(ids, single, it) {
    const items = ids.map(findItem).filter(Boolean);
    const total = items.reduce((s, i) => s + i.sell_cents, 0);
    if (single && it && rank(it.tier) >= rank("epic")) {
      const ok = await confirmSheet({ title: "Продать?", text: `${itemTitle(it)} уйдёт системе за <b>${money(total)}</b>.`, ok: "Продать", pause: rank(it.tier) >= rank("mythic") ? 3 : 0 });
      if (!ok) return;
    } else if (!single) {
      const ok = await confirmSheet({ title: `Продать ${items.length} шт.?`, text: `Получишь <b>${money(total)}</b>. Отменить можно 10 минут.`, ok: "Продать" });
      if (!ok) return;
    }
    try {
      const res = await api.post("/items/sell", { item_ids: ids, confirmed: true });
      haptic("success");
      closeSheet();
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
          t.shareToStory(res.image_url, { text: res.text, widget_link: res.link ? { url: res.link, name: "Крутить" } : undefined });
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

  // ---------- колёса, задания, розыгрыш ----------
  function wheelSvg(sectors) {
    const n = sectors.length, R = 120, cx = 130, cy = 130;
    return sectors.map((s, i) => {
      const a0 = (i / n) * 2 * Math.PI - Math.PI / 2, a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2;
      const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0), x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
      const am = (a0 + a1) / 2, lx = cx + R * 0.62 * Math.cos(am), ly = cy + R * 0.62 * Math.sin(am);
      const rare = s.p <= 2;
      return `<path d="M${cx},${cy} L${x0},${y0} A${R},${R} 0 0 1 ${x1},${y1} Z" fill="${rare ? "var(--accent)" : i % 2 ? "var(--bg)" : "var(--surface-2)"}" stroke="var(--line)"/>
        <text x="${lx}" y="${ly}" fill="var(--fg)" font-size="12" font-weight="700" text-anchor="middle" dominant-baseline="middle" transform="rotate(${(am * 180) / Math.PI + 90} ${lx} ${ly})">${esc(s.label)}</text>`;
    }).join("");
  }

  function openWheel(kind) {
    kind = kind === "weekly" ? "weekly" : (M.today.wheel_daily || !M.today.wheel_weekly_unlocked ? "daily" : "weekly");
    const sectors = C.wheel[kind];
    const available = kind === "daily" ? M.today.wheel_daily : M.today.wheel_weekly_unlocked;
    const prize = (s) => (s.cases ? ` · ${money(Math.round(C.unit_cents * s.cases))}` : "");
    const body = openSheet(`
      <div class="seg small"><button type="button" data-k="daily" class="${kind === "daily" ? "on" : ""}">Колесо дня</button>
        <button type="button" data-k="weekly" class="${kind === "weekly" ? "on" : ""}">Недельное</button></div>
      <p class="lead">${kind === "daily" ? "Один бесплатный поворот в сутки, пустых секторов нет." : `Открывается, если задания выполнены в ${C.wheel.weekly_unlock_days} днях из 7. Сейчас: ${M.today.quest_days_week} из 7.`} Суммы в ценах самого дешёвого кейса (${money(C.unit_cents)}).</p>
      <div class="wheel-wrap"><span class="pin"></span><svg viewBox="0 0 260 260"><g id="wheel-g">${wheelSvg(sectors)}</g><circle cx="130" cy="130" r="16" fill="var(--surface)" stroke="var(--line)"/></svg></div>
      <table class="odds-table"><tr><th>Приз</th><th>Шанс</th></tr>${sectors.map((s) => `<tr><td>${esc(s.label)}${prize(s)}</td><td>${s.p}%</td></tr>`).join("")}</table>
      <button type="button" class="btn-spin small" id="w-go" ${available ? "" : "disabled"}><span>${available ? "КРУТИТЬ КОЛЕСО" : kind === "daily" ? "ЗАВТРА СНОВА" : "ПОКА ЗАКРЫТО"}</span></button>`);
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
      const row = (t, weekly) => `<div class="task"><b>${esc(t.title)}</b>
        <small>${t.progress}/${t.target} · ${money(t.reward_cents)}${t.tickets ? ` + ${t.tickets} билет` : ""}${t.cosmetic ? " + рамка" : ""}</small>
        <div class="bar"><i style="width:${(t.progress / t.target) * 100}%"></i></div>
        <button type="button" data-q="${t.code}" ${t.claimed || t.progress < t.target ? "disabled" : ""}>${t.claimed ? "Получено" : "Забрать"}</button></div>`;
      const body = openSheet(`
        <h2>Задания</h2>
        <p class="lead">Дневные обновляются в полночь по Москве. Каждое даёт билет в розыгрыш. Задания в ${q.weekly_wheel_unlock_days} днях из 7 открывают недельное колесо: сейчас ${q.week_days} из 7.</p>
        <h3 class="h-sub">Сегодня</h3>${q.daily.map((t) => row(t)).join("")}
        <h3 class="h-sub">Неделя</h3>${q.weekly.map((t) => row(t, true)).join("")}`);
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
        <p class="lead">Участие бесплатное: билеты только за задания, купить их нельзя. Каждый день в ${r.draw_hour_msk}:00 по Москве 10 призов среди всех, у кого есть билеты.</p>
        <div class="stats"><div><small>Мои билеты</small><b>${r.my_tickets}</b></div><div><small>Всего билетов</small><b>${r.total_tickets}</b></div></div>
        <table class="odds-table"><tr><th>Приз</th><th>Сколько</th></tr>${r.prizes.map((p) => `<tr><td>${esc(p.label)}</td><td>${p.count}</td></tr>`).join("")}</table>
        <p class="note">Хеш зерна розыгрыша опубликован заранее:<br><span class="mono">${esc(r.seed_hash)}</span><br>После розыгрыша зерно раскрывается, победителей можно пересчитать.</p>
        ${prev ? `<h3 class="h-sub">Вчера</h3><p class="note">Зерно: <span class="mono">${esc(prev.seed)}</span></p>
          <ol class="board">${prev.winners.map((w) => `<li><span>${esc(w.name)}</span><b>${esc(w.prize)}</b></li>`).join("") || "<li>Билетов не было</li>"}</ol>` : ""}`);
    } catch (err) { handleError(err); }
  }

  // ---------- КОЛЛЕКЦИЯ ----------
  function renderCollection() {
    if (!INV) return;
    const s = INV.summary;
    $("col-slots").textContent = `${s.used} / ${s.total}`;
    $("col-value").textContent = money(s.value_cents);
    $("col-week").textContent = s.week_change_cents ? `${s.week_change_cents > 0 ? "+" : ""}${money(s.week_change_cents)} за неделю` : "";
    $("col-week").className = s.week_change_cents > 0 ? "up" : s.week_change_cents < 0 ? "down" : "";
    const ob = $("overflow-banner");
    ob.hidden = !s.overflow;
    ob.textContent = s.overflow ? `Переполнение: ${s.overflow} из ${C.inventory.overflow_slots}. Освободи место за 48 часов, иначе эти предметы продадутся системе по 60%.` : "";
    const items = INV.items;
    const chips = [{ id: "all", label: "Все" }]
      .concat(CASE_TIERS.filter((t) => items.some((i) => i.tier === t)).map((t) => ({ id: t, label: tier(t).name, icon: tier(t).icon, tier: t })))
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
    const sorters = { new: (a, b) => b.id - a.id, price: (a, b) => b.price_cents - a.price_cents, tier: (a, b) => rank(b.tier) - rank(a.tier) || b.price_cents - a.price_cents, growth: (a, b) => growth(b) - growth(a) };
    list.sort(sorters[ui.colSort]);
    $("col-grid").innerHTML = list.map((i) => tileHtml(i)).join("");
    $("col-empty").hidden = items.length > 0;
  }

  $("col-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-f]"); if (b) { ui.colFilter = b.dataset.f; renderCollection(); } });
  $("col-search").addEventListener("input", (e) => { ui.colSearch = e.target.value; renderCollection(); });
  $("col-sort").addEventListener("change", (e) => { ui.colSort = e.target.value; renderCollection(); });
  $("col-grid").addEventListener("click", (e) => { const t = e.target.closest("[data-item]"); if (t) showItem(findItem(+t.dataset.item)); });
  $("sell-dupes").addEventListener("click", async () => {
    try {
      const q = await api.post("/items/sell-duplicates/quote");
      if (!q.item_ids.length) return toast("Дублей нет: у каждого вида по одному экземпляру");
      sellItems(q.item_ids, false);
    } catch (err) { handleError(err); }
  });
  $("sell-cheap").addEventListener("click", () => {
    const body = openSheet(`<h2>Продать дешевле…</h2>
      <p class="lead">Продадим всё, что дешевле этой суммы. Легендарные и выше и предметы под замком не трогаем.</p>
      <label class="lead" for="cheap-v">Сумма, ${esc(M.currency.symbol)}</label>
      <input class="field" id="cheap-v" type="number" min="0" step="any" inputmode="decimal" value="${(50 * M.currency.rate).toFixed(M.currency.decimals)}">
      <button type="button" class="btn-spin small" id="cheap-go"><span>ПОКАЗАТЬ СУММУ</span></button>`);
    body.querySelector("#cheap-go").onclick = async () => {
      const v = parseFloat(String(body.querySelector("#cheap-v").value).replace(",", ".")) || 0;
      const cents = Math.round((v / M.currency.rate) * 100);
      try {
        const q = await api.post("/items/sell-cheaper/quote", { max_cents: cents });
        if (!q.item_ids.length) return toast("Таких предметов нет");
        sellItems(q.item_ids, false);
      } catch (err) { handleError(err); }
    };
  });

  // ---------- РЫНОК ----------
  function renderMarket() {
    document.querySelector('#market-seg [data-sub="upgrade"]').hidden = settings.noUpgrader;
    if (settings.noUpgrader && ui.market === "upgrade") ui.market = "lots";
    document.querySelectorAll("#market-seg button").forEach((b) => b.classList.toggle("on", b.dataset.sub === ui.market));
    ["lots", "upgrade", "contract", "trades"].forEach((s) => ($("sub-" + s).hidden = s !== ui.market));
    if (ui.market === "lots") loadLots();
    if (ui.market === "upgrade") renderUpgrade();
    if (ui.market === "contract") renderContract();
    if (ui.market === "trades") loadTrades();
  }
  $("market-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sub]");
    if (!b) return;
    ui.market = b.dataset.sub;
    renderMarket();
    if (ui.market === "upgrade" && !ui.up.targets.length) loadTargets();
  });
  $("lots-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-view]");
    if (!b) return;
    ui.lotsView = b.dataset.view;
    document.querySelectorAll("#lots-seg button").forEach((x) => x.classList.toggle("on", x === b));
    loadLots();
  });
  $("lots-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    ui.lotsTier = b.dataset.t === "all" ? null : b.dataset.t;
    loadLots();
  });

  function lotRow(l, action) {
    const it = l.item;
    return `<div class="lot" style="${tc(it.tier)}">
      <img src="${esc(it.image_url)}" alt="" loading="lazy">
      <div class="lot-main"><b>${it.stattrak ? "ST™ " : ""}${itemTitle(it)}</b>
        <small>${it.wear || ""} · ${esc(tier(it.tier).name)} · ${esc(l.seller_name)}</small>
        <small>${l.price_pct}% справочной</small></div>
      <div class="lot-side"><em>${money(l.price_cents)}</em>${action}</div></div>`;
  }

  async function loadLots() {
    $("lots-chips").hidden = ui.lotsView !== "all";
    $("lots-chips").innerHTML = [["all", "Все"]].concat(CASE_TIERS.map((t) => [t, tier(t).name])).map(([id, label]) =>
      `<button type="button" class="chip ${(ui.lotsTier || "all") === id ? "on" : ""}" data-t="${id}" ${id !== "all" ? `style="${tc(id)}"` : ""}>${id !== "all" ? `<i>${tier(id).icon}</i>` : ""}${esc(label)}</button>`).join("");
    const list = $("lots-list");
    try {
      if (ui.lotsView === "orders") {
        const { orders } = await api.get("/market/orders");
        list.innerHTML = orders.length ? orders.map((o) => `<div class="lot" style="${tc(o.tier)}"><img src="${esc(o.image_url)}" alt="">
          <div class="lot-main"><b>${o.stattrak ? "ST™ " : ""}${esc(o.name)}</b><small>${o.wear || ""} · ${o.status === "active" ? "ждёт продавца" : o.status === "filled" ? "исполнена" : "отменена"}</small></div>
          <div class="lot-side"><em>до ${money(o.max_price_cents)}</em>${o.status === "active" ? `<button type="button" class="mini" data-cancel-order="${o.id}">Снять</button>` : ""}</div></div>`).join("")
          : '<p class="empty">Заявок нет. Открой лот на витрине и нажми «Хочу дешевле», монеты заблокируются до покупки.</p>';
        return;
      }
      if (ui.lotsView === "deals") {
        const { deals } = await api.get("/market/deals");
        list.innerHTML = deals.length ? deals.map((d) => lotRow(d, `<small class="${d.side === "sell" ? "up" : ""}">${d.side === "sell" ? "продал" : "купил"} ${dayStr(d.sold_at)}</small>`)).join("")
          : '<p class="empty">Сделок пока не было.</p>';
        return;
      }
      const q = ui.lotsView === "mine" ? "?mine=1" : `?sort=new${ui.lotsTier ? "&tier=" + ui.lotsTier : ""}`;
      const { lots } = await api.get("/market/lots" + q);
      const mine = ui.lotsView === "mine";
      list.innerHTML = lots.length ? lots.map((l) => lotRow(l, mine ? `<button type="button" class="mini" data-cancel="${l.id}">Снять</button>`
        : l.seller_id === M.user.id ? "<small>твой лот</small>" : `<button type="button" class="mini primary" data-lot="${l.id}">Купить</button>`)).join("")
        : `<p class="empty">${mine ? "Лотов нет. Выставить предмет можно из его карточки в коллекции." : "На витрине пока пусто."}</p>`;
      list.querySelectorAll("[data-lot]").forEach((b) => (b.onclick = () => openLot(lots.find((l) => l.id === +b.dataset.lot))));
    } catch (err) { handleError(err); }
  }

  $("lots-list").addEventListener("click", async (e) => {
    const c = e.target.closest("[data-cancel]");
    const o = e.target.closest("[data-cancel-order]");
    try {
      if (c) { await api.del(`/market/lots/${c.dataset.cancel}`); toast("Лот снят"); await refresh(); loadLots(); }
      if (o) { await api.del(`/market/orders/${o.dataset.cancelOrder}`); toast("Заявка снята, монеты вернулись"); await refresh(false); loadLots(); }
    } catch (err) { handleError(err); }
  });

  function openLot(l) {
    const it = l.item;
    const body = openSheet(`
      <div class="tier-label" style="${tc(it.tier)}">${tier(it.tier).icon} ${esc(tier(it.tier).name)}<small>продаёт ${esc(l.seller_name)}</small></div>
      <div class="item-hero" style="${tc(it.tier)}"><img src="${esc(it.image_url)}" alt=""></div>
      <div class="item-name"><b>${it.stattrak ? "StatTrak™ " : ""}${itemTitle(it)}</b><span>${it.wear ? WEAR[it.wear] : ""}${it.float != null ? " · " + it.float.toFixed(4) : ""}</span></div>
      <div class="stats"><div><small>Цена лота</small><b>${money(l.price_cents)}</b></div><div><small>Справочная</small><b>${money(it.price_cents)}</b></div></div>
      <div class="actions"><button type="button" class="primary" id="lot-buy">Купить за ${money(l.price_cents)}</button><button type="button" id="lot-order">Хочу дешевле</button></div>
      <p class="note">После покупки предмет 24 часа на удержании: перепродать или обменять его можно будет завтра.</p>`);
    body.querySelector("#lot-buy").onclick = async () => {
      const ok = await confirmSheet({ title: "Купить?", text: `${itemTitle(it)} за <b>${money(l.price_cents)}</b>.`, ok: "Купить" });
      if (!ok) return;
      try {
        await api.post(`/market/lots/${l.id}/buy`);
        haptic("success");
        await refresh();
        toast("Куплено: предмет в коллекции");
        loadLots();
      } catch (err) { handleError(err); }
    };
    body.querySelector("#lot-order").onclick = () => openOrder(it, l.price_cents);
  }

  function priceInput(label, cents, lo, hi) {
    const val = ((cents / 100) * M.currency.rate).toFixed(M.currency.decimals);
    return `<label class="lead" for="price-v">${esc(label)}, ${esc(M.currency.symbol)} (от ${money(lo)} до ${money(hi)})</label>
      <input class="field" id="price-v" type="number" step="any" inputmode="decimal" value="${val}">`;
  }
  const readPrice = (body) => Math.round(((parseFloat(String(body.querySelector("#price-v").value).replace(",", ".")) || 0) / M.currency.rate) * 100);

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

  // ---------- апгрейдер ----------
  function upStake() {
    ui.up.items = ui.up.items.filter((id) => { const i = findItem(id); return i && !i.locked && i.state === "free"; });
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
    $("up-formula").innerHTML = t ? `<b>${money(sum)}</b> ÷ <b>${money(t.price_cents)}</b> × 0,8${bonus} = <b>${pct(chance, 2)}</b> (потолок ${C.upgrader.max_chance_pct}%)`
      : "Шанс = ставка ÷ цель × 0,8, не больше 80%. Выбери ставку и цель.";
    $("up-mults").innerHTML = C.upgrader.multipliers.map((m) => `<button type="button" data-m="${m}" class="${ui.up.mult === m ? "on" : ""}">×${String(m).replace(".", ",")}</button>`).join("");
    $("up-targets").innerHTML = sum > 0 ? ui.up.targets.map((x, i) => `<button type="button" class="tile ${t && t.catalog_item_id === x.catalog_item_id && t.wear === x.wear && t.stattrak === x.stattrak ? "sel" : ""}" data-t="${i}" style="${tc(x.tier)}">
        <img src="${esc(x.image_url)}" alt="" loading="lazy"><span class="w">${x.stattrak ? '<span class="st">ST™</span> ' : ""}${x.wear || ""}</span><span class="n">${esc(x.name)}</span><span class="p">${money(x.price_cents)}</span></button>`).join("") : "";
    $("up-go").disabled = !(chance > 0) || ui.up.spinning || M.upgrades_left <= 0;
    $("up-note").textContent = `Осталось сегодня: ${M.upgrades_left} из ${C.upgrader.per_day}. При неудаче ставка сгорает, назад приходят запчасти на 10% ставки, а следующий апгрейд получает +3% к шансу.`;
  }

  async function loadTargets() {
    const { sum } = upStake();
    if (sum <= 0) { ui.up.targets = []; renderUpgrade(); return; }
    try {
      const res = await api.get(`/upgrades/targets?stake_cents=${sum}&mult=${ui.up.mult}`);
      ui.up.targets = res.targets;
      if (!ui.up.target || ui.up.target.price_cents <= sum) ui.up.target = res.targets[0] || null;
    } catch (err) { handleError(err); }
    renderUpgrade();
  }

  $("up-mults").addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (!b) return; ui.up.mult = +b.dataset.m; ui.up.target = null; loadTargets(); });
  $("up-targets").addEventListener("click", (e) => { const b = e.target.closest("[data-t]"); if (!b) return; ui.up.target = ui.up.targets[+b.dataset.t]; haptic("tick"); renderUpgrade(); });
  $("up-target").addEventListener("click", () => { if (!ui.up.targets.length) loadTargets(); });
  $("up-stake").addEventListener("click", openStakePicker);

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
  const freeItems = () => INV.items.filter((i) => i.state === "free" && !i.locked && !i.frozen).sort((a, b) => b.price_cents - a.price_cents);

  function openStakePicker() {
    pickerGrid(freeItems(), ui.up.items, C.upgrader.max_items, (ids, body) => {
      const v = parseFloat(String(body.querySelector("#up-coins").value).replace(",", ".")) || 0;
      ui.up.coins = Math.max(0, Math.min(Math.round((v / M.currency.rate) * 100), M.balance_cents));
      ui.up.items = ids;
      ui.up.target = null;
      closeSheet();
      loadTargets();
    }, `<h2>Ставка</h2><p class="lead">До ${C.upgrader.max_items} предметов и монеты. За раз — не больше половины коллекции и баланса.</p>
      <label for="up-coins" class="lead">Добавить монет, ${esc(M.currency.symbol)} (баланс ${money(M.balance_cents)})</label>
      <input class="field" id="up-coins" type="number" min="0" step="any" inputmode="decimal" value="${ui.up.coins ? ((ui.up.coins / 100) * M.currency.rate).toFixed(M.currency.decimals) : ""}" placeholder="0">`);
  }

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
        const ok = await confirmSheet({ title: "Точно?", text: `Ставка ${money(sum)} сгорит с шансом ${pct(1 - quote.chance_pct / 100, 1)}.`, ok: "Улучшить", pause: C.upgrader.confirm_pause_s });
        if (!ok) return;
      }
      const res = await api.post("/upgrades", { stake_item_ids: ui.up.items, stake_coins_cents: ui.up.coins, target, quote_chance_pct: quote.chance_pct, confirmed: true });
      await spinNeedle(res.roll / 100);
      haptic(res.success ? "success" : "error");
      Object.assign(ui.up, { items: [], coins: 0, target: null, targets: [] });
      await refresh();
      if (res.success) showItem(res.item, { fresh: true });
      else openSheet(`<h2>Не получилось</h2><p>Шанс был ${String(res.chance_pct).replace(".", ",")}%, стрелка остановилась на ${String(res.roll.toFixed(1)).replace(".", ",")}%.</p>
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
        const ms = settings.fast ? 500 : 3600;
        n.style.transition = `transform ${ms}ms cubic-bezier(.15,.7,.15,1)`;
        n.style.transform = `rotate(${360 * 4 + roll * 360}deg)`;
        setTimeout(resolve, ms + 200);
      }));
    });
  }

  // ---------- 5 в 1 ----------
  function renderContract() {
    if (!INV) return;
    const next = C.contracts.next;
    $("ct-chips").innerHTML = Object.keys(next).map((t) => {
      const n = INV.items.filter((i) => i.tier === t && i.state === "free" && !i.locked).length;
      return `<button type="button" class="chip ${ui.ct.tier === t ? "on" : ""}" data-ct="${t}" style="${tc(t)}"><i>${tier(t).icon}</i>${esc(tier(t).name)} · ${n}</button>`;
    }).join("");
    const pool = INV.items.filter((i) => i.tier === ui.ct.tier && i.state === "free" && !i.locked);
    ui.ct.items = ui.ct.items.filter((id) => pool.some((i) => i.id === id));
    const chosen = ui.ct.items.map((id) => pool.find((i) => i.id === id));
    $("ct-slots").innerHTML = Array.from({ length: 5 }, (_, k) => chosen[k]
      ? `<div class="ct-slot" style="border:1px solid var(--t-${ui.ct.tier})"><img src="${esc(chosen[k].image_url)}" alt=""></div>`
      : '<div class="ct-slot"><span class="plus small">+</span></div>').join("");
    const sum = chosen.reduce((s, i) => s + i.price_cents, 0);
    const nt = tier(next[ui.ct.tier]);
    const ok = rank(ui.ct.tier) <= rank(C.contracts.guaranteed_up_to) ? 1 : C.contracts.success_pct / 100;
    $("ct-info").innerHTML = `${esc(tier(ui.ct.tier).name)} ×5 → <b style="color:var(--t-${next[ui.ct.tier]})">${nt.icon} ${esc(nt.name)}</b> · успех <b>${pct(ok, 0)}</b>${ok < 1 ? ", иначе предмет той же ступени" : ""} · вклад ${money(sum)}`;
    $("ct-grid").innerHTML = pool.map((i) => tileHtml(i, ui.ct.items.includes(i.id) ? "sel" : "")).join("");
    $("ct-go").disabled = chosen.length !== 5;
  }
  $("ct-chips").addEventListener("click", (e) => { const b = e.target.closest("[data-ct]"); if (!b) return; ui.ct.tier = b.dataset.ct; ui.ct.items = []; renderContract(); });
  $("ct-grid").addEventListener("click", (e) => {
    const t = e.target.closest("[data-item]");
    if (!t) return;
    const id = +t.dataset.item;
    const i = ui.ct.items.indexOf(id);
    if (i >= 0) ui.ct.items.splice(i, 1);
    else if (ui.ct.items.length < 5) ui.ct.items.push(id);
    haptic("tick");
    renderContract();
  });
  $("ct-go").addEventListener("click", async () => {
    try {
      const res = await withConfirm((c) => api.post("/contracts", { item_ids: ui.ct.items, confirmed: c }), "Собрать 5 в 1?", "Собрать");
      if (!res) return;
      haptic(res.success ? "success" : "warning");
      ui.ct.items = [];
      await refresh();
      showItem(res.item, { fresh: true });
      if (!res.success) toast("Ступень не поднялась, но предмет твой");
    } catch (err) { handleError(err); }
  });

  // ---------- обмены ----------
  async function loadTrades() {
    try {
      const { trades } = await api.get("/trades");
      const mini = (items) => items.map((i) => `<img src="${esc(i.image_url)}" alt="" title="${esc(i.name)}">`).join("");
      $("trades-list").innerHTML = trades.length ? trades.map((t) => `
        <div class="trade">
          <div class="trade-h"><b>${t.incoming ? esc(t.from_name) + " предлагает" : "Ты → " + esc(t.to_name)}</b><small>${{ pending: "ждёт ответа", accepted: "принят", declined: "отклонён", cancelled: "отменён", expired: "истёк" }[t.status]}</small></div>
          <div class="trade-sides"><div><small>${t.incoming ? "Отдаёт" : "Отдаёшь"} · ${money(t.value_from_cents)}</small><div class="mini-items">${mini(t.give)}</div></div>
            <div><small>${t.incoming ? "Просит" : "Просишь"} · ${money(t.value_to_cents)}</small><div class="mini-items">${mini(t.take) || "—"}</div></div></div>
          ${t.status === "pending" ? (t.incoming ? `<div class="actions"><button type="button" class="primary" data-tr="${t.id}" data-a="accept">Принять</button><button type="button" data-tr="${t.id}" data-a="decline">Отклонить</button></div>`
            : `<div class="actions"><button type="button" class="wide" data-tr="${t.id}" data-a="cancel">Отменить</button></div>`) : ""}
        </div>`).join("") : '<p class="empty">Обменов пока не было.</p>';
    } catch (err) { handleError(err); }
  }
  $("trades-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-tr]");
    if (!b) return;
    if (b.dataset.a === "accept" && !(await confirmSheet({ title: "Принять обмен?", text: "Предметы сразу поменяются владельцами и 24 часа будут на удержании.", ok: "Принять" }))) return;
    try {
      await api.post(`/trades/${b.dataset.tr}/${b.dataset.a}`);
      await refresh();
      loadTrades();
    } catch (err) { handleError(err); }
  });
  $("trade-new").addEventListener("click", async () => {
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
              loadTrades();
            } catch (err) { handleError(err); }
          }, `<h2>Что просишь у ${esc(other.name || "игрока")}</h2><p class="lead">Можно ничего не просить: тогда это подарок.</p>`);
        }, `<h2>Что отдаёшь</h2>`);
      } catch (err) { handleError(err); }
    };
  });

  // ---------- ДРУЗЬЯ ----------
  async function renderFriends() {
    document.querySelectorAll("#friends-seg button").forEach((b) => b.classList.toggle("on", b.dataset.sub === ui.friends));
    ["invite", "rating", "duels"].forEach((s) => ($("sub-" + s).hidden = s !== ui.friends));
    try {
      if (ui.friends === "invite") {
        const f = await api.get("/friends");
        $("ref-text").textContent = `Когда друг откроет ${f.qualify_spins} кейсов, он засчитается. Друзей в игре: ${f.qualified}.`;
        $("ref-steps").innerHTML = f.tiers.map((t) => `<div class="${t.granted ? "done" : ""}"><b>${t.friends}</b><small>${t.spins ? t.spins + " спина" : ""}${t.cases ? money(Math.round(C.unit_cents * t.cases)) : ""}${t.cosmetic ? (t.spins || t.cases ? " + " : "") + C.cosmetics[t.cosmetic].title.toLowerCase() : ""}</small></div>`).join("");
        $("friends-list").innerHTML = f.invited.map((x) => `<div class="lot"><div class="lot-main"><b>${esc(x.name)}</b><small>${x.qualified ? "в игре" : `открыл ${x.spins} из ${f.qualify_spins}`}</small></div></div>`).join("");
      }
      if (ui.friends === "rating") {
        const r = await api.get(`/leaderboard?board=${ui.board}&scope=${ui.scope}`);
        const fmt = (v) => (ui.board === "level" ? `${v} XP` : ui.board === "collection" ? `${v} видов` : money(v));
        $("board").innerHTML = r.rows.map((x) => `<li class="${x.me ? "me" : ""}"><span>${x.place}. ${esc(x.name)} <small>ур. ${x.level}</small></span><b>${fmt(x.value)}</b></li>`).join("")
          || '<li>Пока пусто. Позови друзей, чтобы было с кем сравниться.</li>';
      }
      if (ui.friends === "duels") {
        const d = await api.get("/duels");
        $("duels-list").innerHTML = (d.duels.length ? d.duels.map((x) => {
          const mine = x.p1 === M.user.id;
          const status = x.status === "open" ? (mine ? "ждёт соперника" : "можно принять") : x.status === "done" ? (x.winner === M.user.id ? "победа" : "поражение") : "закрыта";
          return `<div class="trade"><div class="trade-h"><b>${esc(x.p1_name)}${x.p2 ? " vs " + esc(x.p2_name) : ""}</b><small class="${status === "победа" ? "up" : ""}">${status}</small></div>
            <div class="mini-items">${x.p1_items.map((i) => `<img src="${esc(i.image_url)}" alt="">`).join("")}</div>
            ${x.status === "done" ? `<small class="note">Сила: ${money(x.p1_power)} против ${money(x.p2_power)}</small>` : ""}
            ${x.status === "open" ? `<div class="actions"><button type="button" class="${mine ? "wide" : "primary wide"}" data-duel="${x.id}" data-a="${mine ? "cancel" : "join"}">${mine ? "Отменить" : "Принять дуэль"}</button></div>` : ""}</div>`;
        }).join("") : "") + `<p class="note">Сегодня осталось дуэлей: ${d.left_today}.</p>`;
      }
    } catch (err) { handleError(err); }
  }
  $("friends-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-sub]"); if (b) { ui.friends = b.dataset.sub; renderFriends(); } });
  $("board-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-board]"); if (!b) return; ui.board = b.dataset.board; document.querySelectorAll("#board-seg button").forEach((x) => x.classList.toggle("on", x === b)); renderFriends(); });
  $("scope-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-scope]"); if (!b) return; ui.scope = b.dataset.scope; document.querySelectorAll("#scope-seg button").forEach((x) => x.classList.toggle("on", x === b)); renderFriends(); });
  $("invite-btn").addEventListener("click", () => {
    const link = M.app_link || M.invite_link || "";
    const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Открывай со мной настоящие кейсы CS2 в Telegram. Тебе 3 бесплатных спина!")}`;
    const t = api.tg();
    if (t && t.openTelegramLink && !window.SKINS_DEMO) t.openTelegramLink(url);
    else toast("В Telegram откроется выбор чата со ссылкой-приглашением");
  });
  $("duel-new").addEventListener("click", () => {
    pickerGrid(INV.items.filter((i) => i.state === "free" && !i.overflow_until).sort((a, b) => b.price_cents - a.price_cents), [], 3, async (ids) => {
      if (ids.length !== 3) return toast("Нужно ровно 3 предмета");
      try {
        await api.post("/duels", { item_ids: ids });
        closeSheet();
        await refresh();
        toast("Дуэль создана: ждём соперника");
        renderFriends();
      } catch (err) { handleError(err); }
    }, `<h2>Твоя тройка</h2><p class="lead">Выбери 3 предмета. Они не теряются: после дуэли вернутся в коллекцию.</p>`);
  });
  $("duels-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-duel]");
    if (!b) return;
    if (b.dataset.a === "cancel") {
      try { await api.post(`/duels/${b.dataset.duel}/cancel`); await refresh(); renderFriends(); } catch (err) { handleError(err); }
      return;
    }
    pickerGrid(INV.items.filter((i) => i.state === "free" && !i.overflow_until).sort((a, c) => c.price_cents - a.price_cents), [], 3, async (ids) => {
      if (ids.length !== 3) return toast("Нужно ровно 3 предмета");
      try {
        const res = await api.post(`/duels/${b.dataset.duel}/join`, { item_ids: ids });
        closeSheet();
        haptic(res.you_won ? "success" : "warning");
        await refresh();
        openSheet(`<h2>${res.you_won ? "Победа!" : "Соперник сильнее"}</h2>
          <div class="stats"><div><small>Соперник</small><b>${money(res.p1_power)}</b></div><div><small>Ты</small><b>${money(res.p2_power)}</b></div></div>
          <p class="lead">${res.you_won ? `+${money(res.prize_cents)} от игры.` : "Предметы остались у тебя."}</p>`);
        renderFriends();
      } catch (err) { handleError(err); }
    }, `<h2>Твоя тройка</h2><p class="lead">Выбери 3 предмета против тройки соперника.</p>`);
  });

  // ---------- ЕЩЁ ----------
  function renderMore() {
    const u = M.user;
    const xpPct = Math.min(100, ((u.xp - u.xp_prev) / Math.max(1, u.xp_next - u.xp_prev)) * 100);
    const show = (M.showcase || []).map(findItem).filter(Boolean);
    $("profile").innerHTML = `
      <div class="pc-head ${u.frame ? "framed" : ""}" style="${u.frame ? tc(C.cosmetics[u.frame].tier) : ""}">
        <div><b>${esc(u.name || "Игрок")}</b><small>ID ${u.id}${u.vip_until ? " · VIP" : ""}</small></div>
        <div class="lvl"><b>Ур. ${u.level}</b><div class="bar"><i style="width:${xpPct}%"></i></div><small>${u.xp} / ${u.xp_next} XP</small></div>
      </div>
      <div class="pc-stats"><div><small>Кейсов</small><b>${M.total_spins}</b></div><div><small>Предметов</small><b>${INV ? INV.summary.used : 0}</b></div><div><small>Коллекция</small><b>${INV ? money(INV.summary.value_cents) : "—"}</b></div></div>
      <div class="showcase">${show.length ? show.map((i) => tileHtml(i)).join("") : '<p class="note">Витрина пустая: добавь до 3 лучших предметов из их карточек.</p>'}</div>`;
    $("country-label").textContent = `${C.currency.countries[M.country].name}, ${M.currency.symbol}`;
  }
  $("profile").addEventListener("click", (e) => { const t = e.target.closest("[data-item]"); if (t) showItem(findItem(+t.dataset.item)); });
  document.querySelector(".menu-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    ({ shop: openShop, raffle: openRaffle, fair: openFair, odds: () => openOdds(ui.level), cosmetics: openCosmetics, country: openCountry, settings: openSettings, terms: openTerms })[b.dataset.open]();
  });

  async function openShop() {
    try {
      const s = await api.get("/shop");
      const body = openSheet(`<h2>Магазин ⭐</h2>
        ${s.enabled ? "" : '<p class="banner">Покупки за Stars откроются после юридической проверки. Пока можно посмотреть, что будет.</p>'}
        <p class="lead">Платно только попытки, удача, VIP и места. Монеты и кейсы за Stars не продаются. Траты за месяц: ${s.month_spent} из ${s.month_cap} ⭐.</p>
        ${s.warn ? '<p class="banner">Ты потратил больше 80% месячного лимита.</p>' : ""}
        <div class="shop">${s.products.map((p) => `<div class="product"><div><b>${esc(p.title)}</b>${p.reason && s.enabled ? `<small>${esc(p.reason)}</small>` : ""}</div>
          <button type="button" class="star-btn" data-p="${p.code}" ${s.enabled && p.available ? "" : "disabled"}>${p.stars} ⭐</button></div>`).join("")}</div>
        <p class="note">Удача действует на 10 следующих спинов Обычной крутки и не складывается. Неиспользованные попытки и удачу можно вернуть через /paysupport.</p>`);
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
    if (res.success !== undefined) return res.success ? "успех" : "неудача";
    if (res.p1 !== undefined) return `сила ${money(res.p1)} против ${money(res.p2)}`;
    return "";
  }

  async function openFair(revealed) {
    try {
      const f = await api.get("/fair");
      const kinds = { spin: "Спин", upgrade: "Апгрейд", contract: "5 в 1", wheel_daily: "Колесо дня", wheel_weekly: "Недельное колесо", duel: "Дуэль" };
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

  function openOdds(level) {
    const scale = (odds) => odds.map((o) => `<tr style="${tc(o.tier)}"><td><span style="color:var(--tc)">${tier(o.tier).icon}</span> ${esc(tier(o.tier).name)}</td><td>${esc(tier(o.tier).cs2 || "")}</td><td>${pct(o.p, 2)}${o.p < 0.1 ? ` · 1 из ${Math.round(1 / o.p)}` : ""}</td></tr>`).join("");
    const wears = C.wear.map((w) => `<tr><td>${WEAR[w[0]]}</td><td>${String(w[1]).replace(".", ",")}–${String(w[2]).replace(".", ",")}</td><td>${w[3]}%</td></tr>`).join("");
    openSheet(`
      <h2>Шансы</h2>
      <p class="lead">Шансы одинаковы для всех и совпадают с теми, по которым бросает сервер. «Шанс 1 из N» на карточке всегда считается по шкале обычного кейса.</p>
      <h3 class="h-sub">Обычный кейс CS2 (как у Valve)</h3>
      <table class="odds-table"><tr><th>Ступень</th><th>В CS2</th><th>Шанс</th></tr>${scale(C.case_odds)}</table>
      ${C.levels.filter((l) => l.odds).map((l) => `<h3 class="h-sub">${esc(l.name)} · ${money(l.price_cents)} · ${esc(l.what)}</h3><table class="odds-table">${scale(l.odds)}</table>`).join("")}
      <h3 class="h-sub">Износ</h3>
      <table class="odds-table"><tr><th>Износ</th><th>Float</th><th>Шанс</th></tr>${wears}</table>
      <p class="lead">StatTrak™ — 10%, если у скина он бывает. Гарантия на Обычной: Эпик и выше не позже 60-го кейса подряд, Легендарный и выше не позже 300-го; шанс растёт заранее, с 51-го и 281-го. Уровни выше Обычной без гарантий. Цена кейса и уровня = средняя цена выпадения по рынку / 0,8.</p>`);
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
      <p class="lead">Страна меняет только то, как показываются суммы: ${money(100000)} у нас это ${list.slice(1, 4).map(([, c]) => Math.round(1000 * c.rate).toLocaleString("ru-RU") + " " + c.symbol).join(", ")}. Цены, шансы и баланс одни для всех. Менять можно раз в 30 дней.</p>
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
      <label class="toggle" for="s-noup"><span>Без апгрейдера<br><small class="lead">вкладка апгрейдера скрыта</small></span><input type="checkbox" id="s-noup" ${settings.noUpgrader ? "checked" : ""}></label>`);
    body.addEventListener("change", () => {
      settings.fast = body.querySelector("#s-fast").checked;
      settings.nofx = body.querySelector("#s-nofx").checked;
      settings.noUpgrader = body.querySelector("#s-noup").checked;
      saveSettings();
      document.body.classList.toggle("no-fx", settings.nofx);
    });
  }

  function openTerms() {
    openSheet(`<h2>Условия</h2>
      <p>Игровые рубли есть только внутри бота. Их нельзя купить за деньги, вывести или обменять на рубли, Stars или настоящие скины. Валюта твоей страны на экране — это пересчёт по игровому курсу.</p>
      <p>Предметы — игровые копии скинов CS2 с ценами рынка. Вывода в Steam нет.</p>
      <p>За Stars продаются только попытки, удача, VIP и места, месячный лимит 500 ⭐.</p>
      <p>Шансы открыты на экране «Шансы», каждый бросок проверяется на экране «Честная игра».</p>
      <p>Уведомлений не больше 2 в день, ночью с 21:00 до 8:00 их нет.</p>`);
  }

  // ---------- навигация ----------
  function switchTab(tab, sub) {
    ui.tab = tab;
    if (sub) ui.market = sub;
    document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.dataset.tab !== tab));
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    $("screens").scrollTop = 0;
    if (tab === "market") { renderMarket(); if (ui.market === "upgrade" && !ui.up.targets.length) loadTargets(); }
    if (tab === "friends") renderFriends();
  }
  $("tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (!b) return; haptic("tick"); switchTab(b.dataset.tab); });
  $("balance").addEventListener("click", () => openSheet(`
    <h2>${money(M.balance_cents)} ${esc(M.currency.game_label || "")}</h2>
    <p class="lead">Игровые рубли: 1 игровой рубль = 1 рубль рыночной цены скина (курс ЦБ на ${esc(new Date(C.generated_at * 1000).toLocaleDateString("ru-RU"))}: ${String(C.usd_rub).replace(".", ",")} ₽ за $). За настоящие деньги не продаются и не выводятся.</p>
    <div class="stats"><div><small>Как получить</small><b class="txt">продажа предметов, колесо, задания, дуэли, розыгрыш</b></div>
    <div><small>Куда потратить</small><b class="txt">кейсы, уровни крутки, апгрейдер, рынок</b></div></div>`));
  $("energy").addEventListener("click", () => toast(`Бесплатный спин раз в 3 часа, копится до 3. Работает на кейсе ${C.cases.find((c) => c.free).name}.`));

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
      ui.caseId = C.free_case_id;
      if (!C.levels.some((l) => l.id === ui.level)) ui.level = "common";
      await refresh();
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
