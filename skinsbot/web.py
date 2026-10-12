"""HTTP API мини-приложения и раздача статики webapp/."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from aiohttp import web

from .auth import AuthError, validate_init_data
from .config import ROOT, Config
from .service import Game, GameError

RATE_LIMIT = 30  # запросов за 10 секунд на игрока
RATE_WINDOW = 10


def create_app(game: Game, config: Config) -> web.Application:
    hits: dict[int, deque] = defaultdict(deque)

    @web.middleware
    async def errors(request: web.Request, handler):
        try:
            return await handler(request)
        except GameError as e:
            return web.json_response({"error": str(e), "code": e.code, **e.extra}, status=400)
        except AuthError as e:
            return web.json_response({"error": "Открой игру через бота в Telegram.", "code": "auth", "detail": str(e)},
                                     status=401)

    def current_user(request: web.Request) -> int:
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        if init_data:
            user = validate_init_data(init_data, config.bot_token)
        elif config.dev_mode:
            user = {"id": int(request.headers.get("X-Dev-User", "1")), "first_name": "Dev"}
        else:
            raise AuthError("нет initData")
        uid = int(user["id"])
        q, now = hits[uid], time.monotonic()
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise GameError("Слишком быстро. Подожди пару секунд.", "rate")
        q.append(now)
        referrer = None
        start = user.get("start_param") or ""
        if start.startswith("ref_") and start[4:].isdigit():
            referrer = int(start[4:])
        game.ensure_user(uid, user.get("first_name") or user.get("username") or "", referrer)
        return uid

    async def body(request: web.Request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    routes = web.RouteTableDef()

    @routes.get("/api/catalog")
    async def catalog(request):
        return web.json_response(game.cases_view(), headers={"Cache-Control": "public, max-age=3600"})

    @routes.get("/api/state")
    async def state(request):
        uid = current_user(request)
        data = game.state(uid)
        if config.bot_username:
            data["user"]["invite"] = f"https://t.me/{config.bot_username}?start=ref_{uid}"
        return web.json_response(data)

    @routes.post("/api/spin")
    async def spin(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.spin(uid, b.get("case_id", ""), int(b.get("count", 1)), bool(b.get("free")),
                                           b.get("idem_key")))

    @routes.post("/api/sell")
    async def sell(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.sell(uid, [int(i) for i in b.get("item_ids", [])], b.get("idem_key")))

    @routes.post("/api/lock")
    async def lock(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.lock(uid, int(b.get("item_id", 0)), bool(b.get("locked"))))

    @routes.get("/api/upgrade/targets")
    async def targets(request):
        current_user(request)
        stake = int(request.query.get("stake", "0"))
        mult = request.query.get("mult")
        return web.json_response({"targets": game.upgrade_targets(stake, float(mult) if mult else None)})

    @routes.post("/api/upgrade")
    async def upgrade(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.upgrade(uid, [int(i) for i in b.get("item_ids", [])], int(b.get("coins", 0)),
                                              b.get("target_id", ""), b.get("target_key", ""), b.get("idem_key")))

    @routes.post("/api/contract")
    async def contract(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.contract(uid, [int(i) for i in b.get("item_ids", [])], b.get("idem_key")))

    @routes.post("/api/wheel")
    async def wheel(request):
        return web.json_response(game.wheel(current_user(request)))

    @routes.post("/api/task")
    async def task(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.claim_task(uid, b.get("task_id", "")))

    @routes.post("/api/fair/rotate")
    async def rotate(request):
        uid, b = current_user(request), await body(request)
        return web.json_response(game.rotate_seed(uid, b.get("client_seed")))

    async def index(request):
        return web.FileResponse(ROOT / "webapp" / "index.html")

    app = web.Application(middlewares=[errors])
    app.add_routes(routes)
    app.router.add_get("/", index)
    app.router.add_static("/", ROOT / "webapp")
    return app
