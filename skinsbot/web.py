"""HTTP API мини-приложения (docs/API.md), админка и статика webapp/."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from aiohttp import web

from . import rules, totp
from .auth import AuthError, validate_init_data
from .config import ROOT, Config
from .service import Game, GameError

STATUS = {"UNAUTHORIZED": 401, "RATE_LIMIT": 429, "NOT_FOUND": 404, "CONFLICT": 409, "CONFIRM_REQUIRED": 409,
          "CONFIG_CHANGED": 409, "DISABLED": 403, "FORBIDDEN": 403}


def public_config(game: Game) -> dict:
    c = rules.CFG
    cat = game.catalog

    def brief(item_id):
        it = cat.items[item_id]
        prices = list(it["prices"].values())
        return {"id": item_id, "name": it["name"], "image_url": it["image"], "tier": it["tier"],
                "min_cents": min(prices), "max_cents": max(prices)}

    levels = []
    for key, lvl in c["levels"].items():
        if key == "common":
            levels.append({"id": key, "name": lvl["name"], "what": lvl["what"], "from": lvl["from"], "odds": None})
            continue
        lv = cat.levels[key]
        odds = rules.level_odds(key)
        goal = lvl["goal"]
        levels.append({"id": key, "name": lvl["name"], "what": lvl["what"], "from": lvl["from"], "price_cents": lv["price_cents"],
                       "ev_cents": lv["ev_cents"], "odds": [{"tier": t, "p": p} for t, p in odds.items()],
                       "goal": {"label": goal["label"], "p": sum(p for t, p in odds.items() if rules.at_least(t, goal["from"]))},
                       "pool_sizes": {t: len(ids) for t, ids in lv["pools"].items()}})
    case_odds = rules.case_odds()
    return {
        "version": rules.VERSION,
        "generated_at": cat.generated_at,
        "usd_rub": cat.usd_rub,
        "tiers": c["tiers"],
        "currency": c["currency"],
        "case_odds": [{"tier": t, "p": p, "one_in": max(1, round(1 / p))} for t, p in case_odds.items()],
        "cases": [{"id": cs["id"], "name": cs["name"], "image_url": cs["image"], "price_cents": cs["price_cents"],
                   "ev_cents": cs["ev_cents"], "free": cs["id"] == cat.free_case_id,
                   "pools": {t: [brief(i) for i in ids] for t, ids in cs["pools"].items()}} for cs in cat.case_list],
        "levels": levels,
        "wear": c["wear"],
        "stattrak": c["stattrak"],
        "pity": c["pity"],
        "spin": c["spin"],
        "energy": c["energy"],
        "upgrader": c["upgrader"],
        "contracts": c["contracts"],
        "market": {k: v for k, v in c["market"].items()},
        "trades": c["trades"],
        "wheel": {"daily": c["wheel"]["daily"], "weekly": c["wheel"]["weekly"], "weekly_unlock_days": c["wheel"]["weekly_unlock_days"]},
        "duels": c["duels"],
        "referral": c["referral"],
        "cosmetics": c["cosmetics"],
        "sell_system_pct": c["sell_system_pct"],
        "undo_sell": c["undo_sell"],
        "inventory": c["inventory"],
        "notifications": c["notifications"],
        "unit_cents": cat.unit_cents,
        "free_case_id": cat.free_case_id,
        "shop_enabled": c["stars"]["shop_enabled"],
    }


def create_app(game: Game, config: Config, bot=None, share=None) -> web.Application:
    hits: dict[int, deque] = defaultdict(deque)
    spin_hits: dict[int, deque] = defaultdict(deque)
    rl = rules.CFG["rate_limit"]

    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            return await handler(request)
        except GameError as e:
            return web.json_response({"error": {"code": e.code, "message": e.message, **e.extra}}, status=STATUS.get(e.code, 400),
                                     dumps=lambda d: json.dumps(d, ensure_ascii=False))
        except AuthError as e:
            return web.json_response({"error": {"code": "UNAUTHORIZED", "message": "Открой игру через бота в Telegram.",
                                                "detail": str(e)}}, status=401)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return web.json_response({"error": {"code": "BAD_REQUEST", "message": "Неверный запрос. Обнови приложение."}},
                                     status=400)

    def limit(q: deque, per_sec: int) -> None:
        now = time.monotonic()
        while q and now - q[0] > 1:
            q.popleft()
        if len(q) >= per_sec:
            raise GameError("RATE_LIMIT", "Слишком быстро. Подожди секунду.")
        q.append(now)

    def tg_user(request: web.Request) -> dict:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("tma "):
            return validate_init_data(auth[4:], config.bot_token)
        if config.dev_mode:
            return {"id": int(request.headers.get("X-Dev-User", "1")), "first_name": "Dev", "language_code": "ru"}
        raise AuthError("нет initData")

    def current_user(request: web.Request) -> int:
        user = tg_user(request)
        uid = int(user["id"])
        limit(hits[uid], rl["per_second"])
        if request.method in ("POST", "DELETE") and not request.headers.get("Idempotency-Key"):
            raise GameError("BAD_REQUEST", "Нет заголовка Idempotency-Key. Обнови приложение.")
        referrer = None
        start = user.get("start_param") or ""
        if start.startswith("ref_") and start[4:].isdigit():
            referrer = int(start[4:])
        game.ensure_user(uid, user.get("first_name") or "", user.get("username"), user.get("language_code"), referrer)
        return uid

    def key(request) -> str | None:
        return request.headers.get("Idempotency-Key")

    async def body(request) -> dict:
        if not request.can_read_body:
            return {}
        return await request.json()

    def j(data, status=200):
        return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False))

    r = web.RouteTableDef()

    # --- профиль и конфиг -----------------------------------------------------------------
    @r.get("/v1/config")
    async def config_(request):
        return j(public_config(game))

    @r.get("/v1/me")
    async def me(request):
        uid = current_user(request)
        data = game.me(uid)
        if config.bot_username:
            data["invite_link"] = f"https://t.me/{config.bot_username}?start=ref_{uid}"
            data["app_link"] = f"https://t.me/{config.bot_username}/{config.app_short_name}?startapp=ref_{uid}" \
                if config.app_short_name else data["invite_link"]
        return j(data)

    @r.put("/v1/me/country")
    async def country(request):
        uid = current_user(request)
        return j(game.set_country(uid, (await body(request))["country"]))

    @r.post("/v1/me/last-drop/ack")
    async def ack(request):
        return j(game.ack_last_drop(current_user(request)))

    @r.get("/v1/users/{id}")
    async def user_public(request):
        current_user(request)
        uid = int(request.match_info["id"])
        row = game.conn.execute("SELECT u.id, u.name, p.level, p.frame FROM users u JOIN players p ON p.user_id=u.id WHERE u.id=?",
                                (uid,)).fetchone()
        if not row:
            raise GameError("NOT_FOUND", "Игрок не найден.")
        return j({**dict(row), "showcase": game.showcase_view(uid),
                  "items": [i for i in game.inventory(uid)["items"] if i["state"] == "free" and not i["hold_until"]
                            and not i["overflow_until"]][:120]})

    # --- спин и честная игра ----------------------------------------------------------------
    @r.post("/v1/spin")
    async def spin(request):
        uid = current_user(request)
        limit(spin_hits[uid], rl["spins_per_second"])
        b = await body(request)
        return j(game.spin(uid, b.get("case_id"), b.get("level"), b.get("source", "coin"), int(b.get("count", 1)),
                           bool(b.get("confirmed")), key(request)))

    @r.get("/v1/fair")
    async def fair(request):
        return j(game.fair(current_user(request)))

    @r.post("/v1/fair/rotate")
    async def rotate(request):
        uid = current_user(request)
        return j(game.rotate_seed(uid, (await body(request)).get("client_seed")))

    # --- коллекция -------------------------------------------------------------------------------
    @r.get("/v1/inventory")
    async def inventory(request):
        q = request.query
        return j(game.inventory(current_user(request), q.get("state"), q.get("tier"), q.get("q"), q.get("sort", "new")))

    @r.get("/v1/items/{id}")
    async def item(request):
        return j(game.item_card(current_user(request), int(request.match_info["id"])))

    @r.post("/v1/items/sell")
    async def sell(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.sell(uid, [int(i) for i in b["item_ids"]], bool(b.get("confirmed")), key(request)))

    @r.post("/v1/sales/{id}/undo")
    async def undo(request):
        return j(game.undo_sale(current_user(request), int(request.match_info["id"]), key(request)))

    @r.post("/v1/items/{id}/lock")
    async def lock(request):
        return j(game.lock(current_user(request), int(request.match_info["id"]), True))

    @r.post("/v1/items/{id}/unlock")
    async def unlock(request):
        uid = current_user(request)
        return j(game.lock(uid, int(request.match_info["id"]), False, bool((await body(request)).get("confirm"))))

    @r.post("/v1/items/sell-duplicates/quote")
    async def dupes(request):
        return j(game.duplicates_quote(current_user(request)))

    @r.post("/v1/items/sell-cheaper/quote")
    async def cheaper(request):
        uid = current_user(request)
        return j(game.sell_cheaper(uid, int((await body(request))["max_cents"])))

    @r.post("/v1/showcase")
    async def showcase(request):
        uid = current_user(request)
        return j(game.set_showcase(uid, [int(i) for i in (await body(request)).get("item_ids", [])]))

    # --- контракты и апгрейдер ---------------------------------------------------------------------
    @r.post("/v1/contracts")
    async def contracts(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.contract(uid, [int(i) for i in b["item_ids"]], bool(b.get("confirmed")), key(request)))

    @r.get("/v1/upgrades/targets")
    async def targets(request):
        current_user(request)
        mult = request.query.get("mult")
        return j({"targets": game.upgrade_targets(int(request.query["stake_cents"]), float(mult) if mult else None)})

    @r.post("/v1/upgrades/quote")
    async def upquote(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.upgrade_quote(uid, [int(i) for i in b.get("stake_item_ids", [])], int(b.get("stake_coins_cents", 0)), b["target"]))

    @r.post("/v1/upgrades")
    async def upgrade(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.upgrade(uid, [int(i) for i in b.get("stake_item_ids", [])], int(b.get("stake_coins_cents", 0)), b["target"],
                              b.get("quote_chance_pct"), bool(b.get("confirmed")), key(request)))

    # --- рынок и обмены --------------------------------------------------------------------------
    @r.get("/v1/market/lots")
    async def lots(request):
        q = request.query
        return j(game.market_lots(current_user(request), q.get("currency", "coins"), q.get("tier"), q.get("q"),
                                  q.get("sort", "new"), q.get("mine") == "1"))

    @r.post("/v1/market/lots")
    async def lot_create(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.create_lot(uid, int(b["item_id"]), int(b["price"]), b.get("currency", "coins"), key(request)))

    @r.delete("/v1/market/lots/{id}")
    async def lot_cancel(request):
        return j(game.cancel_lot(current_user(request), int(request.match_info["id"]), key(request)))

    @r.post("/v1/market/lots/{id}/buy")
    async def lot_buy(request):
        return j(game.buy_lot(current_user(request), int(request.match_info["id"]), key(request)))

    @r.post("/v1/market/lots/{id}/invoice")
    async def lot_invoice(request):
        current_user(request)
        raise GameError("DISABLED", "Рынок за Stars откроется после проверки юристом.")

    @r.get("/v1/market/orders")
    async def orders(request):
        return j(game.buy_orders(current_user(request)))

    @r.post("/v1/market/orders")
    async def order_create(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.create_order(uid, b["catalog_item_id"], b.get("wear"), bool(b.get("stattrak")), int(b["max_price_cents"]),
                                   key(request)))

    @r.delete("/v1/market/orders/{id}")
    async def order_cancel(request):
        return j(game.cancel_order(current_user(request), int(request.match_info["id"]), key(request)))

    @r.get("/v1/market/deals")
    async def deals(request):
        return j(game.market_deals(current_user(request)))

    @r.get("/v1/trades")
    async def trades(request):
        return j(game.trades(current_user(request)))

    @r.post("/v1/trades")
    async def trade_create(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.propose_trade(uid, int(b["to_user"]), [int(i) for i in b.get("give", [])],
                                    [int(i) for i in b.get("take", [])], key(request)))

    @r.post("/v1/trades/{id}/{action:accept|decline|cancel}")
    async def trade_answer(request):
        uid = current_user(request)
        return j(game.answer_trade(uid, int(request.match_info["id"]), request.match_info["action"], key(request)))

    # --- удержание и социальное ----------------------------------------------------------------------
    @r.post("/v1/wheel/{kind:daily|weekly}")
    async def wheel(request):
        return j(game.wheel(current_user(request), request.match_info["kind"], key(request)))

    @r.get("/v1/quests")
    async def quests(request):
        return j(game.quests(current_user(request)))

    @r.post("/v1/quests/{code}/claim")
    async def quest_claim(request):
        return j(game.claim_quest(current_user(request), request.match_info["code"], key(request)))

    @r.get("/v1/raffle/today")
    async def raffle(request):
        return j(game.raffle_today(current_user(request)))

    @r.get("/v1/leaderboard")
    async def leaderboard(request):
        uid = current_user(request)
        game.visit_rating(uid)
        return j(game.leaderboard(uid, request.query.get("board", "capital"), request.query.get("scope", "friends")))

    @r.get("/v1/friends")
    async def friends(request):
        return j(game.friends(current_user(request)))

    @r.get("/v1/duels")
    async def duels(request):
        return j(game.duels(current_user(request)))

    @r.post("/v1/duels")
    async def duel_create(request):
        uid = current_user(request)
        b = await body(request)
        return j(game.create_duel(uid, [int(i) for i in b["item_ids"]], b.get("friend_id"), key(request)))

    @r.post("/v1/duels/{id}/join")
    async def duel_join(request):
        uid = current_user(request)
        return j(game.join_duel(uid, int(request.match_info["id"]), [int(i) for i in (await body(request))["item_ids"]], key(request)))

    @r.post("/v1/duels/{id}/cancel")
    async def duel_cancel(request):
        return j(game.cancel_duel(current_user(request), int(request.match_info["id"])))

    @r.get("/v1/cosmetics")
    async def cosmetics(request):
        return j(game.cosmetics(current_user(request)))

    @r.post("/v1/cosmetics/equip")
    async def equip(request):
        uid = current_user(request)
        return j(game.equip(uid, (await body(request)).get("code")))

    @r.get("/v1/feed")
    async def feed(request):
        current_user(request)
        return j({"feed": game.feed()})

    @r.post("/v1/share/{item_id}")
    async def share_prepare(request):
        uid = current_user(request)
        item_id = int(request.match_info["item_id"])
        view = game.item_card(uid, item_id)
        base = config.webapp_url.rstrip("/")
        token = share.sign(item_id) if share else ""
        return j({"image_url": f"{base}/v1/share/{item_id}.png?t={token}" if base else None,
                  "text": f"Мне выпал {view['name']} в «{config.bot_name}»!",
                  "link": f"https://t.me/{config.bot_username}?start=ref_{uid}" if config.bot_username else None})

    @r.get("/v1/share/{item_id}.png")
    async def share_png(request):
        if not share:
            raise GameError("DISABLED", "Картинки для шеринга выключены.")
        item_id = int(request.match_info["item_id"])
        if not share.check(item_id, request.query.get("t", "")):
            raise GameError("NOT_FOUND", "Картинка не найдена.")
        row = game._item(item_id)
        if not row:
            raise GameError("NOT_FOUND", "Картинка не найдена.")
        png = await share.render(game.item_view(row))
        return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    # --- магазин Stars ---------------------------------------------------------------------------------
    @r.get("/v1/shop")
    async def shop(request):
        return j(game.shop(current_user(request)))

    @r.post("/v1/shop/invoice")
    async def invoice(request):
        uid = current_user(request)
        product = (await body(request))["product"]
        inv = game.create_invoice(uid, product)
        if not bot:
            raise GameError("DISABLED", "Оплата работает только в Telegram.")
        from aiogram.types import LabeledPrice

        link = await bot.create_invoice_link(title=inv["title"][:32], description=inv["title"], payload=inv["payload"],
                                             currency="XTR", prices=[LabeledPrice(label=inv["title"][:32], amount=inv["stars"])])
        return j({"invoice_link": link})

    # --- админка ----------------------------------------------------------------------------------------
    def admin_user(request) -> int:
        user = tg_user(request)
        uid = int(user["id"])
        if uid not in config.admin_ids:
            raise GameError("FORBIDDEN", "Нет доступа.")
        if config.admin_totp_secret:
            if not totp.verify(config.admin_totp_secret, request.headers.get("X-Admin-Code", "")):
                raise GameError("FORBIDDEN", "Неверный код второго фактора.")
        elif not config.dev_mode:
            raise GameError("FORBIDDEN", "Задай ADMIN_TOTP_SECRET, чтобы включить админку.")
        return uid

    @r.get("/v1/admin/economy")
    async def a_economy(request):
        admin_user(request)
        return j(game.economy(int(request.query.get("days", "14"))))

    @r.get("/v1/admin/configs")
    async def a_configs(request):
        admin_user(request)
        return j(game.configs())

    @r.post("/v1/admin/configs/check")
    async def a_check(request):
        admin_user(request)
        return j(game.diff_config(await body(request)))

    @r.post("/v1/admin/configs")
    async def a_save(request):
        aid = admin_user(request)
        b = await body(request)
        return j(game.save_config(aid, b["config"], bool(b.get("activate"))))

    @r.get("/v1/admin/items/{id}")
    async def a_item(request):
        admin_user(request)
        return j(game.admin_item(int(request.match_info["id"])))

    @r.post("/v1/admin/items/{id}/{action:freeze|unfreeze}")
    async def a_freeze(request):
        aid = admin_user(request)
        return j(game.freeze(aid, int(request.match_info["id"]), request.match_info["action"] == "freeze"))

    @r.post("/v1/admin/sales/{id}/rollback")
    async def a_rollback(request):
        aid = admin_user(request)
        return j(game.rollback_sale(aid, int(request.match_info["id"])))

    @r.get("/v1/admin/users/{id}")
    async def a_user(request):
        admin_user(request)
        return j(game.admin_user(int(request.match_info["id"])))

    @r.post("/v1/admin/users/{id}/{action:ban|unban}")
    async def a_ban(request):
        aid = admin_user(request)
        b = await body(request)
        return j(game.ban(aid, int(request.match_info["id"]), request.match_info["action"] == "ban", b.get("reason", "")))

    @r.get("/v1/admin/fraud")
    async def a_fraud(request):
        admin_user(request)
        return j(game.fraud())

    @r.post("/v1/admin/fraud/{id}/resolve")
    async def a_resolve(request):
        aid = admin_user(request)
        return j(game.resolve_flag(aid, int(request.match_info["id"])))

    @r.get("/v1/admin/payments")
    async def a_payments(request):
        admin_user(request)
        return j(game.payments())

    @r.post("/v1/admin/refunds/{id}")
    async def a_refund(request):
        aid = admin_user(request)
        info = game.refund_payment(int(request.match_info["id"]))
        if bot and info.get("charge_id"):
            await bot.refund_star_payment(user_id=info["user_id"], telegram_payment_charge_id=info["charge_id"])
        with game.tx():
            game._audit(aid, "refund", {"payment_id": int(request.match_info["id"])}, None, info)
        return j(info)

    async def index(request):
        return web.FileResponse(ROOT / "webapp" / "index.html", headers={"Cache-Control": "no-cache"})

    async def admin_page(request):
        return web.FileResponse(ROOT / "webapp" / "admin.html", headers={"Cache-Control": "no-cache"})

    app = web.Application(middlewares=[errors], client_max_size=2 * 1024 * 1024)
    app.add_routes(r)
    app.router.add_get("/", index)
    app.router.add_get("/admin", admin_page)
    app.router.add_static("/", ROOT / "webapp")
    return app
