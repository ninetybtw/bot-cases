// Демо-сервер для превью: та же логика, что в skinsbot/service.py, но в браузере.
// Данные живут в localStorage этого браузера. Каталог приходит из window.DEMO_CATALOG.
(function () {
  "use strict";
  const C = window.DEMO_CATALOG;
  const TIERS = {
    common: { name: "Обычный", icon: "○" }, uncommon: { name: "Необычный", icon: "◇" },
    rare: { name: "Редкий", icon: "◆" }, epic: { name: "Эпик", icon: "⬢" }, legendary: { name: "Легендарный", icon: "★" },
    mythic: { name: "Мифический", icon: "✹" }, secret: { name: "Секретный", icon: "♛" },
    divine: { name: "Божественный", icon: "✺" }, exclusive: { name: "Эксклюзив", icon: "◈" },
  };
  const ORDER = Object.keys(TIERS);
  const ODDS = { rare: 0.7992, epic: 0.1598, legendary: 0.032, mythic: 0.0064, secret: 0.0026 };
  const WEAR_RANGES = { FN: [0, 0.07], MW: [0.07, 0.15], FT: [0.15, 0.38], WW: [0.38, 0.45], BS: [0.45, 1] };
  const WEAR_W = { FN: 0.03, MW: 0.24, FT: 0.33, WW: 0.24, BS: 0.16 };
  const WHEEL = [
    { id: "c05", label: "0,5 кейса", cases: 0.5, p: 0.55 }, { id: "c1", label: "1 кейс", cases: 1, p: 0.25 },
    { id: "c25", label: "2,5 кейса", cases: 2.5, p: 0.12 }, { id: "spin", label: "1 спин", spins: 1, p: 0.06 },
    { id: "c10", label: "10 кейсов", cases: 10, p: 0.02 },
  ];
  const TASKS = [
    { id: "spin3", title: "Открой 3 кейса", goal: 3, reward_cases: 0.4, field: "spins" },
    { id: "sell1", title: "Продай 1 предмет", goal: 1, reward_cases: 0.3, field: "sells" },
    { id: "upgrade1", title: "Попробуй апгрейд", goal: 1, reward_cases: 0.6, field: "upgrades" },
  ];
  const NEXT = { rare: "epic", epic: "legendary", legendary: "mythic", mythic: "secret" };
  const CT_OK = { rare: 1, epic: 0.7, legendary: 0.7, mythic: 0.7 };
  const ENERGY_PERIOD = 3 * 3600, ENERGY_MAX = 3;

  const cases = Object.fromEntries(C.cases.map((c) => [c.id, c]));
  const cheapest = C.cases.reduce((a, b) => (b.price < a.price ? b : a));
  const UNIT = Math.round(cheapest.price * 100);
  const targets = [];
  Object.values(C.items).forEach((it) => Object.entries(it.prices).forEach(([k, p]) => targets.push([Math.round(p * 100), it.id, k])));
  targets.sort((a, b) => a[0] - b[0]);

  const now = () => Date.now() / 1000;
  const day = () => new Date().toDateString();
  const fail = (msg, code, extra) => { throw Object.assign(new Error(msg), { code: code || "error", data: extra || {} }); };
  const rnd = () => Math.random();
  const pick = (a) => a[Math.floor(rnd() * a.length)];
  function weighted(w) {
    const entries = Object.entries(w).filter(([, v]) => v > 0);
    let r = rnd() * entries.reduce((s, [, v]) => s + v, 0);
    for (const [k, v] of entries) { if ((r -= v) < 0) return k; }
    return entries[entries.length - 1][0];
  }
  const priceKey = (wear, st) => (st ? "ST_" : "") + (wear || "-");
  const priceOf = (it, wear, st) => Math.round(it.prices[priceKey(wear, st)] * 100);
  const hex = (n) => Array.from(crypto.getRandomValues(new Uint8Array(n)), (b) => b.toString(16).padStart(2, "0")).join("");
  async function sha256(s) {
    try {
      const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
      return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) { return "—"; }
  }

  // ---------- хранилище ----------
  let db = load();
  function load() {
    try { const s = JSON.parse(localStorage.getItem("skins-demo-v1")); if (s && s.user) return s; } catch (e) { /* пусто */ }
    return null;
  }
  function save() { try { localStorage.setItem("skins-demo-v1", JSON.stringify(db)); } catch (e) { /* без хранилища */ } }
  function daily() {
    if (!db.daily || db.daily.day !== day()) db.daily = { day: day(), wheel: false, spins: 0, sells: 0, upgrades: 0, claimed: [] };
    return db.daily;
  }

  function fresh() {
    const t = now();
    db = {
      user: { id: 1, name: "Ты", balance: UNIT * 5, energy: 2, energy_ts: t - 3600 * 1.6, bonus_spins: 0, since_epic: 0, since_legendary: 0,
        upgrade_bonus: false, total_spins: 0, server_seed: hex(32), client_seed: hex(8), nonce: 0 },
      items: [], nextId: 1, daily: null, feed: [],
    };
    // Стартовая коллекция, чтобы превью открывалось в рабочем состоянии.
    const seedCase = cases[cheapest.id];
    for (let i = 0; i < 9; i++) addItem(rollDrop(seedCase, ODDS), "case:" + seedCase.id, t - (9 - i) * 900);
    addItem(makeDrop(C.items[pick(seedCase.pools.epic)]), "case:" + seedCase.id, t - 300);
    db.user.total_spins = 10;
    // Лента чужих дропов (вымышленные игроки демо).
    const names = ["Игрок 4821", "Игрок 1177", "Игрок 9034", "Игрок 2560", "Игрок 7312", "Игрок 6408"];
    const all = C.cases;
    ["legendary", "epic", "mythic", "epic", "secret", "legendary"].forEach((tier, i) => {
      const c = all[i % all.length];
      const d = makeDrop(C.items[pick(c.pools[tier])]);
      db.feed.push({ ...view({ ...d, id: -1 - i, origin: "case:" + c.id, created_at: t - i * 420, drop_price: d.price }), owner_name: names[i] });
    });
    save();
  }

  function energy() {
    const u = db.user;
    let e = u.energy, ts = u.energy_ts;
    const n = now();
    if (e >= ENERGY_MAX) return [e, n];
    const g = Math.floor((n - ts) / ENERGY_PERIOD);
    if (g) { e = Math.min(ENERGY_MAX, e + g); ts = e >= ENERGY_MAX ? n : ts + g * ENERGY_PERIOD; }
    return [e, ts];
  }

  // ---------- шансы и дроп ----------
  function spinOdds(sinceEpic, sinceLeg) {
    const o = { ...ODDS };
    [["legendary", sinceLeg, ["legendary", "mythic", "secret"], 281, 300], ["epic", sinceEpic, ["epic", "legendary", "mythic", "secret"], 51, 60]]
      .forEach(([, counter, group, soft, hard]) => {
        const n = counter + 1;
        if (n < soft) return;
        const cur = group.reduce((s, t) => s + o[t], 0);
        const target = n >= hard ? 1 : Math.min(1, cur + 0.05 * (n - soft + 1));
        const extra = target - cur;
        if (extra <= 0) return;
        const below = Object.keys(o).filter((t) => !group.includes(t));
        const tot = below.reduce((s, t) => s + o[t], 0);
        below.forEach((t) => (o[t] *= 1 - extra / tot));
        o[group[0]] += extra;
      });
    return o;
  }
  function wearDist(it) {
    if (!it.wears || !it.wears.length) return null;
    const tot = it.wears.reduce((s, w) => s + WEAR_W[w], 0);
    return Object.fromEntries(it.wears.map((w) => [w, WEAR_W[w] / tot]));
  }
  function makeDrop(it, st, fv) {
    let wear = null;
    if (fv == null && it.wears && it.wears.length) {
      wear = weighted(wearDist(it));
      let [lo, hi] = WEAR_RANGES[wear];
      lo = Math.max(lo, it.min_float); hi = Math.min(hi, it.max_float);
      fv = +(lo + (hi - lo) * rnd()).toFixed(6);
    } else if (fv != null) {
      wear = Object.keys(WEAR_RANGES).find((w) => fv >= WEAR_RANGES[w][0] && fv < WEAR_RANGES[w][1] && it.wears.includes(w)) || it.wears[it.wears.length - 1];
    }
    if (st == null) st = !!it.stattrak && rnd() < 0.1;
    return { catalog_id: it.id, tier: it.tier, wear, float_value: fv == null ? null : fv, pattern: Math.floor(rnd() * 1000), stattrak: st, price: priceOf(it, wear, st) };
  }
  function rollDrop(c, odds) {
    const tier = weighted(Object.fromEntries(Object.entries(odds).filter(([t]) => c.pools[t] && c.pools[t].length)));
    return makeDrop(C.items[pick(c.pools[tier])]);
  }
  function addItem(d, origin, ts) {
    const row = { id: db.nextId++, ...d, origin, drop_price: d.price, created_at: ts || now(), locked: false };
    db.items.push(row);
    return row;
  }
  function view(r) {
    const it = C.items[r.catalog_id];
    const price = priceOf(it, r.wear, r.stattrak);
    return {
      id: r.id, catalog_id: r.catalog_id, name: it.name, weapon: it.weapon, finish: it.finish, tier: r.tier,
      game_rarity: it.game_rarity, image: it.image, wear: r.wear, float: r.float_value, pattern: r.pattern,
      stattrak: !!r.stattrak, price, sell_price: Math.round(price * 0.6), drop_price: r.drop_price,
      origin: r.origin, created_at: r.created_at, locked: !!r.locked,
    };
  }
  const owned = (ids, allowLocked) => {
    if (!ids.length || new Set(ids).size !== ids.length) fail("Выбери предметы.", "bad_items");
    const rows = ids.map((id) => db.items.find((x) => x.id === id));
    if (rows.some((r) => !r)) fail("Предмет не найден в коллекции.", "bad_items");
    if (!allowLocked && rows.some((r) => r.locked)) fail("Предмет под замком. Сначала сними замок.", "locked");
    return rows;
  };
  const remove = (rows) => { const ids = new Set(rows.map((r) => r.id)); db.items = db.items.filter((x) => !ids.has(x.id)); };
  const brief = (id) => {
    const it = C.items[id];
    const ps = Object.values(it.prices);
    return { id, name: it.name, image: it.image, tier: it.tier, min: Math.round(Math.min(...ps) * 100), max: Math.round(Math.max(...ps) * 100) };
  };

  // ---------- маршруты ----------
  const routes = {
    "GET /api/catalog": () => ({
      cases: C.cases.map((c) => ({
        id: c.id, name: c.name, image: c.image, price: Math.round(c.price * 100), ev: Math.round(c.ev * 100), free: c.id === cheapest.id,
        pools: Object.fromEntries(Object.entries(c.pools).map(([t, ids]) => [t, ids.map(brief)])),
      })),
      odds: Object.entries(ODDS).map(([tier, p]) => ({ tier, p, one_in: Math.max(1, Math.round(1 / p)) })),
      tiers: TIERS, wheel: WHEEL, unit: UNIT, generated_at: C.generated_at,
    }),

    "GET /api/state": async () => {
      const u = db.user, d = daily();
      const [e, ts] = energy();
      const odds = spinOdds(u.since_epic, u.since_legendary);
      const items = db.items.slice().sort((a, b) => b.id - a.id).map(view);
      return {
        user: {
          id: u.id, name: u.name, balance: u.balance, energy: e, energy_max: ENERGY_MAX,
          next_energy_in: e >= ENERGY_MAX ? 0 : Math.round(ts + ENERGY_PERIOD - now()),
          bonus_spins: u.bonus_spins, total_spins: u.total_spins, slots: 200, upgrade_bonus: u.upgrade_bonus,
          upgrades_left: 10 - d.upgrades, epic_chance_now: odds.epic + odds.legendary + odds.mythic + odds.secret,
          break_reminder: d.spins >= 50, invite: "https://t.me/cs2_cases_bot?start=ref_1",
          fair: { server_seed_hash: await sha256(u.server_seed), client_seed: u.client_seed, nonce: u.nonce },
        },
        items, collection_value: items.reduce((s, i) => s + i.price, 0), wheel_available: !d.wheel,
        tasks: TASKS.map((t) => ({ id: t.id, title: t.title, goal: t.goal, progress: Math.min(d[t.field], t.goal),
          reward: Math.round(UNIT * t.reward_cases), claimed: d.claimed.includes(t.id) })),
        feed: db.feed.slice(0, 12),
      };
    },

    "POST /api/spin": (b) => {
      const c = cases[b.case_id], count = +b.count || 1, u = db.user;
      if (!c) fail("Такого кейса нет.", "bad_case");
      if (![1, 5, 10].includes(count)) fail("Можно крутить ×1, ×5 или ×10.", "bad_count");
      if (db.items.length + count > 200) fail("Коллекция заполнена. Продай лишнее, чтобы крутить дальше.", "full");
      let [e, ts] = energy();
      if (b.free) {
        if (c.id !== cheapest.id) fail("Бесплатные спины работают на самом дешёвом кейсе.", "bad_case");
        if (e + u.bonus_spins < count) fail("Бесплатные спины закончились.", "no_spins");
        const use = Math.min(e, count);
        if (e >= ENERGY_MAX && use) ts = now();
        e -= use;
        u.bonus_spins -= count - use;
      } else {
        const cost = Math.round(c.price * 100) * count;
        if (u.balance < cost) fail("Не хватает $" + ((cost - u.balance) / 100).toFixed(2), "no_money");
        u.balance -= cost;
      }
      u.energy = e; u.energy_ts = ts;
      const out = [];
      for (let i = 0; i < count; i++) {
        const odds = spinOdds(u.since_epic, u.since_legendary);
        const d = rollDrop(c, odds);
        const r = ORDER.indexOf(d.tier);
        u.since_epic = r >= ORDER.indexOf("epic") ? 0 : u.since_epic + 1;
        u.since_legendary = r >= ORDER.indexOf("legendary") ? 0 : u.since_legendary + 1;
        const row = addItem(d, "case:" + c.id);
        out.push({ ...view(row), chance: odds[d.tier], one_in: Math.max(1, Math.round(1 / odds[d.tier])) });
        if (r >= ORDER.indexOf("legendary") || r === ORDER.indexOf("epic")) db.feed.unshift({ ...view(row), owner_name: u.name });
      }
      db.feed = db.feed.slice(0, 20);
      u.total_spins += count;
      u.nonce += 1;
      daily().spins += count;
      save();
      return { items: out, nonce: u.nonce - 1, case_id: c.id };
    },

    "POST /api/sell": (b) => {
      const rows = owned((b.item_ids || []).map(Number));
      if (rows.length > 1 && rows.some((r) => ORDER.indexOf(r.tier) >= ORDER.indexOf("legendary"))) fail("Легендарные и выше продаются только по одному.", "single_only");
      const total = rows.reduce((s, r) => s + view(r).sell_price, 0);
      remove(rows);
      db.user.balance += total;
      daily().sells += rows.length;
      save();
      return { sold: b.item_ids, total, balance: db.user.balance };
    },

    "POST /api/lock": (b) => {
      const [r] = owned([+b.item_id], true);
      r.locked = !!b.locked;
      save();
      return { id: r.id, locked: r.locked };
    },

    "GET /api/upgrade/targets": (b, q) => {
      const stake = +q.get("stake"), mult = q.get("mult") ? +q.get("mult") : null;
      const lo = stake * (mult || 1.2), hi = lo * (mult ? 1.25 : 50);
      let out = targets.filter(([p]) => p >= Math.floor(lo) && p <= hi).map(([price, id, key]) => {
        const it = C.items[id];
        const st = key.startsWith("ST_"), wear = key.replace("ST_", "");
        return { catalog_id: id, key, name: it.name, image: it.image, tier: it.tier, wear: wear === "-" ? null : wear, stattrak: st, price };
      });
      if (out.length > 30) { const step = out.length / 30; out = Array.from({ length: 30 }, (_, i) => out[Math.floor(i * step)]); }
      return { targets: out };
    },

    "POST /api/upgrade": (b) => {
      const u = db.user, d = daily();
      if (d.upgrades >= 10) fail("На сегодня апгрейды закончились. Возвращайся завтра.", "limit");
      const ids = (b.item_ids || []).map(Number);
      if (ids.length > 3) fail("В апгрейдер можно положить до 3 предметов.", "too_many");
      const rows = ids.length ? owned(ids) : [];
      const coins = +b.coins || 0;
      if (!rows.length && !coins) fail("Положи предмет или монеты.", "empty");
      if (u.balance < coins) fail("Не хватает монет.", "no_money");
      const it = C.items[b.target_id];
      if (!it || !(b.target_key in it.prices)) fail("Цель не найдена.", "bad_target");
      const stake = rows.reduce((s, r) => s + view(r).price, 0) + coins;
      const all = db.items.reduce((s, r) => s + view(r).price, 0) + u.balance;
      if (stake > all / 2) fail("За раз можно поставить не больше половины коллекции и баланса.", "too_big");
      const st = b.target_key.startsWith("ST_"), wearK = b.target_key.replace("ST_", ""), wear = wearK === "-" ? null : wearK;
      const tp = priceOf(it, wear, st);
      if (tp <= stake) fail("Цель должна стоить дороже ставки.", "bad_target");
      const chance = Math.min(0.95, (stake / tp) * 0.85 * (u.upgrade_bonus ? 1.03 : 1));
      const roll = rnd();
      const success = roll < chance;
      remove(rows);
      u.balance -= coins;
      const res = { success, chance, roll, stake, target_price: tp };
      if (success) {
        let fv = null;
        if (wear) { const lo = Math.max(WEAR_RANGES[wear][0], it.min_float), hi = Math.min(WEAR_RANGES[wear][1], it.max_float); fv = +(lo + (hi - lo) * rnd()).toFixed(6); }
        const row = addItem({ catalog_id: it.id, tier: it.tier, wear, float_value: fv, pattern: Math.floor(rnd() * 1000), stattrak: st, price: tp }, "upgrade");
        res.item = view(row);
        u.upgrade_bonus = false;
      } else {
        res.scrap = Math.round(stake * 0.1);
        u.balance += res.scrap;
        u.upgrade_bonus = true;
      }
      d.upgrades += 1;
      u.nonce += 1;
      res.balance = u.balance;
      save();
      return res;
    },

    "POST /api/contract": (b) => {
      const ids = (b.item_ids || []).map(Number);
      if (ids.length !== 5) fail("Нужно ровно 5 предметов.", "bad_items");
      const rows = owned(ids);
      const tier = rows[0].tier;
      if (rows.some((r) => r.tier !== tier)) fail("Все 5 предметов должны быть одной ступени.", "mixed");
      if (!NEXT[tier]) fail("Эту ступень контрактом не поднять.", "top");
      const success = rnd() < CT_OK[tier];
      const outTier = success ? NEXT[tier] : tier;
      const src = C.items[pick(rows).catalog_id];
      let pool = [];
      src.cases.forEach((cid) => (pool = pool.concat((cases[cid] && cases[cid].pools[outTier]) || [])));
      const it = C.items[pick([...new Set(pool)])];
      const floats = rows.map((r) => r.float_value).filter((f) => f != null);
      let fv = null;
      if (it.wears && it.wears.length && floats.length) fv = +(it.min_float + (floats.reduce((a, x) => a + x, 0) / floats.length) * (it.max_float - it.min_float)).toFixed(6);
      const d = makeDrop(it, !!it.stattrak && rows.every((r) => r.stattrak), fv);
      remove(rows);
      const row = addItem(d, "contract");
      db.user.nonce += 1;
      save();
      return { success, chance: CT_OK[tier], item: view(row) };
    },

    "POST /api/wheel": () => {
      const d = daily();
      if (d.wheel) fail("Колесо уже крутили сегодня. Приходи завтра.", "used");
      const s = WHEEL.find((x) => x.id === weighted(Object.fromEntries(WHEEL.map((w) => [w.id, w.p]))));
      d.wheel = true;
      const res = { sector: s.id, label: s.label };
      if (s.spins) { db.user.bonus_spins += s.spins; res.spins = s.spins; }
      else { res.coins = Math.round(UNIT * s.cases); db.user.balance += res.coins; }
      save();
      return res;
    },

    "POST /api/task": (b) => {
      const t = TASKS.find((x) => x.id === b.task_id);
      if (!t) fail("Задание не найдено.", "bad_task");
      const d = daily();
      if (d.claimed.includes(t.id)) fail("Награда уже получена.", "claimed");
      if (d[t.field] < t.goal) fail("Задание ещё не выполнено.", "not_done");
      d.claimed.push(t.id);
      const coins = Math.round(UNIT * t.reward_cases);
      db.user.balance += coins;
      save();
      return { task: t.id, coins };
    },

    "POST /api/fair/rotate": async (b) => {
      const u = db.user;
      const revealed = { server_seed: u.server_seed, client_seed: u.client_seed, seed_hash: await sha256(u.server_seed), last_nonce: u.nonce };
      u.server_seed = hex(32);
      u.client_seed = String(b.client_seed || "").trim().slice(0, 64) || hex(8);
      u.nonce = 0;
      save();
      return { revealed, next: { server_seed_hash: await sha256(u.server_seed), client_seed: u.client_seed, nonce: 0 } };
    },
  };

  window.MockApi = {
    async handle(method, path, body) {
      if (!db) fresh();
      const [p, query] = path.split("?");
      const fn = routes[method + " " + p];
      if (!fn) fail("Нет такого действия.", "not_found");
      await new Promise((r) => setTimeout(r, 60)); // как будто сходили на сервер
      return JSON.parse(JSON.stringify(await fn(body || {}, new URLSearchParams(query || ""))));
    },
    reset() { try { localStorage.removeItem("skins-demo-v1"); } catch (e) { /* без хранилища */ } db = null; },
  };
})();
