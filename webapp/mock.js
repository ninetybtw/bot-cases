// Демо-сервер для превью: API /v1 из skinsbot/ в браузере, данные в localStorage.
// Каталог и конфиг приходят из demo-data.js (window.DEMO_CATALOG, window.DEMO_CONFIG).
// Броски настоящие: HMAC-SHA256 как на сервере, поэтому «Честная игра» проверяется и в демо.
(function () {
  "use strict";
  const CAT = window.DEMO_CATALOG;
  const CFG = window.DEMO_CONFIG;
  const KEY = "skins-demo-v14";
  const ORDER = ["common", "uncommon", "rare", "epic", "legendary", "mythic", "secret", "divine", "exclusive"];
  const CASE_TIERS = ["rare", "epic", "legendary", "mythic", "secret"];
  const rank = (t) => ORDER.indexOf(t);
  const atLeast = (t, f) => rank(t) >= rank(f);
  const ME = 1;
  const DEMO_PLAYERS = [[1001, "Макс"], [1002, "Аня"], [1003, "Тимур"], [1004, "Лиза"], [1005, "Ден"]];

  // ---------- SHA-256 и HMAC (синхронно) ----------
  const K = new Uint32Array([0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  function sha256(bytes) {
    const l = bytes.length, withPad = ((l + 9 + 63) >> 6) << 6;
    const m = new Uint8Array(withPad);
    m.set(bytes);
    m[l] = 0x80;
    const bits = l * 8;
    m[withPad - 4] = bits >>> 24; m[withPad - 3] = bits >>> 16; m[withPad - 2] = bits >>> 8; m[withPad - 1] = bits;
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64);
    const r = (x, n) => (x >>> n) | (x << (32 - n));
    for (let o = 0; o < withPad; o += 64) {
      for (let i = 0; i < 16; i++) w[i] = (m[o + 4 * i] << 24) | (m[o + 4 * i + 1] << 16) | (m[o + 4 * i + 2] << 8) | m[o + 4 * i + 3];
      for (let i = 16; i < 64; i++) {
        const s0 = r(w[i - 15], 7) ^ r(w[i - 15], 18) ^ (w[i - 15] >>> 3), s1 = r(w[i - 2], 17) ^ r(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
        const t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    const out = new Uint8Array(32);
    for (let i = 0; i < 8; i++) { out[4 * i] = h[i] >>> 24; out[4 * i + 1] = h[i] >>> 16; out[4 * i + 2] = h[i] >>> 8; out[4 * i + 3] = h[i]; }
    return out;
  }
  const enc = (s) => new TextEncoder().encode(s);
  const fromHex = (hex) => new Uint8Array(hex.match(/../g).map((x) => parseInt(x, 16)));
  const toHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  function hmac(key, msg) {
    let k = key.length > 64 ? sha256(key) : key;
    const kp = new Uint8Array(64); kp.set(k);
    const ip = kp.map((x) => x ^ 0x36), op = kp.map((x) => x ^ 0x5c);
    const inner = sha256(new Uint8Array([...ip, ...msg]));
    return sha256(new Uint8Array([...op, ...inner]));
  }
  const roll = (seed, client, nonce, i) => parseInt(toHex(hmac(fromHex(seed), enc(`${client}:${nonce}:${i}`))).slice(0, 13), 16) / 2 ** 52;
  const seedHash = (seed) => toHex(sha256(fromHex(seed)));
  const randHex = (n) => toHex(crypto.getRandomValues(new Uint8Array(n)));

  class Rng {
    constructor(seed, nonce) { this.s = seed; this.nonce = nonce; this.i = 0; }
    random() { return roll(this.s.server_seed, this.s.client_seed, this.nonce, this.i++); }
    choice(a) { return a[Math.min(Math.floor(this.random() * a.length), a.length - 1)]; }
    weighted(w) {
      const e = Object.entries(w).filter(([, v]) => v > 0);
      let x = this.random() * e.reduce((s, [, v]) => s + v, 0);
      for (const [k, v] of e) { x -= v; if (x < 0) return k; }
      return e[e.length - 1][0];
    }
  }
  const cos = (a) => a[Math.floor(Math.random() * a.length)];

  // ---------- правила (как skinsbot/rules.py и game.py) ----------
  const caseOdds = () => Object.fromEntries(Object.entries(CFG.case_odds).map(([t, p]) => [t, p / 100]));
  const levelOdds = (l) => (CFG.levels[l].odds ? Object.fromEntries(Object.entries(CFG.levels[l].odds).map(([t, p]) => [t, p / 100])) : caseOdds());
  const WEAR = Object.fromEntries(CFG.wear.map(([w, lo, hi, p, m]) => [w, { lo, hi, p: p / 100, m }]));
  const priceKey = (wear, st) => (st ? "ST_" : "") + (wear || "-");
  const splitKey = (k) => { const st = k.startsWith("ST_"); const w = k.replace("ST_", ""); return [w === "-" ? null : w, st]; };
  const price = (id, wear, st) => CAT.items[id].prices[priceKey(wear, st)];
  const quickSell = (c) => Math.floor((c * CFG.sell_system_pct) / 100);
  const cases = Object.fromEntries(CAT.cases.map((c) => [c.id, c]));
  const levels = Object.fromEntries((CAT.levels || []).map((l) => [l.id, l]));
  const cheapest = CAT.cases.reduce((a, b) => (b.price_cents < a.price_cents ? b : a));
  const UNIT = cheapest.price_cents;
  const TARGETS = [];
  Object.values(CAT.items).forEach((it) => Object.entries(it.prices).forEach(([k, p]) => TARGETS.push([p, it.id, k])));
  TARGETS.sort((a, b) => a[0] - b[0]);

  function groupProb(base, n, soft, step, hard) {
    if (n >= hard) return 1;
    if (n >= soft) return Math.min(1, base + (n - soft + 1) * step);
    return base;
  }
  function applyLuck(o, m) {
    if (!m || m === 1) return { ...o };
    const donors = Object.keys(o).sort((a, b) => o[b] - o[a]).slice(0, 2);
    const out = {};
    let used = 0;
    for (const t in o) if (!donors.includes(t)) { out[t] = o[t] * m; used += out[t]; }
    const dt = donors.reduce((s, t) => s + o[t], 0);
    donors.forEach((t) => (out[t] = (o[t] / dt) * (1 - used)));
    return out;
  }
  function spinOdds(se, sl, luck) {
    const o = applyLuck(caseOdds(), luck);
    const P = CFG.pity;
    const B = CASE_TIERS.filter((t) => atLeast(t, P.legend_plus.from)), A = CASE_TIERS.filter((t) => atLeast(t, P.epic_plus.from));
    const baseB = B.reduce((s, t) => s + o[t], 0), baseA = A.reduce((s, t) => s + o[t], 0);
    const qb = groupProb(baseB, sl + 1, P.legend_plus.soft, P.legend_plus.step_pct / 100, P.legend_plus.hard);
    const qa = Math.max(groupProb(baseA, se + 1, P.epic_plus.soft, P.epic_plus.step_pct / 100, P.epic_plus.hard), qb);
    if (qb <= baseB + 1e-12 && qa <= baseA + 1e-12) return [o, null];
    const out = {};
    const part = (ts, total) => { const s = ts.reduce((x, t) => x + o[t], 0); ts.forEach((t) => (out[t] = s ? (o[t] / s) * total : 0)); };
    part(B, qb); part(A.filter((t) => !B.includes(t)), qa - qb); part(Object.keys(o).filter((t) => !A.includes(t)), 1 - qa);
    return [out, qb > baseB + 1e-12 ? "legend" : "epic"];
  }
  function wearDist(it) {
    if (!it.wears || !it.wears.length) return null;
    const tot = it.wears.reduce((s, w) => s + WEAR[w].p, 0);
    return Object.fromEntries(it.wears.map((w) => [w, WEAR[w].p / tot]));
  }
  function makeDrop(rng, it, st, fv, wear) {
    if (wear && it.wears && it.wears.length) {
      const lo = Math.max(WEAR[wear].lo, it.min_float), hi = Math.min(WEAR[wear].hi, it.max_float);
      fv = +(lo + (hi - lo) * rng.random()).toFixed(6);
    } else if (fv == null && it.wears && it.wears.length) {
      wear = rng.weighted(wearDist(it));
      const lo = Math.max(WEAR[wear].lo, it.min_float), hi = Math.min(WEAR[wear].hi, it.max_float);
      fv = +(lo + (hi - lo) * rng.random()).toFixed(6);
    } else if (fv != null) {
      wear = Object.keys(WEAR).find((w) => fv >= WEAR[w].lo && fv < WEAR[w].hi && it.wears.includes(w)) || it.wears[it.wears.length - 1];
    }
    if (st == null) st = !!it.stattrak && rng.random() < CFG.stattrak.pct / 100;
    return { catalog_id: it.id, tier: it.tier, wear: wear || null, float_value: fv == null ? null : fv, pattern: Math.floor(rng.random() * 1000), stattrak: !!st, price: price(it.id, wear, st) };
  }
  function openPool(rng, pools, odds) {
    const tier = rng.weighted(Object.fromEntries(Object.entries(odds).filter(([t]) => pools[t] && pools[t].length)));
    return makeDrop(rng, CAT.items[rng.choice(pools[tier])]);
  }

  // ---------- состояние ----------
  const now = () => Date.now() / 1000;
  const msk = (ts) => new Date(((ts || now()) + 3 * 3600) * 1000);
  const today = () => msk().toISOString().slice(0, 10);
  const week = () => { const d = msk(); const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())); const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day); const y = t.getUTCFullYear(); const w = Math.ceil(((t - Date.UTC(y, 0, 1)) / 864e5 + 1) / 7); return `${y}-W${String(w).padStart(2, "0")}`; };
  const fail = (code, message, extra) => { throw Object.assign(new Error(message), { code, message, ...(extra || {}) }); };
  let db = null;
  function save() { try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (e) { /* без хранилища */ } }
  function load() { try { const d = JSON.parse(localStorage.getItem(KEY)); if (d && d.players) return d; } catch (e) { /* пусто */ } return null; }

  const P = (uid) => db.players[uid];
  const U = (uid) => db.users[uid];
  function money(c) {
    const cur = CFG.currency.countries[U(ME).country];
    const v = (c / 100) * cur.rate;
    const t = cur.decimals ? v.toFixed(cur.decimals).replace(".", ",") : Math.round(v).toLocaleString("ru-RU");
    return `${t} ${cur.symbol}`;
  }
  function ledger(uid, delta, reason) {
    P(uid).balance += delta;
    db.ledger.push([uid, now(), delta, P(uid).balance, reason]);
    if (db.ledger.length > 400) db.ledger.splice(0, 100);
  }
  function charge(uid, c, reason) {
    if (P(uid).balance < c) fail("NOT_ENOUGH_COINS", `Не хватает ${money(c - P(uid).balance)}. Задания и колесо дадут монеты.`, { need_cents: c - P(uid).balance });
    ledger(uid, -c, reason);
  }
  function counter(uid, k) { const p = P(uid); return p.counters[k] && p.counters[k][0] === today() ? p.counters[k][1] : 0; }
  function bump(uid, k, n = 1) { P(uid).counters[k] = [today(), counter(uid, k) + n]; }
  function seed(uid) {
    const p = P(uid);
    if (!p.seed) { const s = randHex(32); p.seed = { id: ++db.seq, server_seed: s, seed_hash: seedHash(s), client_seed: randHex(8), nonce: 0 }; }
    return p.seed;
  }
  function rng(uid) { const s = seed(uid); const r = new Rng(s, s.nonce); s.nonce += 1; return r; }
  function logRoll(uid, kind, r, result) {
    db.rolls.push({ user_id: uid, ts: now(), kind, seed_id: r.s.id, nonce: r.nonce, numbers: r.i, result });
    if (db.rolls.length > 300) db.rolls.splice(0, 100);
  }
  const fairOf = (r) => ({ server_seed_hash: r.s.seed_hash, client_seed: r.s.client_seed, nonce: r.nonce });

  function addItem(uid, d, origin, opts = {}) {
    const it = { id: ++db.seq, owner: uid, catalog_id: d.catalog_id, tier: d.tier, wear: d.wear, float_value: d.float_value, pattern: d.pattern,
      stattrak: d.stattrak, state: "free", locked: false, hold_until: opts.hold ? now() + 86400 : null,
      overflow_until: opts.overflow ? now() + 48 * 3600 : null, origin, drop_price: d.price, created_at: opts.ts || now(), events: [["created", now()]] };
    db.items[it.id] = it;
    return it;
  }
  function view(r) {
    const c = CAT.items[r.catalog_id];
    const p = price(r.catalog_id, r.wear, r.stattrak);
    return { id: r.id, catalog_id: r.catalog_id, name: c.name, weapon: c.weapon, finish: c.finish, tier: r.tier, game_rarity: c.game_rarity,
      image_url: c.image, wear: r.wear, float: r.float_value, pattern: r.pattern, stattrak: !!r.stattrak, price_cents: p, sell_cents: quickSell(p),
      drop_price_cents: r.drop_price, one_in: Math.max(1, Math.round(1 / (caseOdds()[r.tier] || 1))), origin: r.origin, created_at: r.created_at,
      state: r.state, locked: !!r.locked, frozen: false, hold_until: r.hold_until && r.hold_until > now() ? r.hold_until : null, overflow_until: r.overflow_until };
  }
  const itemsOf = (uid) => Object.values(db.items).filter((i) => i.owner === uid && i.state !== "gone");
  function owned(uid, ids, opts = {}) {
    if (!ids.length || new Set(ids).size !== ids.length) fail("BAD_REQUEST", "Выбери предметы.");
    return ids.map((id) => {
      const r = db.items[id];
      if (!r || r.owner !== uid || r.state === "gone") fail("NOT_FOUND", "Предмет не найден в коллекции.");
      if (r.state !== "free") fail("ITEM_BUSY", `Предмет сейчас ${{ market: "на рынке", trade: "в обмене", duel: "в дуэли" }[r.state]}. Сначала сними его оттуда.`);
      if (r.locked && !opts.allowLocked) fail("ITEM_LOCKED", "Предмет под замком. Сначала сними замок.");
      if (opts.noHold && r.hold_until && r.hold_until > now()) fail("ITEM_BUSY", "Предмет на удержании 24 часа после покупки или обмена.");
      if (opts.noOverflow && r.overflow_until) fail("ITEM_BUSY", "Предмет в переполнении. Освободи место в коллекции.");
      return r;
    });
  }
  const isVip = (uid) => P(uid).vip_until && P(uid).vip_until > now();
  const slots = (uid) => Math.min(CFG.inventory.slots_max, P(uid).slots + (isVip(uid) ? CFG.inventory.slots_vip_bonus : 0));
  const used = (uid) => { const it = itemsOf(uid); return [it.filter((i) => !i.overflow_until).length, it.filter((i) => i.overflow_until).length]; };
  function energy(uid) {
    const p = P(uid), period = CFG.energy.regen_minutes * 60;
    let e = p.energy, ts = p.energy_ts;
    if (e >= CFG.energy.max) return [e, now()];
    const g = Math.floor((now() - ts) / period);
    if (g) { e = Math.min(CFG.energy.max, e + g); ts = e >= CFG.energy.max ? now() : ts + g * period; }
    return [e, ts];
  }
  function sync(uid) {
    const p = P(uid);
    [p.energy, p.energy_ts] = energy(uid);
    let free = slots(uid) - used(uid)[0];
    for (const it of itemsOf(uid).filter((i) => i.overflow_until).sort((a, b) => a.id - b.id)) {
      if (free <= 0) break;
      if (it.overflow_until < now()) { it.state = "gone"; ledger(uid, quickSell(price(it.catalog_id, it.wear, it.stattrak)), "overflow_sale"); continue; }
      it.overflow_until = null; free--;
    }
  }
  function addXp(uid, xp) {
    const p = P(uid);
    p.xp += xp;
    while (p.xp >= (CFG.xp || { per_level: 100 }).per_level * p.level * (p.level + 1) / 2) p.level++;
  }
  const coinLimit = (lvl) => Math.min(CFG.spin.coin_per_day_max, CFG.spin.coin_per_day_base + CFG.spin.coin_per_level * (lvl - 1));

  function newPlayer(uid, name, opts = {}) {
    db.users[uid] = { id: uid, name, country: opts.country || "RU", country_confirmed: !!opts.confirmed, country_set_at: null, created_at: opts.created || now() };
    db.players[uid] = { balance: 0, xp: 0, level: 1, energy: CFG.energy.start, energy_ts: now(), bonus_spins: 0, paid_attempts: 0, counters: {},
      since_epic: 0, since_legendary: 0, luck_mult: null, luck_left: 0, upgrade_bonus: 0, slots: CFG.inventory.slots, vip_until: null,
      total_spins: 0, showcase: [], frame: null, seed: null, last_drop: null };
    ledger(uid, UNIT * CFG.start_balance_cases, "start");
  }

  function seedWorld() {
    db = { users: {}, players: {}, items: {}, ledger: [], rolls: [], seq: 0, sales: [], lots: [], orders: [], trades: [], duels: [], quests: {},
      wheel: {}, raffle: null, tickets: {}, refs: [], tiers: {}, cosmetics: {}, payments: [], feed: [] };
    const lang = (navigator.language || "ru").slice(0, 2);
    newPlayer(ME, "Ты", { country: CFG.currency.language_to_country[lang] || "RU", created: now() - 3 * 86400 });
    ledger(ME, UNIT * 25, "demo_bonus"); // демо: побольше монет, чтобы попробовать рынок и уровни
    P(ME).energy = 2;
    P(ME).energy_ts = now() - 3600 * 1.6;
    const r0 = new Rng({ id: 0, server_seed: randHex(32), client_seed: "demo", nonce: 0 }, 0);
    for (let k = 0; k < 9; k++) addItem(ME, openPool(r0, cheapest.pools, caseOdds()), "case:" + cheapest.id, { ts: now() - (12 - k) * 1800 });
    addItem(ME, makeDrop(r0, CAT.items[cos(cheapest.pools.epic)]), "case:" + cheapest.id, { ts: now() - 600 });
    P(ME).total_spins = 10;
    P(ME).xp = 100; P(ME).level = 2;
    // Вымышленные игроки демо: лоты на рынке, лента дропов, рейтинг, обмен и дуэль.
    DEMO_PLAYERS.forEach(([uid, name], n) => {
      newPlayer(uid, name, { confirmed: true, created: now() - 30 * 86400 });
      P(uid).level = 3 + n * 2;
      P(uid).xp = 100 * P(uid).level * (P(uid).level - 1) / 2 + 20;
      const c = CAT.cases[n % CAT.cases.length];
      for (let k = 0; k < 6; k++) addItem(uid, openPool(r0, c.pools, caseOdds()), "case:" + c.id, { ts: now() - k * 3000 });
      const rare = ["legendary", "epic", "mythic", "epic", "secret"][n];
      const it = addItem(uid, makeDrop(r0, CAT.items[cos(c.pools[rare])]), "case:" + c.id, { ts: now() - n * 900 });
      db.feed.push(it.id);
    });
    db.refs.push([ME, 1002, now() - 86400, now() - 80000], [ME, 1004, now() - 50000, null]);
    const sellers = itemsOf(1001).concat(itemsOf(1003), itemsOf(1005)).sort((a, b) => b.drop_price - a.drop_price).slice(0, 10);
    sellers.forEach((it, k) => {
      const p = price(it.catalog_id, it.wear, it.stattrak);
      it.state = "market";
      db.lots.push({ id: ++db.seq, item_id: it.id, seller: it.owner, price: Math.max(1, Math.round(p * (0.85 + (k % 5) * 0.08))), fee_pct: 10,
        status: "active", buyer: null, sold_at: null, created_at: now() - k * 3600, expires_at: now() + 2 * 86400 });
    });
    const gift = itemsOf(1002).find((i) => i.state === "free" && i.tier === "rare");
    const mine = itemsOf(ME).find((i) => i.tier === "rare");
    if (gift && mine) {
      gift.state = "trade";
      db.trades.push({ id: ++db.seq, from_user: 1002, to_user: ME, status: "pending", give: [gift.id], take: [mine.id],
        value_from: price(gift.catalog_id, gift.wear, gift.stattrak), value_to: price(mine.catalog_id, mine.wear, mine.stattrak), created_at: now() - 1200 });
    }
    const duelItems = itemsOf(1004).filter((i) => i.state === "free").slice(0, 3);
    duelItems.forEach((i) => (i.state = "duel"));
    db.duels.push({ id: ++db.seq, p1: 1004, p2: null, friend_id: null, p1_items: duelItems.map((i) => i.id), p2_items: null, status: "open", created_at: now() - 900 });
    const rs = randHex(32);
    db.raffle = { day: today(), seed: rs, seed_hash: seedHash(rs) };
    save();
  }

  function questProgress(uid, code, n, weekly) {
    const q = [...CFG.quests.daily, ...CFG.quests.weekly].find((x) => x.code === code);
    const isWeekly = weekly || CFG.quests.weekly.some((x) => x.code === code);
    const key = `${uid}|${isWeekly ? week() : today()}|${code}`;
    const row = db.quests[key] || (db.quests[key] = { progress: 0, target: q ? q.target : 0, claimed: false });
    row.progress += n;
  }
  const questRow = (uid, period, code) => db.quests[`${uid}|${period}|${code}`];
  function questDaysWeek(uid) {
    const d = msk(); const wd = (d.getUTCDay() + 6) % 7;
    const days = Array.from({ length: 7 }, (_, i) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - wd + i)).toISOString().slice(0, 10));
    return days.filter((day) => CFG.quests.daily.some((q) => (questRow(uid, day, q.code) || {}).claimed)).length;
  }
  function grant(uid, r, reason, rg) {
    const out = {};
    if (r.cases) { out.coins_cents = Math.round(UNIT * r.cases); ledger(uid, out.coins_cents, reason); }
    if (r.spins) { P(uid).bonus_spins += r.spins; out.spins = r.spins; }
    if (r.cosmetic) { db.cosmetics[`${uid}|${r.cosmetic}`] = true; out.cosmetic = r.cosmetic; }
    if (r.drop_from || r.level) {
      let odds, pools;
      if (r.level) { odds = levelOdds(r.level); pools = levels[r.level].pools; }
      else {
        const base = Object.fromEntries(Object.entries(caseOdds()).filter(([t]) => atLeast(t, r.drop_from)));
        const s = Object.values(base).reduce((a, b) => a + b, 0);
        odds = Object.fromEntries(Object.entries(base).map(([t, p]) => [t, p / s]));
        pools = Object.fromEntries(Object.keys(odds).map((t) => [t, [...new Set(CAT.cases.flatMap((c) => c.pools[t]))]]));
      }
      const it = addItem(uid, openPool(rg || rng(uid), pools, odds), reason.split("_")[0], { overflow: slots(uid) - used(uid)[0] < 1 });
      out.item = view(it);
    }
    return out;
  }
  function referralCheck(uid) {
    const ref = db.refs.find((r) => r[1] === uid && !r[3]);
    if (!ref || P(uid).total_spins < CFG.referral.qualify_spins) return;
    ref[3] = now();
    const n = db.refs.filter((r) => r[0] === ref[0] && r[3]).length;
    for (const [k, reward] of Object.entries(CFG.referral.rewards)) {
      if (n >= +k && !db.tiers[`${ref[0]}|${k}`]) { db.tiers[`${ref[0]}|${k}`] = now(); grant(ref[0], reward, "referral"); }
    }
  }
  const friendIds = (uid) => db.refs.filter((r) => r[0] === uid).map((r) => r[1]).concat(db.refs.filter((r) => r[1] === uid).map((r) => r[0]));
  function tickets(uid) {
    if (!db.raffle || db.raffle.day !== today()) { const s = randHex(32); db.raffle = { day: today(), seed: s, seed_hash: seedHash(s) }; db.tickets = {}; }
    return db.tickets[uid] || 0;
  }

  function lotView(l) {
    return { id: l.id, item: view(db.items[l.item_id]), price_cents: l.price, currency: "coins", seller_name: U(l.seller).name, seller_id: l.seller,
      status: l.status, created_at: l.created_at, expires_at: l.expires_at, buyer_id: l.buyer, sold_at: l.sold_at,
      price_pct: Math.round((l.price / Math.max(1, view(db.items[l.item_id]).price_cents)) * 100) };
  }
  function executeSale(l, buyer, reserved) {
    if (slots(buyer) - used(buyer)[0] < 1) fail("INVENTORY_FULL", "Освободи место в коллекции, чтобы купить предмет.");
    if (!reserved) charge(buyer, l.price, "market_buy");
    const fee = Math.floor((l.price * l.fee_pct) / 100);
    ledger(l.seller, l.price - fee, "market_sale");
    Object.assign(l, { status: "sold", buyer, sold_at: now() });
    Object.assign(db.items[l.item_id], { owner: buyer, state: "free", hold_until: now() + 86400, locked: false });
    return { lot_id: l.id, item: view(db.items[l.item_id]), price_cents: l.price, fee_cents: fee, balance_cents: P(buyer).balance };
  }

  // ---------- маршруты ----------
  const R = {};
  const route = (m, p, fn) => (R[m + " " + p] = fn);

  route("GET", "/config", () => ({
    version: CFG.version, generated_at: CAT.generated_at, usd_rub: CAT.usd_rub, tiers: CFG.tiers, currency: CFG.currency,
    case_odds: Object.entries(caseOdds()).map(([tier, p]) => ({ tier, p, one_in: Math.max(1, Math.round(1 / p)) })),
    cases: CAT.cases.map((c) => ({ id: c.id, name: c.name, image_url: c.image, price_cents: c.price_cents, ev_cents: c.ev_cents, free: c.id === cheapest.id,
      pools: Object.fromEntries(Object.entries(c.pools).map(([t, ids]) => [t, ids.map((id) => { const it = CAT.items[id]; const ps = Object.values(it.prices); return { id, name: it.name, image_url: it.image, tier: it.tier, min_cents: Math.min(...ps), max_cents: Math.max(...ps) }; })])) })),
    levels: Object.entries(CFG.levels).map(([id, l]) => {
      if (!l.odds) return { id, name: l.name, what: l.what, from: l.from, odds: null };
      const o = levelOdds(id);
      return { id, name: l.name, what: l.what, from: l.from, price_cents: levels[id].price_cents, ev_cents: levels[id].ev_cents,
        odds: Object.entries(o).map(([tier, p]) => ({ tier, p })), goal: { label: l.goal.label, p: Object.entries(o).filter(([t]) => atLeast(t, l.goal.from)).reduce((s, [, p]) => s + p, 0) } };
    }),
    wear: CFG.wear, stattrak: CFG.stattrak, pity: CFG.pity, spin: CFG.spin, energy: CFG.energy, upgrader: CFG.upgrader, contracts: CFG.contracts,
    market: CFG.market, trades: CFG.trades, wheel: { daily: CFG.wheel.daily, weekly: CFG.wheel.weekly, weekly_unlock_days: CFG.wheel.weekly_unlock_days },
    duels: CFG.duels, referral: CFG.referral, cosmetics: CFG.cosmetics, sell_system_pct: CFG.sell_system_pct, undo_sell: CFG.undo_sell,
    inventory: CFG.inventory, notifications: CFG.notifications, unit_cents: UNIT, free_case_id: cheapest.id, shop_enabled: true,
  }));

  route("GET", "/me", () => {
    sync(ME);
    const u = U(ME), p = P(ME), s = seed(ME);
    const [e, ts] = energy(ME);
    const [odds] = spinOdds(p.since_epic, p.since_legendary, 1);
    const [main, over] = used(ME);
    const per = CFG.xp ? CFG.xp.per_level : 100;
    return {
      user: { id: ME, name: u.name, level: p.level, xp: p.xp, xp_next: per * p.level * (p.level + 1) / 2, xp_prev: per * (p.level - 1) * p.level / 2,
        vip_until: isVip(ME) ? p.vip_until : null, frame: p.frame, created_at: u.created_at },
      country: u.country, country_confirmed: u.country_confirmed, country_change_after: u.country_set_at && u.country_set_at + 30 * 86400 > now() ? u.country_set_at + 30 * 86400 : null,
      currency: { country: u.country, ...CFG.currency.countries[u.country], game_label: CFG.currency.game_label },
      balance_cents: p.balance, star_credit: 0, energy: { now: e, max: CFG.energy.max, next_at: e >= CFG.energy.max ? null : ts + CFG.energy.regen_minutes * 60 },
      attempts: p.paid_attempts, bonus_spins: p.bonus_spins, coin_spins_left: Math.max(0, coinLimit(p.level) - counter(ME, "coin")),
      level_spins_left: Math.max(0, CFG.spin.levels_per_day - counter(ME, "lvl")), luck: p.luck_left ? { mult: p.luck_mult, spins_left: p.luck_left } : null,
      pity: { epic_in: CFG.pity.epic_plus.hard - p.since_epic, legend_in: CFG.pity.legend_plus.hard - p.since_legendary,
        epic_chance_pct: Math.round(Object.entries(odds).filter(([t]) => atLeast(t, "epic")).reduce((x, [, v]) => x + v, 0) * 10000) / 100 },
      slots: { used: main, total: slots(ME), overflow: over, overflow_max: CFG.inventory.overflow_slots },
      upgrades_left: CFG.upgrader.per_day - counter(ME, "upg"), upgrade_bonus_pct: p.upgrade_bonus, duels_left: CFG.duels.casual.per_day - counter(ME, "duel"),
      total_spins: p.total_spins, break_reminder: counter(ME, "spins") >= CFG.spin.break_reminder_spins,
      today: { wheel_daily: !db.wheel[`daily|${today()}`], wheel_weekly_unlocked: questDaysWeek(ME) >= CFG.wheel.weekly_unlock_days && !db.wheel[`weekly|${week()}`],
        quest_days_week: questDaysWeek(ME), quests_done: CFG.quests.daily.filter((q) => (questRow(ME, today(), q.code) || {}).claimed).length, raffle_tickets: tickets(ME) },
      fair: { server_seed_hash: s.seed_hash, client_seed: s.client_seed, nonce: s.nonce }, showcase: p.showcase, last_drop: null, shop_enabled: true,
      invite_link: "https://t.me/cs2_cases_bot?start=ref_1",
    };
  });

  route("PUT", "/me/country", (b) => {
    const u = U(ME);
    if (!CFG.currency.countries[b.country]) fail("BAD_REQUEST", "Такой страны нет в списке.");
    if (u.country_confirmed && u.country !== b.country && u.country_set_at && u.country_set_at + 30 * 86400 > now())
      fail("NOT_ALLOWED_YET", `Страну можно менять раз в 30 дней. Осталось ${Math.ceil((u.country_set_at + 30 * 86400 - now()) / 86400)} дн.`);
    if (!u.country_confirmed || u.country !== b.country) Object.assign(u, { country: b.country, country_confirmed: true, country_set_at: now() });
    return { country: u.country };
  });
  route("POST", "/me/last-drop/ack", () => ({ ok: true }));

  route("POST", "/spin", (b) => {
    const count = +b.count || 1, level = b.level || "common", isLevel = level !== "common", source = b.source || "coin";
    if (!CFG.spin.counts.includes(count)) fail("BAD_REQUEST", "Можно крутить ×1, ×3, ×5 или ×10.");
    sync(ME);
    const p = P(ME);
    let pools, cost = 0, unit;
    if (isLevel) {
      if (source !== "coin") fail("BAD_REQUEST", "Уровни выше Обычной крутятся только за монеты.");
      pools = levels[level].pools; unit = levels[level].price_cents;
      if (counter(ME, "lvl") + count > CFG.spin.levels_per_day) fail("LEVEL_LIMIT", `Уровни выше Обычной: не больше ${CFG.spin.levels_per_day} круток в сутки.`);
    } else {
      const c = cases[b.case_id];
      if (!c) fail("BAD_REQUEST", "Такого кейса нет.");
      pools = c.pools; unit = c.price_cents;
      if (source !== "coin" && c.id !== cheapest.id) fail("BAD_REQUEST", "Бесплатные спины и попытки открывают самый дешёвый кейс.");
    }
    let [e, ts] = energy(ME);
    if (source === "free") {
      if (e + p.bonus_spins < count) fail("NO_ENERGY", "Бесплатные спины закончились. Новый будет через 3 часа.");
      const use = Math.min(e, count);
      if (e >= CFG.energy.max && use) ts = now();
      p.energy = e - use; p.energy_ts = ts; p.bonus_spins -= count - use;
    } else if (source === "attempt") {
      if (p.paid_attempts < count) fail("NO_ENERGY", "Попыток не хватает.");
      p.paid_attempts -= count;
    } else {
      if (!isLevel && count > coinLimit(p.level) - counter(ME, "coin")) fail("DAILY_LIMIT", `За монеты сегодня можно открыть ещё ${Math.max(0, coinLimit(p.level) - counter(ME, "coin"))}.`);
      cost = unit * count;
      if (!b.confirmed && p.balance >= cost && (cost >= CFG.spin.confirm_from_cents || cost > p.balance * CFG.spin.confirm_balance_share))
        fail("CONFIRM_REQUIRED", `Подтверди покупку на ${money(cost)}.`, { cost_cents: cost, pause_s: CFG.spin.confirm_pause_s });
      charge(ME, cost, "spin");
      bump(ME, isLevel ? "lvl" : "coin", count);
    }
    const free = slots(ME) - used(ME)[0];
    const toOver = Math.max(0, count - Math.max(0, free));
    if (used(ME)[1] + toOver > CFG.inventory.overflow_slots) fail("INVENTORY_FULL", "Освободи место: коллекция и переполнение заполнены.");
    const r = rng(ME);
    const drops = [];
    for (let k = 0; k < count; k++) {
      let odds, applied = null;
      if (isLevel) odds = levelOdds(level);
      else { [odds, applied] = spinOdds(p.since_epic, p.since_legendary, p.luck_left > 0 ? p.luck_mult : 1); if (p.luck_left > 0) p.luck_left--; }
      const d = openPool(r, pools, odds);
      if (!isLevel) { p.since_epic = atLeast(d.tier, "epic") ? 0 : p.since_epic + 1; p.since_legendary = atLeast(d.tier, "legendary") ? 0 : p.since_legendary + 1; }
      const it = addItem(ME, d, isLevel ? "level:" + level : "case:" + b.case_id, { overflow: k >= count - toOver });
      if (atLeast(d.tier, "epic")) db.feed.push(it.id);
      const avail = Object.entries(odds).filter(([t]) => pools[t] && pools[t].length);
      const reel = Array.from({ length: 60 }, (_, i) => {
        if (i === 52) return d.catalog_id;
        let x = Math.random() * avail.reduce((s, [, v]) => s + v, 0);
        for (const [t, v] of avail) { x -= v; if (x < 0) return cos(pools[t]); }
        return cos(pools[avail[0][0]]);
      }).map((id) => ({ tier: CAT.items[id].tier, image_url: CAT.items[id].image, name: CAT.items[id].name }));
      drops.push({ drop_id: r.nonce, items: [view(it)], reel, stop_index: 52, pity_applied: applied });
    }
    if (!p.luck_left) p.luck_mult = null;
    p.total_spins += count;
    addXp(ME, 10 * count);
    bump(ME, "spins", count);
    questProgress(ME, "spin3", count);
    logRoll(ME, "spin", r, { level, case_id: b.case_id, tiers: drops.map((d) => d.items[0].tier) });
    const [e2, ts2] = energy(ME);
    return { drops, fair: fairOf(r), balance_cents: p.balance, energy: { now: e2, max: CFG.energy.max, next_at: e2 >= CFG.energy.max ? null : ts2 + CFG.energy.regen_minutes * 60 }, overflow: toOver };
  });

  route("GET", "/fair", () => {
    const s = seed(ME);
    const seeds = Object.fromEntries((db.old_seeds || []).concat([s]).map((x) => [x.id, x]));
    return { server_seed_hash: s.seed_hash, client_seed: s.client_seed, nonce: s.nonce, formula: CFG.fairness.roll,
      rolls: db.rolls.filter((r) => r.user_id === ME).slice(-50).reverse().map((r) => {
        const sd = seeds[r.seed_id] || {};
        return { kind: r.kind, ts: r.ts, nonce: r.nonce, numbers: r.numbers, seed_hash: sd.seed_hash, client_seed: sd.client_seed, server_seed: sd.revealed ? sd.server_seed : null, result: r.result };
      }) };
  });
  route("POST", "/fair/rotate", (b) => {
    const old = seed(ME);
    old.revealed = true;
    (db.old_seeds = db.old_seeds || []).push(old);
    const s = randHex(32);
    P(ME).seed = { id: ++db.seq, server_seed: s, seed_hash: seedHash(s), client_seed: String(b.client_seed || "").trim().slice(0, 64) || randHex(8), nonce: 0 };
    return { revealed: { server_seed: old.server_seed, seed_hash: old.seed_hash, client_seed: old.client_seed, last_nonce: old.nonce },
      next: { server_seed_hash: P(ME).seed.seed_hash, client_seed: P(ME).seed.client_seed, nonce: 0 } };
  });

  route("GET", "/inventory", () => {
    sync(ME);
    const items = itemsOf(ME).sort((a, b) => b.id - a.id).map(view);
    const [main, over] = used(ME);
    return { items, summary: { used: main, total: slots(ME), overflow: over, value_cents: items.reduce((s, i) => s + i.price_cents, 0), week_change_cents: 0 } };
  });
  route("GET", "/items/:id", (b, q, id) => {
    const r = db.items[id];
    if (!r || r.owner !== ME) fail("NOT_FOUND", "Предмет не найден.");
    const p = price(r.catalog_id, r.wear, r.stattrak);
    const chart = Array.from({ length: 30 }, (_, k) => {
      const d = new Date((now() - (29 - k) * 86400) * 1000).toISOString().slice(0, 10);
      return { day: d, cents: Math.round(p * (1 + 0.06 * Math.sin((r.id + k) / 4) - 0.002 * (29 - k))) };
    });
    chart[29].cents = p;
    return { ...view(r), chart, history: r.events.map(([kind, ts]) => ({ kind, ts })), owners: r.prev_owner ? 2 : 1 };
  });
  route("POST", "/items/sell", (b) => {
    const rows = owned(ME, b.item_ids.map(Number));
    if (rows.length > 1 && rows.some((r) => atLeast(r.tier, "legendary"))) fail("BAD_REQUEST", "Легендарные и выше продаются только по одному.");
    if (rows.length === 1 && atLeast(rows[0].tier, "legendary") && !b.confirmed) fail("CONFIRM_REQUIRED", "Подтверди продажу.", { pause_s: atLeast(rows[0].tier, "mythic") ? 3 : 0 });
    let total = 0;
    const states = {};
    rows.forEach((r) => { total += quickSell(price(r.catalog_id, r.wear, r.stattrak)); states[r.id] = [r.hold_until, r.overflow_until]; r.state = "gone"; r.events.push(["sold_system", now()]); });
    if (rows.some((r) => itemsOf(ME).some((x) => x.catalog_id === r.catalog_id))) questProgress(ME, "sell1", 1);
    ledger(ME, total, "sell_system");
    const sale = { id: ++db.seq, item_ids: rows.map((r) => r.id), states, total, created_at: now(), undo_until: now() + CFG.undo_sell.minutes * 60, undone: false };
    db.sales.push(sale);
    return { sale_id: sale.id, total_cents: total, undo_until: sale.undo_until, balance_cents: P(ME).balance };
  });
  route("POST", "/sales/:id/undo", (b, q, id) => {
    const s = db.sales.find((x) => x.id === id);
    if (!s || s.undone) fail("NOT_FOUND", "Продажа не найдена или уже отменена.");
    if (s.undo_until < now()) fail("NOT_ALLOWED_YET", "Отменить продажу можно только в течение 10 минут.");
    if (counter(ME, "undo") >= CFG.undo_sell.per_day) fail("DAILY_LIMIT", "Отмен продажи сегодня больше нельзя: лимит 5 в сутки.");
    charge(ME, s.total, "sell_undo");
    s.item_ids.forEach((i) => { const r = db.items[i]; r.state = "free"; [r.hold_until, r.overflow_until] = s.states[i]; r.events.push(["undo", now()]); });
    s.undone = true;
    bump(ME, "undo");
    return { sale_id: id, restored: s.item_ids, balance_cents: P(ME).balance };
  });
  route("POST", "/items/:id/lock", (b, q, id) => { const [r] = owned(ME, [id], { allowLocked: true }); r.locked = true; r.events.push(["lock", now()]); return { id, locked: true }; });
  route("POST", "/items/:id/unlock", (b, q, id) => {
    const [r] = owned(ME, [id], { allowLocked: true });
    if (r.locked && !b.confirm) fail("CONFIRM_REQUIRED", "Подтверди снятие замка.");
    r.locked = false; r.events.push(["unlock", now()]);
    return { id, locked: false };
  });
  function dupes() {
    const g = {};
    itemsOf(ME).filter((i) => i.state === "free" && !i.locked).forEach((i) => (g[i.catalog_id + i.wear + i.stattrak] = g[i.catalog_id + i.wear + i.stattrak] || []).push(i));
    return Object.values(g).flatMap((arr) => arr.sort((a, b) => (a.float_value ?? 1) - (b.float_value ?? 1)).slice(1).filter((i) => !atLeast(i.tier, "legendary")));
  }
  route("POST", "/items/sell-duplicates/quote", () => { const d = dupes(); return { item_ids: d.map((i) => i.id), total_cents: d.reduce((s, i) => s + view(i).sell_cents, 0) }; });
  route("POST", "/items/sell-cheaper/quote", (b) => {
    const d = itemsOf(ME).filter((i) => i.state === "free" && !i.locked && !atLeast(i.tier, "legendary") && view(i).price_cents < b.max_cents);
    return { item_ids: d.map((i) => i.id), total_cents: d.reduce((s, i) => s + view(i).sell_cents, 0) };
  });
  route("POST", "/showcase", (b) => { if (b.item_ids.length > 3) fail("BAD_REQUEST", "На витрине до 3 предметов."); P(ME).showcase = b.item_ids; return { showcase: b.item_ids }; });

  route("POST", "/contracts", (b) => {
    const ids = b.item_ids.map(Number);
    if (ids.length !== 5) fail("BAD_REQUEST", "Нужно ровно 5 предметов.");
    const rows = owned(ME, ids);
    const tier = rows[0].tier;
    if (rows.some((r) => r.tier !== tier)) fail("BAD_REQUEST", "Все 5 предметов должны быть одной ступени.");
    if (!CFG.contracts.next[tier]) fail("BAD_REQUEST", "Эту ступень контрактом не поднять.");
    if (atLeast(tier, "epic") && !b.confirmed) fail("CONFIRM_REQUIRED", "Пять предметов исчезнут, взамен придёт один.", { pause_s: 3 });
    const r = rng(ME);
    const guaranteed = rank(tier) <= rank(CFG.contracts.guaranteed_up_to);
    const success = guaranteed || r.random() < CFG.contracts.success_pct / 100;
    const outTier = success ? CFG.contracts.next[tier] : tier;
    const src = CAT.items[r.choice(rows).catalog_id];
    const pool = [...new Set(src.cases.flatMap((cid) => (cases[cid] ? cases[cid].pools[outTier] : [])))].sort();
    const it = CAT.items[r.choice(pool)];
    const floats = rows.map((x) => x.float_value).filter((f) => f != null);
    const fv = it.wears && it.wears.length && floats.length ? +(it.min_float + (floats.reduce((a, x) => a + x, 0) / floats.length) * (it.max_float - it.min_float)).toFixed(6) : null;
    const d = makeDrop(r, it, !!it.stattrak && rows.every((x) => x.stattrak), fv);
    rows.forEach((x) => (x.state = "gone"));
    const item = addItem(ME, d, "contract");
    questProgress(ME, "contract1", 1, true);
    logRoll(ME, "contract", r, { success });
    return { success, chance_pct: guaranteed ? 100 : CFG.contracts.success_pct, item: view(item), fair: fairOf(r) };
  });

  route("GET", "/upgrades/targets", (b, q) => {
    const stake = +q.get("stake_cents"), mult = q.get("mult") ? +q.get("mult") : null;
    const lo = Math.max(stake * (mult || 1.2), stake + 1), hi = stake * (mult || 1.2) * (mult ? 1.25 : 50);
    let out = TARGETS.filter(([p]) => p >= lo && p <= hi);
    if (out.length > 30) { const step = out.length / 30; out = Array.from({ length: 30 }, (_, i) => out[Math.floor(i * step)]); }
    return { targets: out.map(([p, id, k]) => { const [wear, st] = splitKey(k); const it = CAT.items[id]; return { catalog_item_id: id, wear, stattrak: st, name: it.name, image_url: it.image, tier: it.tier, price_cents: p }; }) };
  });
  function upQuote(b) {
    const u = CFG.upgrader;
    const ids = (b.stake_item_ids || []).map(Number);
    if (ids.length > u.max_items) fail("BAD_REQUEST", `В апгрейдер можно положить до ${u.max_items} предметов.`);
    const it = CAT.items[b.target.catalog_item_id];
    const key = priceKey(b.target.wear, b.target.stattrak);
    if (!it || !(key in it.prices)) fail("NOT_FOUND", "Цель не найдена.");
    const rows = ids.length ? owned(ME, ids) : [];
    const coins = +b.stake_coins_cents || 0;
    if (!rows.length && !coins) fail("BAD_REQUEST", "Положи предмет или монеты.");
    const stake = rows.reduce((s, r) => s + price(r.catalog_id, r.wear, r.stattrak), 0) + coins;
    const tp = it.prices[key];
    if (tp <= stake) fail("BAD_REQUEST", "Цель должна стоить дороже ставки.");
    const chance = Math.min(u.max_chance_pct / 100, (stake / tp) * u.k * (1 + P(ME).upgrade_bonus / 100));
    return { rows, stake, it, key, tp, chance, coins, confirm: rows.some((r) => atLeast(r.tier, u.confirm_from)) };
  }
  route("POST", "/upgrades/quote", (b) => { const q = upQuote(b); return { chance_pct: Math.round(q.chance * 10000) / 100, formula: "", confirm_required: q.confirm, stake_cents: q.stake, target_cents: q.tp, max_chance_pct: CFG.upgrader.max_chance_pct }; });
  route("POST", "/upgrades", (b) => {
    const u = CFG.upgrader;
    if (counter(ME, "upg") >= u.per_day) fail("DAILY_LIMIT", "На сегодня апгрейды закончились. Возвращайся завтра.");
    const q = upQuote(b);
    if (b.quote_chance_pct != null && Math.abs(b.quote_chance_pct - q.chance * 100) > 0.01) fail("CONFIG_CHANGED", "Цены обновились, шанс изменился.");
    if (q.confirm && !b.confirmed) fail("CONFIRM_REQUIRED", "Точно? Ставка сгорит при неудаче.", { pause_s: u.confirm_pause_s });
    const total = itemsOf(ME).reduce((s, r) => s + price(r.catalog_id, r.wear, r.stattrak), 0) + P(ME).balance;
    if (q.stake > total * u.max_share_of_collection) fail("BAD_REQUEST", "За раз можно поставить не больше половины коллекции и баланса.");
    if (q.coins) charge(ME, q.coins, "upgrade_stake");
    q.rows.forEach((r) => (r.state = "gone"));
    const r = rng(ME);
    const x = r.random();
    const success = x < q.chance;
    const res = { success, roll: Math.round(x * 1e6) / 1e4, chance_pct: Math.round(q.chance * 10000) / 100, fair: fairOf(r) };
    if (success) {
      const [wear, st] = splitKey(q.key);
      res.item = view(addItem(ME, makeDrop(r, q.it, st, null, wear), "upgrade"));
      P(ME).upgrade_bonus = 0; res.next_bonus_pct = 0;
    } else {
      res.parts_cents = Math.floor((q.stake * u.fail_parts_pct) / 100);
      ledger(ME, res.parts_cents, "upgrade_parts");
      P(ME).upgrade_bonus = Math.min(u.max_bonus_pct, P(ME).upgrade_bonus + u.fail_bonus_pct);
      res.next_bonus_pct = P(ME).upgrade_bonus;
    }
    bump(ME, "upg");
    logRoll(ME, "upgrade", r, { success });
    res.balance_cents = P(ME).balance;
    return res;
  });

  route("GET", "/market/lots", (b, q) => {
    const mine = q.get("mine") === "1", tier = q.get("tier");
    let lots = db.lots.filter((l) => l.status === "active" && (!mine || l.seller === ME));
    if (tier) lots = lots.filter((l) => db.items[l.item_id].tier === tier);
    return { lots: lots.slice().reverse().map(lotView), stars_enabled: false };
  });
  route("POST", "/market/lots", (b) => {
    const m = CFG.market;
    const [r] = owned(ME, [+b.item_id], { noHold: true, noOverflow: true });
    if (db.lots.filter((l) => l.seller === ME && l.status === "active").length >= m.lots_max) fail("DAILY_LIMIT", `Можно держать не больше ${m.lots_max} лотов.`);
    const ref = price(r.catalog_id, r.wear, r.stattrak), lo = Math.floor((ref * m.band_pct[0]) / 100), hi = Math.floor((ref * m.band_pct[1]) / 100);
    if (b.price < lo || b.price > hi) fail("PRICE_OUT_OF_BAND", `Цена лота от ${money(lo)} до ${money(hi)} (50–150% справочной).`);
    const l = { id: ++db.seq, item_id: r.id, seller: ME, price: b.price, fee_pct: isVip(ME) ? m.fee_vip_pct : m.fee_pct, status: "active", buyer: null, sold_at: null, created_at: now(), expires_at: now() + m.lot_days * 86400 };
    db.lots.push(l);
    r.state = "market";
    // В демо один из игроков иногда сразу покупает недорогой лот.
    if (b.price <= ref) setTimeout(() => { if (l.status === "active") { executeSale(l, 1003, true); save(); } }, 15000);
    return lotView(l);
  });
  route("DELETE", "/market/lots/:id", (b, q, id) => {
    const l = db.lots.find((x) => x.id === id && x.seller === ME && x.status === "active");
    if (!l) fail("NOT_FOUND", "Лот не найден.");
    l.status = "cancelled"; db.items[l.item_id].state = "free";
    return { id, status: "cancelled" };
  });
  route("POST", "/market/lots/:id/buy", (b, q, id) => {
    const l = db.lots.find((x) => x.id === id);
    if (!l || l.status !== "active") fail("NOT_FOUND", "Лот уже купили или сняли.");
    if (l.seller === ME) fail("BAD_REQUEST", "Это твой лот.");
    return executeSale(l, ME, false);
  });
  route("GET", "/market/orders", () => ({ orders: db.orders.filter((o) => o.buyer === ME).slice().reverse().map((o) => ({ ...o, name: CAT.items[o.catalog_item_id].name, image_url: CAT.items[o.catalog_item_id].image, tier: CAT.items[o.catalog_item_id].tier })) }));
  route("POST", "/market/orders", (b) => {
    const it = CAT.items[b.catalog_item_id], key = priceKey(b.wear, b.stattrak);
    if (!it || !(key in it.prices)) fail("NOT_FOUND", "Предмет не найден.");
    const ref = it.prices[key], lo = Math.floor((ref * 50) / 100), hi = Math.floor((ref * 150) / 100);
    if (b.max_price_cents < lo || b.max_price_cents > hi) fail("PRICE_OUT_OF_BAND", `Цена заявки от ${money(lo)} до ${money(hi)}.`);
    charge(ME, b.max_price_cents, "order_reserve");
    const o = { id: ++db.seq, buyer: ME, catalog_item_id: b.catalog_item_id, wear: b.wear, stattrak: !!b.stattrak, max_price_cents: b.max_price_cents, status: "active", item_id: null };
    db.orders.push(o);
    const l = db.lots.find((x) => x.status === "active" && x.seller !== ME && db.items[x.item_id].catalog_id === o.catalog_item_id && db.items[x.item_id].wear === o.wear && !!db.items[x.item_id].stattrak === o.stattrak && x.price <= o.max_price_cents);
    if (l) { executeSale(l, ME, true); if (o.max_price_cents > l.price) ledger(ME, o.max_price_cents - l.price, "order_release"); o.status = "filled"; o.item_id = l.item_id; }
    return o;
  });
  route("DELETE", "/market/orders/:id", (b, q, id) => {
    const o = db.orders.find((x) => x.id === id && x.buyer === ME && x.status === "active");
    if (!o) fail("NOT_FOUND", "Заявка не найдена.");
    o.status = "cancelled"; ledger(ME, o.max_price_cents, "order_release");
    return { id, status: "cancelled" };
  });
  route("GET", "/market/deals", () => ({ deals: db.lots.filter((l) => l.status === "sold" && (l.seller === ME || l.buyer === ME)).slice().reverse().map((l) => ({ ...lotView(l), side: l.seller === ME ? "sell" : "buy" })) }));

  function tradeView(t) {
    return { id: t.id, status: t.status, incoming: t.to_user === ME, from_user: t.from_user, to_user: t.to_user, from_name: U(t.from_user).name, to_name: U(t.to_user).name,
      give: t.give.map((i) => view(db.items[i])), take: t.take.map((i) => view(db.items[i])), value_from_cents: t.value_from, value_to_cents: t.value_to, created_at: t.created_at };
  }
  route("GET", "/trades", () => ({ trades: db.trades.filter((t) => t.from_user === ME || t.to_user === ME).slice().reverse().map(tradeView) }));
  route("GET", "/users/:id", (b, q, id) => {
    if (!U(id)) fail("NOT_FOUND", "Игрок не найден. В демо есть игроки 1001–1005.");
    return { id, name: U(id).name, level: P(id).level, showcase: [], items: itemsOf(id).filter((i) => i.state === "free").map(view) };
  });
  route("POST", "/trades", (b) => {
    const to = +b.to_user;
    if (to === ME) fail("BAD_REQUEST", "Нельзя меняться с собой.");
    if (!U(to)) fail("NOT_FOUND", "Игрок не найден. В демо есть игроки 1001–1005.");
    const mine = owned(ME, b.give.map(Number), { noHold: true, noOverflow: true });
    const theirs = (b.take || []).map((i) => db.items[i]).filter(Boolean);
    const vf = mine.reduce((s, r) => s + price(r.catalog_id, r.wear, r.stattrak), 0), vt = theirs.reduce((s, r) => s + price(r.catalog_id, r.wear, r.stattrak), 0);
    if (Math.max(vf, vt) && (Math.abs(vf - vt) / Math.max(vf, vt)) * 100 > CFG.trades.max_value_gap_pct) fail("PRICE_OUT_OF_BAND", `Слишком большая разница в цене: не больше ${CFG.trades.max_value_gap_pct}%.`);
    const t = { id: ++db.seq, from_user: ME, to_user: to, status: "pending", give: mine.map((r) => r.id), take: theirs.map((r) => r.id), value_from: vf, value_to: vt, created_at: now() };
    db.trades.push(t);
    mine.forEach((r) => (r.state = "trade"));
    return { id: t.id, status: "pending" };
  });
  route("POST", "/trades/:id/:action", (b, q, id, action) => {
    const t = db.trades.find((x) => x.id === id && x.status === "pending");
    if (!t) fail("NOT_FOUND", "Обмен не найден или уже закрыт.");
    const release = () => t.give.forEach((i) => { if (db.items[i].state === "trade") db.items[i].state = "free"; });
    if (action === "decline" || action === "cancel") { release(); t.status = action === "decline" ? "declined" : "cancelled"; return { id, status: t.status }; }
    const theirs = owned(ME, t.take, { noHold: true, noOverflow: true });
    t.give.forEach((i) => Object.assign(db.items[i], { owner: ME, state: "free", hold_until: now() + 86400, prev_owner: t.from_user }));
    theirs.forEach((r) => Object.assign(r, { owner: t.from_user, state: "free", hold_until: now() + 86400 }));
    t.status = "accepted";
    return { id, status: "accepted" };
  });

  route("POST", "/wheel/:kind", (b, q, kind) => {
    const period = kind === "daily" ? today() : week();
    if (db.wheel[`${kind}|${period}`]) fail("DAILY_LIMIT", kind === "daily" ? "Колесо уже крутили. Приходи завтра." : "Недельное колесо уже крутили на этой неделе.");
    if (kind === "weekly" && questDaysWeek(ME) < CFG.wheel.weekly_unlock_days) fail("NOT_ALLOWED_YET", "Недельное колесо откроется, когда выполнишь задания в 5 днях из 7.");
    const r = rng(ME);
    const sectors = CFG.wheel[kind];
    const sid = r.weighted(Object.fromEntries(sectors.map((s) => [s.id, s.p])));
    const s = sectors.find((x) => x.id === sid);
    db.wheel[`${kind}|${period}`] = sid;
    const out = { sector: sid, label: s.label, fair: fairOf(r), ...grant(ME, s, "wheel_" + kind, r) };
    logRoll(ME, "wheel_" + kind, r, { sector: sid });
    return out;
  });
  route("GET", "/quests", () => {
    const rows = (list, period) => list.map((qq) => { const r = questRow(ME, period, qq.code) || { progress: 0, claimed: false };
      return { code: qq.code, title: qq.title, target: qq.target, progress: Math.min(r.progress, qq.target), reward_cents: Math.round(UNIT * qq.reward_cases), tickets: qq.tickets || 0, cosmetic: qq.cosmetic || null, claimed: !!r.claimed }; });
    return { daily: rows(CFG.quests.daily, today()), weekly: rows(CFG.quests.weekly, week()), week_days: questDaysWeek(ME), weekly_wheel_unlock_days: CFG.wheel.weekly_unlock_days };
  });
  route("POST", "/quests/:code/claim", (b, q, code) => {
    const qq = CFG.quests.daily.concat(CFG.quests.weekly).find((x) => x.code === code);
    if (!qq) fail("NOT_FOUND", "Задание не найдено.");
    const weekly = CFG.quests.weekly.includes(qq);
    const r = questRow(ME, weekly ? week() : today(), code);
    if (!r || r.progress < qq.target) fail("BAD_REQUEST", "Задание ещё не выполнено.");
    if (r.claimed) fail("BAD_REQUEST", "Награда уже получена.");
    r.claimed = true;
    const coins = Math.round(UNIT * qq.reward_cases);
    ledger(ME, coins, "quest");
    if (qq.tickets) { tickets(ME); db.tickets[ME] = (db.tickets[ME] || 0) + qq.tickets; }
    if (qq.cosmetic) db.cosmetics[`${ME}|${qq.cosmetic}`] = true;
    return { code, coins_cents: coins, tickets: qq.tickets || 0, cosmetic: qq.cosmetic || null };
  });
  route("GET", "/raffle/today", () => {
    const mine = tickets(ME);
    const prev = db.prev_raffle || (() => {
      const s = randHex(32); const names = DEMO_PLAYERS.map((p) => p[1]);
      db.prev_raffle = { day: "вчера", seed: s, seed_hash: seedHash(s), winners: CFG.raffle.prizes.flatMap((p) => Array(p.count).fill(p.label)).slice(0, 5).map((label, i) => ({ name: names[i], prize: label })) };
      return db.prev_raffle;
    })();
    return { day: db.raffle.day, seed_hash: db.raffle.seed_hash, prizes: CFG.raffle.prizes, my_tickets: mine, total_tickets: mine + 37, draw_hour_msk: CFG.raffle.draw_hour_msk, drawn: false, winners: null, seed: null, previous: prev };
  });
  route("GET", "/leaderboard", (b, q) => {
    questProgress(ME, "rating1", (questRow(ME, today(), "rating1") || { progress: 0 }).progress ? 0 : 1);
    const board = q.get("board") || "capital", scope = q.get("scope") || "friends";
    const ids = scope === "friends" ? [ME, ...friendIds(ME)] : Object.keys(db.users).map(Number);
    const rows = ids.map((uid) => {
      const items = itemsOf(uid), prices = items.map((i) => price(i.catalog_id, i.wear, i.stattrak));
      const value = { capital: prices.reduce((a, x) => a + x, 0), rarest: Math.max(0, ...prices), level: P(uid).xp, collection: new Set(items.map((i) => i.catalog_id)).size }[board];
      return { user_id: uid, name: U(uid).name, level: P(uid).level, value, me: uid === ME };
    }).sort((a, x) => x.value - a.value).map((r, i) => ({ ...r, place: i + 1 }));
    return { board, scope, rows, me: rows.find((r) => r.me) };
  });
  route("GET", "/friends", () => {
    const refs = db.refs.filter((r) => r[0] === ME);
    return { invited: refs.map((r) => ({ id: r[1], name: U(r[1]).name, qualified: !!r[3], spins: r[3] ? 24 : 4 })), qualified: refs.filter((r) => r[3]).length,
      tiers: Object.entries(CFG.referral.rewards).map(([k, v]) => ({ friends: +k, ...v, granted: !!db.tiers[`${ME}|${k}`] || +k <= refs.filter((r) => r[3]).length })),
      qualify_spins: CFG.referral.qualify_spins, friends: friendIds(ME) };
  });
  function duelView(d) {
    return { id: d.id, status: d.status, p1: d.p1, p2: d.p2, p1_name: U(d.p1).name, p2_name: d.p2 ? U(d.p2).name : "", p1_items: d.p1_items.map((i) => view(db.items[i])),
      p2_items: (d.p2_items || []).map((i) => view(db.items[i])), p1_power: d.p1_power, p2_power: d.p2_power, winner: d.winner, friend_id: d.friend_id, created_at: d.created_at };
  }
  route("GET", "/duels", () => ({ duels: db.duels.slice().reverse().map(duelView), left_today: CFG.duels.casual.per_day - counter(ME, "duel") }));
  route("POST", "/duels", (b) => {
    if (b.item_ids.length !== 3) fail("BAD_REQUEST", "Выбери 3 предмета для дуэли.");
    if (counter(ME, "duel") >= CFG.duels.casual.per_day) fail("DAILY_LIMIT", "Дуэлей на сегодня больше нет: лимит 5 в сутки.");
    const rows = owned(ME, b.item_ids.map(Number), { allowLocked: true, noOverflow: true });
    const d = { id: ++db.seq, p1: ME, p2: null, friend_id: b.friend_id || null, p1_items: rows.map((r) => r.id), p2_items: null, status: "open", created_at: now() };
    rows.forEach((r) => (r.state = "duel"));
    db.duels.push(d);
    bump(ME, "duel");
    // В демо соперник находится через несколько секунд.
    setTimeout(() => {
      if (d.status !== "open") return;
      const opp = itemsOf(1001).filter((i) => i.state === "free").slice(0, 3);
      resolveDuel(d, 1001, opp, rng(1001));
      save();
    }, 6000);
    return { id: d.id, status: "open" };
  });
  function resolveDuel(d, p2, mine, r) {
    const [lo, hi] = CFG.duels.casual.rand;
    const power = (rows) => rows.reduce((s, x) => s + price(x.catalog_id, x.wear, x.stattrak) * (lo + (hi - lo) * r.random()), 0);
    const theirs = d.p1_items.map((i) => db.items[i]);
    const a = power(theirs), bb = power(mine);
    const winner = a >= bb ? d.p1 : p2;
    theirs.forEach((x) => { if (x.state === "duel") x.state = "free"; });
    const prize = Math.round(UNIT * CFG.duels.casual.win_cases);
    ledger(winner, prize, "duel_win");
    Object.assign(d, { p2, p2_items: mine.map((x) => x.id), p1_power: Math.round(a), p2_power: Math.round(bb), winner, status: "done" });
    return { id: d.id, winner, you_won: winner === ME, p1_power: Math.round(a), p2_power: Math.round(bb), prize_cents: prize, fair: fairOf(r) };
  }
  route("POST", "/duels/:id/join", (b, q, id) => {
    const d = db.duels.find((x) => x.id === id && x.status === "open");
    if (!d) fail("NOT_FOUND", "Дуэль уже закончилась.");
    if (d.p1 === ME) fail("BAD_REQUEST", "Эта дуэль не для тебя.");
    if (counter(ME, "duel") >= CFG.duels.casual.per_day) fail("DAILY_LIMIT", "Дуэлей на сегодня больше нет: лимит 5 в сутки.");
    const mine = owned(ME, b.item_ids.map(Number), { allowLocked: true, noOverflow: true });
    bump(ME, "duel");
    const r = rng(ME);
    const res = resolveDuel(d, ME, mine, r);
    logRoll(ME, "duel", r, { p1: res.p1_power, p2: res.p2_power });
    return res;
  });
  route("POST", "/duels/:id/cancel", (b, q, id) => {
    const d = db.duels.find((x) => x.id === id && x.p1 === ME && x.status === "open");
    if (!d) fail("NOT_FOUND", "Дуэль не найдена.");
    d.status = "cancelled"; d.p1_items.forEach((i) => (db.items[i].state = "free"));
    return { id, status: "cancelled" };
  });
  route("GET", "/cosmetics", () => ({ items: Object.entries(CFG.cosmetics).map(([code, v]) => ({ code, ...v, owned: !!db.cosmetics[`${ME}|${code}`] || (code === "badge_vip" && isVip(ME)), equipped: P(ME).frame === code })) }));
  route("POST", "/cosmetics/equip", (b) => { if (b.code && !db.cosmetics[`${ME}|${b.code}`]) fail("NOT_FOUND", "Этой рамки у тебя нет."); P(ME).frame = b.code || null; return { frame: P(ME).frame }; });
  route("GET", "/feed", () => ({ feed: db.feed.slice(-12).reverse().map((i) => db.items[i]).filter((r) => r && r.state !== "gone" && r.owner).map((r) => ({ ...view(r), owner_name: U(r.owner).name })) }));
  route("POST", "/share/:id", (b, q, id) => ({ image_url: null, text: `Мне выпал ${view(db.items[id]).name}!`, link: "https://t.me/cs2_cases_bot?start=ref_1" }));

  function monthSpent() { const m = msk().toISOString().slice(0, 7); return db.payments.filter((x) => x.month === m).reduce((s, x) => s + x.stars, 0); }
  function checkProduct(code) {
    const s = CFG.stars, prod = s.products[code];
    if (!prod) fail("NOT_FOUND", "Товар не найден.");
    if (monthSpent() + prod.stars > s.month_cap) fail("STARS_MONTH_CAP", `Месячный лимит ${s.month_cap} ⭐ исчерпан.`);
    const g = prod.grants, p = P(ME), lim = prod.limits || {};
    if (g.attempts && p.paid_attempts + g.attempts > s.attempts_stock_max) fail("DAILY_LIMIT", `Запас попыток не больше ${s.attempts_stock_max}.`);
    if (g.luck && p.luck_left && p.luck_mult >= g.luck.mult) fail("BAD_REQUEST", "Удача уже действует. Усиления не складываются.");
    const bought = db.payments.filter((x) => x.code === code).length;
    if (lim.once && bought) fail("BAD_REQUEST", "Этот набор можно купить один раз.");
    if (lim.max_times && bought >= lim.max_times) fail("BAD_REQUEST", "Больше этот товар купить нельзя.");
    return prod;
  }
  route("GET", "/shop", () => {
    const products = Object.entries(CFG.stars.products).map(([code, p]) => {
      try { checkProduct(code); return { code, title: p.title, stars: p.stars, grants: p.grants, available: true, reason: null }; }
      catch (e) { return { code, title: p.title, stars: p.stars, grants: p.grants, available: false, reason: e.message }; }
    });
    return { enabled: true, products, month_spent: monthSpent(), month_cap: CFG.stars.month_cap, warn: monthSpent() >= CFG.stars.month_cap * 0.8 };
  });
  route("POST", "/shop/invoice", (b) => {
    const prod = checkProduct(b.product);
    const g = prod.grants, p = P(ME);
    if (g.attempts) p.paid_attempts = Math.min(CFG.stars.attempts_stock_max, p.paid_attempts + g.attempts);
    if (g.luck) { p.luck_mult = g.luck.mult; p.luck_left = g.luck.spins; }
    if (g.vip_days) p.vip_until = Math.max(now(), p.vip_until || 0) + g.vip_days * 86400;
    if (g.slots) p.slots = Math.min(CFG.inventory.slots_max, p.slots + g.slots);
    db.payments.push({ code: b.product, stars: prod.stars, month: msk().toISOString().slice(0, 7) });
    return { demo: true };
  });

  // ---------- вход ----------
  function match(method, path) {
    for (const key of Object.keys(R)) {
      const [m, pattern] = key.split(" ");
      if (m !== method) continue;
      const names = [];
      const re = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, n) => { names.push(n); return "([^/]+)"; }) + "$");
      const hit = path.match(re);
      if (hit) return [R[key], hit.slice(1).map((v) => (/^\d+$/.test(v) ? +v : v))];
    }
    return [null, []];
  }

  window.MockApi = {
    async handle(method, fullPath, body) {
      if (!db) { db = load(); if (!db) seedWorld(); }
      const [path, query] = fullPath.split("?");
      const [fn, params] = match(method, path);
      if (!fn) fail("NOT_FOUND", "Нет такого действия.");
      await new Promise((r) => setTimeout(r, 50));
      const snapshot = JSON.stringify(db);
      try {
        const res = fn(body || {}, new URLSearchParams(query || ""), ...params);
        save();
        return JSON.parse(JSON.stringify(res));
      } catch (e) {
        db = JSON.parse(snapshot); // как ROLLBACK: ошибка не оставляет следов
        throw e;
      }
    },
    reset() { try { localStorage.removeItem(KEY); } catch (e) { /* без хранилища */ } db = null; },
  };
})();
