// Мини-приложение «Кейсы CS2». Всё случайное решает сервер, клиент только показывает результат.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TIER_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret", "divine", "exclusive"];
  const CASE_TIERS = ["rare", "epic", "legendary", "mythic", "secret"];
  const WEAR = { FN: "Factory New", MW: "Minimal Wear", FT: "Field-Tested", WW: "Well-Worn", BS: "Battle-Scarred" };
  const WEAR_SHORT = { FN: "FN", MW: "MW", FT: "FT", WW: "WW", BS: "BS" };
  const rank = (t) => TIER_ORDER.indexOf(t);

  let catalog = null;   // /api/catalog
  let S = null;         // /api/state
  let stateAt = 0;
  let caseId = null;
  let count = 1;
  let busy = false;
  const settings = loadSettings();
  const ui = {
    tab: "play", sub: "upgrade", colFilter: "all", colSort: "new", colSearch: "",
    up: { items: [], coins: 0, mult: 2, target: null, targets: [], spinning: false },
    ct: { tier: "rare", items: [] },
  };

  // ---------- утилиты ----------
  function loadSettings() {
    const d = { fast: false, nofx: false, noUpgrader: false };
    try { return Object.assign(d, JSON.parse(localStorage.getItem("settings") || "{}")); } catch (e) { return d; }
  }
  function saveSettings() { try { localStorage.setItem("settings", JSON.stringify(settings)); } catch (e) { /* без хранилища */ } }
  function money(c) {
    const v = (c / 100).toFixed(2).split(".");
    return "$" + v[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ") + "." + v[1];
  }
  function pct(p, digits) {
    const d = digits != null ? digits : p >= 0.1 ? 1 : 2;
    return (p * 100).toFixed(d).replace(".", ",") + "%";
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function tc(tier) { return `--tc: var(--t-${tier})`; }
  function tierInfo(t) { return (catalog && catalog.tiers[t]) || { name: t, icon: "" }; }
  function idem() { return (crypto.randomUUID ? crypto.randomUUID() : Date.now() + "-" + Math.random()); }
  function haptic(kind) {
    const t = window.Api.tg();
    if (!t || !t.HapticFeedback) return;
    try {
      if (kind === "tick") t.HapticFeedback.selectionChanged();
      else if (kind === "light" || kind === "medium" || kind === "heavy") t.HapticFeedback.impactOccurred(kind);
      else t.HapticFeedback.notificationOccurred(kind);
    } catch (e) { /* старый клиент */ }
  }
  let toastTimer;
  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 2600);
  }
  function itemTitle(it) { return `${esc(it.weapon)}${it.finish ? " | " + esc(it.finish) : ""}`; }
  function wearLine(it) {
    const parts = [];
    if (it.stattrak) parts.push('<span class="st">ST™</span>');
    if (it.wear) parts.push(WEAR_SHORT[it.wear]);
    return parts.join(" ");
  }
  const selectedCase = () => catalog.cases.find((c) => c.id === caseId);

  // ---------- шторка ----------
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

  // Подтверждение внутри страницы; pause — секунды, пока кнопка неактивна.
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
          if (left <= 0) { clearInterval(iv); btn.disabled = false; btn.textContent = ok; }
          else btn.querySelector(".count-down").textContent = left;
        }, 1000);
      }
      body.querySelector("#cf-no").onclick = () => closeSheet();
      btn.onclick = () => { sheetClose = null; closeSheet(); resolve(true); };
    });
  }

  function handleError(err) {
    haptic("error");
    if (err.code === "no_money") {
      const body = openSheet(`<h2>${esc(err.message)}</h2>
        <p>Игровые доллары дают колесо дня, задания и продажа предметов.</p>
        <div class="actions"><button type="button" id="ne-wheel">Колесо дня</button><button type="button" id="ne-tasks">Задания</button></div>`);
      body.querySelector("#ne-wheel").onclick = openWheel;
      body.querySelector("#ne-tasks").onclick = openTasks;
      return;
    }
    toast(err.message || "Что-то пошло не так. Попробуй ещё раз.");
  }

  // ---------- загрузка ----------
  async function refresh() {
    S = await window.Api.get("/api/state");
    stateAt = Date.now();
    renderAll();
  }

  function renderAll() {
    renderHeader();
    renderPlay();
    renderCollection();
    renderMarket();
    renderFriends();
    renderMore();
  }

  function renderHeader() {
    $("balance-value").textContent = money(S.user.balance);
    $("energy-value").textContent = `${S.user.energy}/${S.user.energy_max}`;
  }

  // ---------- ИГРАТЬ ----------
  function renderPlay() {
    $("case-rail").innerHTML = catalog.cases.map((c) => `
      <button type="button" class="case-chip ${c.id === caseId ? "on" : ""}" data-case="${c.id}" role="option" aria-selected="${c.id === caseId}">
        ${c.free ? '<span class="free-tag">бесплатный</span>' : ""}
        <img src="${esc(c.image)}" alt="" loading="lazy"><span>${esc(c.name.replace(/ Case$/, ""))}</span><b>${money(c.price)}</b>
      </button>`).join("");
    const c = selectedCase();
    $("case-image").src = c.image;
    $("case-name").textContent = c.name.replace(/ Case$/, "");
    $("odds").innerHTML = catalog.odds.map((o) => {
      const t = tierInfo(o.tier);
      return `<div class="odd" style="${tc(o.tier)}"><i>${t.icon}</i><b>${pct(o.p)}</b><small>${esc(t.name)}</small></div>`;
    }).join("");
    $("pity").innerHTML = `Шанс Эпика и выше сейчас <b>${pct(S.user.epic_chance_now, 1)}</b> · гарантия не позже 60-го кейса`;
    document.querySelectorAll("#mult button").forEach((b) => b.classList.toggle("on", +b.dataset.count === count));
    $("spin-price").textContent = money(c.price * count);
    const freeLeft = S.user.energy + S.user.bonus_spins;
    $("free-btn").hidden = !(c.free && freeLeft >= count);
    $("free-count").textContent = freeLeft;
    const t = S.tasks.filter((x) => x.claimed).length;
    const ready = S.tasks.some((x) => !x.claimed && x.progress >= x.goal);
    $("tasks-status").textContent = ready ? "Награда ждёт" : `${t}/${S.tasks.length} выполнено`;
    $("tasks-card").classList.toggle("ready", ready);
    $("wheel-status").textContent = S.wheel_available ? "Доступно" : "Завтра снова";
    $("wheel-card").classList.toggle("ready", S.wheel_available);
    $("feed").innerHTML = S.feed.length ? S.feed.slice(0, 8).map((f) => `
      <div class="feed-row" style="${tc(f.tier)}"><img src="${esc(f.image)}" alt="" loading="lazy">
        <div><b>${itemTitle(f)}</b><small>${esc(f.owner_name)} · ${esc(tierInfo(f.tier).name)}${f.wear ? " · " + f.wear : ""}</small></div>
        <em>${money(f.price)}</em></div>`).join("")
      : '<p class="empty">Здесь появятся Эпики и всё, что выше, у всех игроков.</p>';
    tickTimer();
  }

  function tickTimer() {
    if (!S) return;
    const left = S.user.next_energy_in - Math.floor((Date.now() - stateAt) / 1000);
    const el = $("energy-timer");
    if (S.user.energy >= S.user.energy_max) el.textContent = "Бесплатные спины накоплены: 3 из 3";
    else if (left <= 0) { el.textContent = "Бесплатный спин готов"; }
    else {
      const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
      el.textContent = `Следующий бесплатный спин через ${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
  }
  setInterval(() => {
    tickTimer();
    if (S && S.user.energy < S.user.energy_max && S.user.next_energy_in - (Date.now() - stateAt) / 1000 < -1) refresh().catch(() => {});
  }, 1000);

  $("case-rail").addEventListener("click", (e) => {
    const b = e.target.closest("[data-case]");
    if (!b) return;
    caseId = b.dataset.case;
    haptic("tick");
    renderPlay();
  });
  $("mult").addEventListener("click", (e) => {
    const b = e.target.closest("[data-count]");
    if (!b) return;
    count = +b.dataset.count;
    renderPlay();
  });
  $("spin-btn").addEventListener("click", () => doSpin(false));
  $("free-btn").addEventListener("click", () => doSpin(true));
  $("case-contents-btn").addEventListener("click", openContents);
  $("wheel-card").addEventListener("click", openWheel);
  $("tasks-card").addEventListener("click", openTasks);

  function openContents() {
    const c = selectedCase();
    const blocks = CASE_TIERS.filter((t) => c.pools[t] && c.pools[t].length).map((t) => {
      const o = catalog.odds.find((x) => x.tier === t);
      const list = t === "secret"
        ? `<p class="lead">${c.pools[t].length} вариантов ножей и перчаток, например: ${c.pools[t].slice(0, 6).map((i) => esc(i.name)).join(", ")}…</p>`
        : `<div class="grid">${c.pools[t].map((i) => `<div class="tile" style="${tc(t)}"><img src="${esc(i.image)}" alt="" loading="lazy"><span class="n">${esc(i.name)}</span><span class="p">${money(i.min)}–${money(i.max)}</span></div>`).join("")}</div>`;
      return `<h3 style="${tc(t)};color:var(--tc);margin:6px 0 0;font-family:var(--f-display);font-size:20px">${tierInfo(t).icon} ${esc(tierInfo(t).name)} · ${pct(o.p)} · 1 из ${o.one_in}</h3>${list}`;
    }).join("");
    openSheet(`<h2>${esc(c.name)}</h2><p class="lead">Цена кейса ${money(c.price)}: средний дроп ${money(c.ev)} по ценам рынка, делённый на 0,9. Внутри ступени все предметы выпадают одинаково часто.</p>${blocks}`);
  }

  async function doSpin(free) {
    if (busy) return;
    busy = true;
    $("spin-btn").disabled = true;
    try {
      const c = selectedCase();
      const res = await window.Api.post("/api/spin", { case_id: c.id, count, free, idem_key: idem() });
      haptic("medium");
      await playReels(c, res.items);
      await refresh();
      showResults(res.items, c);
      if (S.user.break_reminder) maybeBreak();
    } catch (err) {
      handleError(err);
    } finally {
      busy = false;
      $("spin-btn").disabled = false;
    }
  }

  // Лента: соседние предметы случайны по тем же шансам кейса, остановка там, где решил сервер.
  function filler(c) {
    const r = Math.random();
    let acc = 0;
    for (const o of catalog.odds) {
      acc += o.p;
      if (r < acc && c.pools[o.tier] && c.pools[o.tier].length) {
        const pool = c.pools[o.tier];
        return pool[Math.floor(Math.random() * pool.length)];
      }
    }
    return c.pools.rare[0];
  }

  function playReels(c, drops) {
    return new Promise((resolve) => {
      const multi = drops.length > 1;
      const reels = $("reels");
      reels.className = "reels" + (multi ? " multi" : "");
      const STOP = 34, LEN = 40;
      reels.innerHTML = drops.map((d) => {
        const cells = [];
        for (let i = 0; i < LEN; i++) {
          const it = i === STOP ? d : filler(c);
          cells.push(`<div class="cell" style="${tc(it.tier)}"><img src="${esc(it.image)}" alt=""><span>${esc(it.name)}</span></div>`);
        }
        return `<div class="reel"><div class="strip">${cells.join("")}</div></div>`;
      }).join("");
      $("reel-overlay").hidden = false;
      const strips = [...reels.querySelectorAll(".strip")];
      const cellW = strips[0].firstElementChild.getBoundingClientRect().width;
      const reelW = reels.firstElementChild.getBoundingClientRect().width;
      const duration = settings.fast ? 600 : multi ? 3600 : 5600;
      const targets = strips.map(() => -(STOP * cellW + cellW / 2 - reelW / 2) + (Math.random() - 0.5) * cellW * 0.7);
      let done = false;
      let raf = 0;
      let lastCell = -1;
      const finish = () => {
        if (done) return;
        done = true;
        cancelAnimationFrame(raf);
        strips.forEach((s, i) => { s.style.transition = "none"; s.style.transform = `translateX(${targets[i]}px)`; });
        const best = drops.reduce((a, b) => (rank(b.tier) > rank(a.tier) ? b : a));
        haptic(rank(best.tier) >= rank("legendary") ? "success" : rank(best.tier) >= rank("epic") ? "heavy" : "light");
        setTimeout(() => { $("reel-overlay").hidden = true; resolve(); }, settings.fast ? 150 : 450);
      };
      $("reel-skip").onclick = finish;
      requestAnimationFrame(() => {
        strips.forEach((s, i) => {
          s.style.transition = `transform ${duration}ms cubic-bezier(.1,.66,.12,1)`;
          s.style.transform = `translateX(${targets[i]}px)`;
        });
        const first = strips[0];
        const watch = () => {
          if (done) return;
          const x = new DOMMatrixReadOnly(getComputedStyle(first).transform).m41;
          const cell = Math.floor((-x + reelW / 2) / cellW);
          if (cell !== lastCell) { lastCell = cell; haptic("tick"); }
          raf = requestAnimationFrame(watch);
        };
        raf = requestAnimationFrame(watch);
      });
      setTimeout(finish, duration + 50);
    });
  }

  function showResults(items, c) {
    if (items.length === 1) return showItem(items[0], { fresh: true, caseRef: c });
    const total = items.reduce((s, i) => s + i.price, 0);
    const rares = items.filter((i) => i.tier === "rare");
    const rareSum = rares.reduce((s, i) => s + i.sell_price, 0);
    const body = openSheet(`
      <h2>Открыто ${items.length} кейсов</h2>
      <p class="lead">Дроп на ${money(total)} при цене ${money(c.price * items.length)}</p>
      <div class="results">${items.map((i) => tileHtml(i)).join("")}</div>
      <div class="actions">
        <button type="button" id="r-keep">Забрать всё</button>
        <button type="button" id="r-sell" ${rares.length ? "" : "disabled"}>Продать синие · ${money(rareSum)}</button>
        <button type="button" class="primary" id="r-again" style="grid-column: span 2">Крутить ещё ×${items.length}</button>
      </div>`);
    body.querySelector(".results").addEventListener("click", (e) => {
      const t = e.target.closest("[data-item]");
      if (t) showItem(S.items.find((x) => x.id === +t.dataset.item) || items.find((x) => x.id === +t.dataset.item));
    });
    body.querySelector("#r-keep").onclick = closeSheet;
    body.querySelector("#r-sell").onclick = () => sellItems(rares.map((i) => i.id), false);
    body.querySelector("#r-again").onclick = () => { closeSheet(); doSpin(false); };
  }

  function tileHtml(it, extra = "") {
    return `<button type="button" class="tile ${extra}" data-item="${it.id}" style="${tc(it.tier)}">
      <span class="ico">${tierInfo(it.tier).icon}</span>
      <span class="badges">${it.locked ? "🔒" : ""}</span>
      <img src="${esc(it.image)}" alt="" loading="lazy">
      <span class="w">${wearLine(it) || "&nbsp;"}</span>
      <span class="n">${itemTitle(it)}</span>
      <span class="p">${money(it.price)}</span></button>`;
  }

  // ---------- карточка предмета ----------
  function showItem(it, opts = {}) {
    const t = tierInfo(it.tier);
    const growth = it.drop_price ? (it.price - it.drop_price) / it.drop_price : 0;
    const origin = it.origin.startsWith("case:")
      ? "Кейс " + ((catalog.cases.find((c) => c.id === it.origin.slice(5)) || {}).name || "")
      : { upgrade: "Апгрейдер", contract: "5 в 1", wheel: "Колесо" }[it.origin] || it.origin;
    const date = new Date(it.created_at * 1000).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
    const big = rank(it.tier) >= rank("epic");
    const body = openSheet(`
      <div class="tier-label" style="${tc(it.tier)}">${t.icon} ${esc(t.name)}<small>${esc(it.game_rarity)}${opts.fresh && it.chance ? ` · шанс ступени ${pct(it.chance, 2)}${it.chance < 0.1 ? ` (1 из ${it.one_in})` : ""}` : ""}</small></div>
      <div class="item-hero ${opts.fresh && big ? "flash" : ""}" style="${tc(it.tier)}"><img src="${esc(it.image)}" alt="${esc(it.name)}"></div>
      <div class="item-name"><b>${it.stattrak ? "StatTrak™ " : ""}${itemTitle(it)}</b><span>${it.wear ? WEAR[it.wear] : "Без износа"}</span></div>
      ${it.float != null ? `<div class="floatbar" aria-label="float ${it.float}"><i style="left:calc(${(it.float * 100).toFixed(2)}% - 1px)"></i></div>` : ""}
      <div class="stats">
        <div><small>Цена по рынку</small><b>${money(it.price)}</b></div>
        <div><small>С момента дропа</small><b class="${growth > 0 ? "up" : growth < 0 ? "down" : ""}">${growth >= 0 ? "+" : ""}${(growth * 100).toFixed(0)}%</b></div>
        <div><small>Float</small><b>${it.float != null ? it.float.toFixed(6) : "—"}</b></div>
        <div><small>Узор</small><b>${it.pattern}</b></div>
        <div><small>Откуда</small><b style="font-family:var(--f-body);font-size:13px">${esc(origin)}</b></div>
        <div><small>Получен</small><b>${date}</b></div>
      </div>
      <div class="actions">
        <button type="button" class="primary" id="i-sell" ${it.locked ? "disabled" : ""}>Продать · ${money(it.sell_price)}</button>
        <button type="button" id="i-share">Показать другу</button>
        <button type="button" id="i-up" ${it.locked || settings.noUpgrader ? "disabled" : ""}>Улучшить</button>
        <button type="button" id="i-ct" ${it.locked || !["rare", "epic", "legendary", "mythic"].includes(it.tier) ? "disabled" : ""}>5 в 1</button>
        <button type="button" id="i-lock">${it.locked ? "Снять замок" : "Замок 🔒"}</button>
        ${opts.caseRef ? '<button type="button" id="i-again">Крутить ещё</button>' : '<button type="button" id="i-close">Закрыть</button>'}
      </div>
      <p class="note">Быстрая продажа системе — 60% цены рынка. Замок защищает от продажи, апгрейда и 5 в 1.</p>`);
    body.querySelector("#i-sell").onclick = () => sellItems([it.id], true, it);
    body.querySelector("#i-share").onclick = () => share(it);
    body.querySelector("#i-up").onclick = () => { closeSheet(); ui.up.items = [it.id]; ui.up.target = null; switchTab("market", "upgrade"); loadTargets(); };
    body.querySelector("#i-ct").onclick = () => { closeSheet(); ui.ct.tier = it.tier; ui.ct.items = [it.id]; switchTab("market", "contract"); };
    body.querySelector("#i-lock").onclick = async () => {
      if (it.locked && !(await confirmSheet({ title: "Снять замок?", text: "Предмет снова можно будет продать или поставить в апгрейдер.", ok: "Снять" }))) return;
      try {
        await window.Api.post("/api/lock", { item_id: it.id, locked: !it.locked });
        await refresh();
        const fresh = S.items.find((x) => x.id === it.id);
        if (fresh) showItem(fresh);
      } catch (err) { handleError(err); }
    };
    const again = body.querySelector("#i-again");
    if (again) again.onclick = () => { closeSheet(); doSpin(false); };
    const close = body.querySelector("#i-close");
    if (close) close.onclick = closeSheet;
  }

  async function sellItems(ids, single, it) {
    const items = ids.map((id) => S.items.find((x) => x.id === id)).filter(Boolean);
    const total = items.reduce((s, i) => s + i.sell_price, 0);
    const top = items.reduce((m, i) => Math.max(m, rank(i.tier)), 0);
    if (single && it && rank(it.tier) >= rank("epic")) {
      const ok = await confirmSheet({
        title: "Продать?",
        text: `${itemTitle(it)} уйдёт системе за <b>${money(total)}</b>.`,
        ok: "Продать",
        pause: top >= rank("mythic") ? 3 : 0,
      });
      if (!ok) return;
    } else if (!single) {
      const ok = await confirmSheet({ title: `Продать ${items.length} шт.?`, text: `Получишь <b>${money(total)}</b>.`, ok: "Продать" });
      if (!ok) return;
    }
    try {
      const res = await window.Api.post("/api/sell", { item_ids: ids, idem_key: idem() });
      haptic("success");
      closeSheet();
      toast(`+${money(res.total)}`);
      await refresh();
    } catch (err) { handleError(err); }
  }

  function share(it) {
    const link = S.user.invite || "";
    const text = `Мне выпал ${it.stattrak ? "StatTrak™ " : ""}${it.name}${it.wear ? " (" + WEAR[it.wear] + ")" : ""} за ${money(it.price)} игр. в «Кейсах CS2»!`;
    const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(text)}`;
    const t = window.Api.tg();
    if (t && t.openTelegramLink && !window.SKINS_DEMO) t.openTelegramLink(url);
    else toast("В Telegram откроется выбор чата с карточкой дропа");
  }

  function maybeBreak() {
    const key = "break-" + new Date().toDateString();
    try { if (localStorage.getItem(key)) return; localStorage.setItem(key, "1"); } catch (e) { /* без хранилища */ }
    openSheet(`<h2>Перерыв?</h2><p>Сегодня ты открыл уже ${S.user.total_spins >= 50 ? "50+" : ""} кейсов. Бесплатные спины копятся, пока тебя нет.</p>
      <div class="actions"><button type="button" class="primary" onclick="document.getElementById('sheet-back').click()">Понятно</button></div>`);
  }

  // ---------- колесо и задания ----------
  function openWheel() {
    const sectors = catalog.wheel;
    const n = sectors.length, R = 120, cx = 130, cy = 130;
    const colors = ["var(--surface-2)", "var(--bg)"];
    const paths = sectors.map((s, i) => {
      const a0 = (i / n) * 2 * Math.PI - Math.PI / 2, a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2;
      const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0), x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
      const am = (a0 + a1) / 2, lx = cx + R * 0.62 * Math.cos(am), ly = cy + R * 0.62 * Math.sin(am);
      const fill = s.id === "c10" ? "var(--accent)" : colors[i % 2];
      return `<path d="M${cx},${cy} L${x0},${y0} A${R},${R} 0 0 1 ${x1},${y1} Z" fill="${fill}" stroke="var(--line)"/>
        <text x="${lx}" y="${ly}" fill="var(--fg)" font-size="13" font-weight="700" text-anchor="middle" dominant-baseline="middle" transform="rotate(${(am * 180) / Math.PI + 90} ${lx} ${ly})">${esc(s.label)}</text>`;
    }).join("");
    const unit = catalog.unit;
    const body = openSheet(`
      <h2>Колесо дня</h2>
      <p class="lead">Один бесплатный поворот в сутки, пустых секторов нет. Суммы считаются в ценах самого дешёвого кейса (${money(unit)}).</p>
      <div class="wheel-wrap"><span class="pin"></span><svg viewBox="0 0 260 260"><g id="wheel-g">${paths}</g><circle cx="130" cy="130" r="16" fill="var(--surface)" stroke="var(--line)"/></svg></div>
      <table class="odds-table"><tr><th>Приз</th><th>Шанс</th></tr>${sectors.map((s) => `<tr><td>${esc(s.label)}${s.cases ? ` · ${money(Math.round(unit * s.cases))}` : ""}</td><td>${pct(s.p, 0)}</td></tr>`).join("")}</table>
      <button type="button" class="btn-spin small" id="w-go" ${S.wheel_available ? "" : "disabled"}><span>${S.wheel_available ? "КРУТИТЬ КОЛЕСО" : "ЗАВТРА СНОВА"}</span></button>`);
    body.querySelector("#w-go").onclick = async (e) => {
      e.currentTarget.disabled = true;
      try {
        const res = await window.Api.post("/api/wheel");
        const idx = sectors.findIndex((s) => s.id === res.sector);
        const deg = 360 * 5 + (360 - (idx + 0.5) * (360 / n));
        const g = body.querySelector("#wheel-g");
        g.style.transition = `transform ${settings.fast ? 500 : 3500}ms cubic-bezier(.15,.7,.15,1)`;
        g.style.transform = `rotate(${deg}deg)`;
        setTimeout(async () => {
          haptic("success");
          toast(res.coins ? `+${money(res.coins)}` : `+${res.spins} бесплатный спин`);
          await refresh();
        }, settings.fast ? 550 : 3600);
      } catch (err) { handleError(err); }
    };
  }

  function openTasks() {
    const body = openSheet(`
      <h2>Задания на сегодня</h2>
      <p class="lead">Обновляются в полночь по Москве. Награды небольшие, чтобы задания не превращались в обязаловку.</p>
      ${S.tasks.map((t) => `<div class="task"><b>${esc(t.title)}</b><small>${t.progress}/${t.goal} · награда ${money(t.reward)}</small>
        <div class="bar"><i style="width:${(t.progress / t.goal) * 100}%"></i></div>
        <button type="button" data-task="${t.id}" ${t.claimed || t.progress < t.goal ? "disabled" : ""}>${t.claimed ? "Получено" : "Забрать"}</button></div>`).join("")}`);
    body.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-task]");
      if (!b) return;
      try {
        const res = await window.Api.post("/api/task", { task_id: b.dataset.task });
        haptic("success");
        toast(`+${money(res.coins)}`);
        await refresh();
        openTasks();
      } catch (err) { handleError(err); }
    });
  }

  // ---------- КОЛЛЕКЦИЯ ----------
  function renderCollection() {
    const items = S.items;
    $("col-slots").textContent = `${items.length} / ${S.user.slots}`;
    $("col-value").textContent = money(S.collection_value);
    const chips = [{ id: "all", label: "Все" }]
      .concat(CASE_TIERS.filter((t) => items.some((i) => i.tier === t)).map((t) => ({ id: t, label: tierInfo(t).name, icon: tierInfo(t).icon, tier: t })))
      .concat(items.some((i) => i.stattrak) ? [{ id: "st", label: "StatTrak™" }] : [])
      .concat(items.some((i) => i.locked) ? [{ id: "locked", label: "🔒 Под замком" }] : []);
    $("col-chips").innerHTML = chips.map((c) => `<button type="button" class="chip ${ui.colFilter === c.id ? "on" : ""}" data-f="${c.id}" ${c.tier ? `style="${tc(c.tier)}"` : ""}>${c.icon ? `<i>${c.icon}</i>` : ""}${esc(c.label)}</button>`).join("");
    let list = items.filter((i) =>
      ui.colFilter === "all" || i.tier === ui.colFilter || (ui.colFilter === "st" && i.stattrak) || (ui.colFilter === "locked" && i.locked));
    const q = ui.colSearch.trim().toLowerCase();
    if (q) list = list.filter((i) => i.name.toLowerCase().includes(q));
    const growth = (i) => (i.price - i.drop_price) / (i.drop_price || 1);
    const sorters = {
      new: (a, b) => b.id - a.id,
      price: (a, b) => b.price - a.price,
      rare: (a, b) => rank(b.tier) - rank(a.tier) || b.price - a.price,
      growth: (a, b) => growth(b) - growth(a),
    };
    list.sort(sorters[ui.colSort]);
    $("col-grid").innerHTML = list.map((i) => tileHtml(i)).join("");
    $("col-empty").hidden = items.length > 0;
    $("sell-dupes").hidden = dupes().length === 0;
  }

  // Дубли: одинаковый предмет и износ; остаётся лучший экземпляр (меньший float).
  function dupes() {
    const groups = {};
    S.items.forEach((i) => { (groups[i.catalog_id + (i.wear || "") + i.stattrak] ||= []).push(i); });
    const out = [];
    Object.values(groups).forEach((g) => {
      if (g.length < 2) return;
      g.sort((a, b) => b.price - a.price || (a.float || 0) - (b.float || 0));
      g.slice(1).forEach((i) => { if (!i.locked && rank(i.tier) < rank("legendary")) out.push(i); });
    });
    return out;
  }

  $("col-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]");
    if (!b) return;
    ui.colFilter = b.dataset.f;
    renderCollection();
  });
  $("col-search").addEventListener("input", (e) => { ui.colSearch = e.target.value; renderCollection(); });
  $("col-sort").addEventListener("change", (e) => { ui.colSort = e.target.value; renderCollection(); });
  $("col-grid").addEventListener("click", (e) => {
    const t = e.target.closest("[data-item]");
    if (t) showItem(S.items.find((x) => x.id === +t.dataset.item));
  });
  $("sell-dupes").addEventListener("click", () => sellItems(dupes().map((i) => i.id), false));

  // ---------- РЫНОК: апгрейдер ----------
  function renderMarket() {
    document.querySelector('#market-seg [data-sub="upgrade"]').hidden = settings.noUpgrader;
    if (settings.noUpgrader && ui.sub === "upgrade") ui.sub = "contract";
    document.querySelectorAll("#market-seg button").forEach((b) => b.classList.toggle("on", b.dataset.sub === ui.sub));
    ["upgrade", "contract", "lots"].forEach((s) => ($("sub-" + s).hidden = s !== ui.sub));
    renderUpgrade();
    renderContract();
  }

  $("market-seg").addEventListener("click", (e) => {
    const b = e.target.closest("[data-sub]");
    if (!b) return;
    ui.sub = b.dataset.sub;
    renderMarket();
  });

  function upStake() {
    ui.up.items = ui.up.items.filter((id) => S.items.some((x) => x.id === id && !x.locked));
    const items = ui.up.items.map((id) => S.items.find((x) => x.id === id));
    return { items, sum: items.reduce((s, i) => s + i.price, 0) + ui.up.coins };
  }
  function upChance(stake, target) {
    if (!target || target.price <= stake || stake <= 0) return 0;
    return Math.min(0.95, (stake / target.price) * 0.85 * (S.user.upgrade_bonus ? 1.03 : 1));
  }

  function renderUpgrade() {
    const { items, sum } = upStake();
    const box = $("up-stake-items");
    box.className = "up-items" + (items.length > 1 ? " many" : "");
    box.innerHTML = items.length
      ? items.map((i) => `<img src="${esc(i.image)}" alt="">`).join("") + (items.length === 1 ? `<span class="nm">${itemTitle(items[0])}</span>` : "") + (ui.up.coins ? `<span class="nm">+ ${money(ui.up.coins)}</span>` : "")
      : ui.up.coins ? `<span class="nm">${money(ui.up.coins)} монетами</span>` : '<span class="plus">+</span>';
    $("up-stake-sum").textContent = money(sum);
    const t = ui.up.target;
    $("up-target-items").innerHTML = t ? `<img src="${esc(t.image)}" alt=""><span class="nm">${t.stattrak ? "ST™ " : ""}${esc(t.name)}${t.wear ? " · " + t.wear : ""}</span>` : '<span class="plus">?</span>';
    $("up-target-sum").textContent = t ? money(t.price) : "—";
    const chance = upChance(sum, t);
    $("up-chance").textContent = pct(chance, 2);
    $("up-arc").setAttribute("stroke-dasharray", `${chance * 1000} 1000`);
    $("up-formula").innerHTML = t
      ? `<b>${money(sum)}</b> ÷ <b>${money(t.price)}</b> × 0,85${S.user.upgrade_bonus ? " × 1,03 (бонус после неудачи)" : ""} = <b>${pct(chance, 2)}</b>`
      : "Шанс = ставка ÷ цель × 0,85. Выбери ставку и цель.";
    $("up-mults").innerHTML = [1.5, 2, 3, 5, 10].map((m) => `<button type="button" data-m="${m}" class="${ui.up.mult === m ? "on" : ""}">×${String(m).replace(".", ",")}</button>`).join("");
    $("up-targets").innerHTML = sum > 0
      ? ui.up.targets.map((x, i) => `<button type="button" class="tile ${t && t.catalog_id === x.catalog_id && t.key === x.key ? "sel" : ""}" data-t="${i}" style="${tc(x.tier)}">
          <img src="${esc(x.image)}" alt="" loading="lazy"><span class="w">${x.stattrak ? '<span class="st">ST™</span> ' : ""}${x.wear || ""}</span><span class="n">${esc(x.name)}</span><span class="p">${money(x.price)}</span></button>`).join("")
      : "";
    $("up-go").disabled = !(chance > 0) || ui.up.spinning || S.user.upgrades_left <= 0;
    $("up-note").textContent = `Осталось сегодня: ${S.user.upgrades_left} из 10. При неудаче ставка сгорает, назад приходят запчасти на 10% ставки и +3% к шансу следующего апгрейда.`;
  }

  async function loadTargets() {
    const { sum } = upStake();
    if (sum <= 0) { ui.up.targets = []; renderUpgrade(); return; }
    try {
      const res = await window.Api.get(`/api/upgrade/targets?stake=${sum}&mult=${ui.up.mult}`);
      ui.up.targets = res.targets;
      if (!ui.up.target || ui.up.target.price <= sum) ui.up.target = res.targets[0] || null;
    } catch (err) { handleError(err); }
    renderUpgrade();
  }

  $("up-mults").addEventListener("click", (e) => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    ui.up.mult = +b.dataset.m;
    ui.up.target = null;
    loadTargets();
  });
  $("up-targets").addEventListener("click", (e) => {
    const b = e.target.closest("[data-t]");
    if (!b) return;
    ui.up.target = ui.up.targets[+b.dataset.t];
    haptic("tick");
    renderUpgrade();
  });
  $("up-target").addEventListener("click", () => { if (!ui.up.targets.length) loadTargets(); });
  $("up-stake").addEventListener("click", openStakePicker);

  function openStakePicker() {
    const free = S.items.filter((i) => !i.locked).sort((a, b) => b.price - a.price);
    const sel = new Set(ui.up.items);
    const body = openSheet(`
      <h2>Ставка</h2>
      <p class="lead">До 3 предметов и монеты. За раз — не больше половины коллекции и баланса.</p>
      <label for="up-coins" class="lead">Добавить монет (баланс ${money(S.user.balance)})</label>
      <input class="field" id="up-coins" type="number" min="0" step="0.01" inputmode="decimal" value="${ui.up.coins ? (ui.up.coins / 100).toFixed(2) : ""}" placeholder="0.00">
      ${free.length ? `<div class="grid" id="pick">${free.map((i) => tileHtml(i, sel.has(i.id) ? "sel" : "")).join("")}</div>` : '<p class="empty">Предметов пока нет: открой кейс или поставь монеты.</p>'}
      <button type="button" class="btn-spin small" id="pick-ok"><span>ГОТОВО</span></button>`);
    const grid = body.querySelector("#pick");
    if (grid) grid.addEventListener("click", (e) => {
      const t = e.target.closest("[data-item]");
      if (!t) return;
      const id = +t.dataset.item;
      if (sel.has(id)) sel.delete(id);
      else if (sel.size < 3) sel.add(id);
      else return toast("Не больше 3 предметов");
      t.classList.toggle("sel", sel.has(id));
    });
    body.querySelector("#pick-ok").onclick = () => {
      const coins = Math.round(parseFloat(body.querySelector("#up-coins").value || "0") * 100) || 0;
      ui.up.coins = Math.max(0, Math.min(coins, S.user.balance));
      ui.up.items = [...sel];
      ui.up.target = null;
      closeSheet();
      loadTargets();
    };
  }

  $("up-go").addEventListener("click", async () => {
    const { items, sum } = upStake();
    const t = ui.up.target;
    if (!t) return;
    if (items.some((i) => rank(i.tier) >= rank("epic"))) {
      const ok = await confirmSheet({ title: "Точно?", text: `Ставка ${money(sum)} сгорит с шансом ${pct(1 - upChance(sum, t), 1)}.`, ok: "Улучшить", pause: 3 });
      if (!ok) return;
    }
    ui.up.spinning = true;
    renderUpgrade();
    try {
      const res = await window.Api.post("/api/upgrade", {
        item_ids: ui.up.items, coins: ui.up.coins, target_id: t.catalog_id, target_key: t.key, idem_key: idem(),
      });
      await spinNeedle(res.roll);
      haptic(res.success ? "success" : "error");
      ui.up.items = [];
      ui.up.coins = 0;
      ui.up.target = null;
      ui.up.targets = [];
      await refresh();
      if (res.success) showItem(res.item, { fresh: true });
      else openSheet(`<h2>Не получилось</h2><p>Шанс был ${pct(res.chance, 2)}, стрелка остановилась на ${pct(res.roll, 1)}.</p>
        <p>Запчасти: <b>+${money(res.scrap)}</b>. Следующий апгрейд получит +3% к шансу.</p>
        <div class="actions"><button type="button" class="primary" id="up-ok" style="grid-column:span 2">Понятно</button></div>`).querySelector("#up-ok").onclick = closeSheet;
    } catch (err) { handleError(err); }
    ui.up.spinning = false;
    renderUpgrade();
  });

  function spinNeedle(roll) {
    return new Promise((resolve) => {
      const n = $("up-needle");
      const deg = 360 * 4 + roll * 360;
      n.style.transition = "none";
      n.style.transform = "rotate(0deg)";
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const ms = settings.fast ? 500 : 3600;
        n.style.transition = `transform ${ms}ms cubic-bezier(.15,.7,.15,1)`;
        n.style.transform = `rotate(${deg}deg)`;
        setTimeout(resolve, ms + 200);
      }));
    });
  }

  // ---------- РЫНОК: 5 в 1 ----------
  function renderContract() {
    const tiers = ["rare", "epic", "legendary", "mythic"];
    const next = { rare: "epic", epic: "legendary", legendary: "mythic", mythic: "secret" };
    const chance = { rare: 1, epic: 0.7, legendary: 0.7, mythic: 0.7 };
    $("ct-chips").innerHTML = tiers.map((t) => {
      const n = S.items.filter((i) => i.tier === t && !i.locked).length;
      return `<button type="button" class="chip ${ui.ct.tier === t ? "on" : ""}" data-ct="${t}" style="${tc(t)}"><i>${tierInfo(t).icon}</i>${esc(tierInfo(t).name)} · ${n}</button>`;
    }).join("");
    const pool = S.items.filter((i) => i.tier === ui.ct.tier && !i.locked);
    ui.ct.items = ui.ct.items.filter((id) => pool.some((i) => i.id === id));
    const chosen = ui.ct.items.map((id) => pool.find((i) => i.id === id));
    $("ct-slots").innerHTML = Array.from({ length: 5 }, (_, k) => chosen[k]
      ? `<div class="ct-slot" style="border:1px solid var(--t-${ui.ct.tier})"><img src="${esc(chosen[k].image)}" alt=""></div>`
      : '<div class="ct-slot"><span class="plus" style="width:28px;height:28px;font-size:16px">+</span></div>').join("");
    const sum = chosen.reduce((s, i) => s + i.price, 0);
    const nt = tierInfo(next[ui.ct.tier]);
    $("ct-info").innerHTML = `${esc(tierInfo(ui.ct.tier).name)} ×5 → <b style="color:var(--t-${next[ui.ct.tier]})">${nt.icon} ${esc(nt.name)}</b> · успех <b>${pct(chance[ui.ct.tier], 0)}</b>${chance[ui.ct.tier] < 1 ? ", иначе предмет той же ступени" : ""} · вклад ${money(sum)}`;
    $("ct-grid").innerHTML = pool.length ? pool.map((i) => tileHtml(i, ui.ct.items.includes(i.id) ? "sel" : "")).join("") : "";
    $("ct-go").disabled = chosen.length !== 5;
  }

  $("ct-chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ct]");
    if (!b) return;
    ui.ct.tier = b.dataset.ct;
    ui.ct.items = [];
    renderContract();
  });
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
    const items = ui.ct.items.map((id) => S.items.find((x) => x.id === id));
    if (items.some((i) => rank(i.tier) >= rank("epic"))) {
      const ok = await confirmSheet({ title: "Собрать 5 в 1?", text: "Пять предметов исчезнут, взамен придёт один.", ok: "Собрать", pause: 3 });
      if (!ok) return;
    }
    try {
      const res = await window.Api.post("/api/contract", { item_ids: ui.ct.items, idem_key: idem() });
      haptic(res.success ? "success" : "warning");
      ui.ct.items = [];
      await refresh();
      showItem(res.item, { fresh: true });
      if (!res.success) toast("Ступень не поднялась, но предмет твой");
    } catch (err) { handleError(err); }
  });

  // ---------- ДРУЗЬЯ ----------
  function renderFriends() {
    $("ref-steps").innerHTML = [[1, "3 спина"], [5, "$ и рамка"], [10, "$ и эффект"], [25, "Амбассадор"]]
      .map(([n, r]) => `<div><b>${n}</b><small>${r}</small></div>`).join("");
  }
  $("invite-btn").addEventListener("click", () => {
    const link = S.user.invite || "";
    const url = `https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent("Открывай со мной настоящие кейсы CS2 в Telegram. Тебе 3 бесплатных спина!")}`;
    const t = window.Api.tg();
    if (t && t.openTelegramLink && !window.SKINS_DEMO) t.openTelegramLink(url);
    else toast("В Telegram откроется выбор чата со ссылкой-приглашением");
  });

  // ---------- ЕЩЁ ----------
  function renderMore() {
    $("profile").innerHTML = `
      <div><small>Кейсов открыто</small><b>${S.user.total_spins}</b></div>
      <div><small>Предметов</small><b>${S.items.length}</b></div>
      <div><small>Коллекция</small><b>${money(S.collection_value)}</b></div>`;
  }
  document.querySelector(".menu-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    ({ fair: openFair, odds: openOdds, settings: openSettings, terms: openTerms })[b.dataset.open]();
  });

  function openFair(revealed) {
    const f = S.user.fair;
    const body = openSheet(`
      <h2>Честная игра</h2>
      <p class="lead">Перед спином сервер показывает хэш своего ключа. Результат = HMAC-SHA256(ключ сервера, «твой ключ:номер спина:шаг»). После смены пары сервер раскрывает старый ключ, и любой спин можно пересчитать самому.</p>
      <div class="stats" style="grid-template-columns:1fr">
        <div><small>Хэш ключа сервера (SHA-256)</small><b class="mono">${esc(f.server_seed_hash)}</b></div>
        <div><small>Номер следующего спина (nonce)</small><b>${f.nonce}</b></div>
      </div>
      <label class="lead" for="fair-seed">Твой ключ</label>
      <input class="field" id="fair-seed" value="${esc(f.client_seed)}" maxlength="64">
      ${revealed ? `<div class="stats" style="grid-template-columns:1fr"><div><small>Раскрытый ключ сервера (спины 0–${revealed.last_nonce - 1})</small><b class="mono">${esc(revealed.server_seed)}</b></div></div>` : ""}
      <button type="button" class="btn-spin small" id="fair-go"><span>СМЕНИТЬ ПАРУ</span></button>`);
    body.querySelector("#fair-go").onclick = async () => {
      try {
        const res = await window.Api.post("/api/fair/rotate", { client_seed: body.querySelector("#fair-seed").value });
        await refresh();
        openFair(res.revealed);
      } catch (err) { handleError(err); }
    };
  }

  function openOdds() {
    const wears = [["FN", "0,00–0,07", 3], ["MW", "0,07–0,15", 24], ["FT", "0,15–0,38", 33], ["WW", "0,38–0,45", 24], ["BS", "0,45–1,00", 16]];
    openSheet(`
      <h2>Шансы</h2>
      <p class="lead">Одинаковы для всех кейсов и совпадают с раскрытыми Valve.</p>
      <table class="odds-table"><tr><th>Ступень</th><th>В CS2</th><th>Шанс</th></tr>
      ${catalog.odds.map((o) => `<tr style="${tc(o.tier)}"><td><span style="color:var(--tc)">${tierInfo(o.tier).icon}</span> ${esc(tierInfo(o.tier).name)}</td><td>${{ rare: "Mil-Spec", epic: "Restricted", legendary: "Classified", mythic: "Covert", secret: "★ Нож/перчатки" }[o.tier]}</td><td>${pct(o.p, 2)} · 1 из ${o.one_in}</td></tr>`).join("")}</table>
      <table class="odds-table"><tr><th>Износ</th><th>Float</th><th>Шанс</th></tr>
      ${wears.map((w) => `<tr><td>${WEAR[w[0]]}</td><td>${w[1]}</td><td>${w[2]}%</td></tr>`).join("")}</table>
      <p class="lead">StatTrak™ — 10%, если у скина он бывает. Гарантия: Эпик и выше не позже 60-го кейса подряд, Легендарный и выше не позже 300-го; шанс начинает расти заранее, с 51-го и 281-го кейса.</p>`);
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
      renderMarket();
    });
  }

  function openTerms() {
    openSheet(`<h2>Условия</h2>
      <p>Игровые доллары ($ игр.) есть только внутри бота. Их нельзя купить за деньги, вывести или обменять на рубли, Stars или настоящие скины.</p>
      <p>Предметы — игровые копии скинов CS2 с ценами рынка Skinport. Вывода в Steam нет.</p>
      <p>Шансы открыты на экране «Шансы», каждый спин проверяется на экране «Честная игра».</p>
      <p>Уведомлений не больше 2 в день, ночью с 21:00 до 8:00 их нет.</p>`);
  }

  // ---------- навигация ----------
  function switchTab(tab, sub) {
    ui.tab = tab;
    if (sub) ui.sub = sub;
    document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.dataset.tab !== tab));
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    $("screens").scrollTop = 0;
    if (tab === "market") { renderMarket(); if (ui.sub === "upgrade" && !ui.up.targets.length) loadTargets(); }
  }
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    haptic("tick");
    switchTab(b.dataset.tab);
  });
  $("balance").addEventListener("click", () => openSheet(`
    <h2>${money(S.user.balance)} игр.</h2>
    <p class="lead">Игровые доллары: 1 игровой $ = 1 $ рыночной цены скина. За настоящие деньги не продаются и не выводятся.</p>
    <div class="stats"><div><small>Как получить</small><b style="font-family:var(--f-body);font-size:13px">продажа предметов, колесо дня, задания</b></div>
    <div><small>Куда потратить</small><b style="font-family:var(--f-body);font-size:13px">кейсы, апгрейдер</b></div></div>`));
  $("energy").addEventListener("click", () => toast(`Бесплатный спин раз в 3 часа, копится до 3. Работает на кейсе ${catalog.cases.find((c) => c.free).name}.`));

  // ---------- старт ----------
  async function boot() {
    await window.Api.ready();
    const t = window.Api.tg();
    if (t) {
      try { t.ready(); t.expand(); t.setHeaderColor("#121317"); t.setBackgroundColor("#121317"); } catch (e) { /* вне Telegram */ }
    }
    document.body.classList.toggle("no-fx", settings.nofx);
    try {
      catalog = await window.Api.get("/api/catalog");
      caseId = (catalog.cases.find((c) => c.free) || catalog.cases[0]).id;
      await refresh();
    } catch (err) {
      document.getElementById("screens").innerHTML = `<p class="empty">${esc(err.message)}<br><br><button class="btn-ghost" onclick="location.reload()">Повторить</button></p>`;
    }
  }
  boot();
})();
