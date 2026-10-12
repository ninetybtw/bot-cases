"""HTTP API: подпись initData, Idempotency-Key, формат ошибок, админка со вторым фактором."""

import json
import time
from pathlib import Path

import pytest

from skinsbot import totp
from skinsbot.auth import AuthError, sign_init_data, validate_init_data
from skinsbot.config import Config
from skinsbot.service import Catalog, Game
from skinsbot.web import create_app

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "123:ABC"


def init_data(uid=42, name="Ann", lang="ru"):
    return sign_init_data({"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": name,
                                                                                    "language_code": lang})}, TOKEN)


@pytest.fixture
def client(aiohttp_client, tmp_path):
    cfg = Config(bot_token=TOKEN, webapp_url="", host="", port=0, db_path=str(tmp_path / "a.sqlite3"),
                 catalog_path=ROOT / "data" / "catalog.json", dev_mode=False, bot_name="Кейсы CS2", bot_username="cs2_cases_bot",
                 app_short_name="", admin_ids={42}, admin_totp_secret="JBSWY3DPEHPK3PXP")
    game = Game(cfg.db_path, Catalog(cfg.catalog_path))
    return aiohttp_client(create_app(game, cfg))


def test_init_data_signature():
    data = sign_init_data({"auth_date": "1700000000", "user": json.dumps({"id": 42})}, TOKEN)
    assert validate_init_data(data, TOKEN, now=1700000100)["id"] == 42
    with pytest.raises(AuthError):
        validate_init_data(data, "123:XYZ", now=1700000100)
    with pytest.raises(AuthError):
        validate_init_data(data, TOKEN, now=1700000000 + 2 * 86400)


async def test_unauthorized(client):
    c = await client
    r = await c.get("/v1/me")
    assert r.status == 401
    assert (await r.json())["error"]["code"] == "UNAUTHORIZED"


async def test_me_and_spin(client):
    c = await client
    h = {"Authorization": f"tma {init_data(lang='kk')}"}
    me = await (await c.get("/v1/me", headers=h)).json()
    assert me["currency"]["symbol"] == "₸" and me["invite_link"].endswith("ref_42")
    cfg = await (await c.get("/v1/config")).json()
    body = {"case_id": cfg["free_case_id"], "source": "free", "count": 1}
    r = await c.post("/v1/spin", json=body, headers=h)
    assert r.status == 400 and (await r.json())["error"]["message"].startswith("Нет заголовка")
    h2 = {**h, "Idempotency-Key": "abc"}
    r1 = await (await c.post("/v1/spin", json=body, headers=h2)).json()
    r2 = await (await c.post("/v1/spin", json=body, headers=h2)).json()
    assert r1 == r2 and len(r1["drops"]) == 1


async def test_admin_needs_totp(client):
    c = await client
    h = {"Authorization": f"tma {init_data()}"}
    await c.get("/v1/me", headers=h)
    assert (await c.get("/v1/admin/economy", headers=h)).status == 403
    ok = await c.get("/v1/admin/economy", headers={**h, "X-Admin-Code": totp.code("JBSWY3DPEHPK3PXP")})
    assert ok.status == 200 and "rtp" in await ok.json()
    other = {"Authorization": f"tma {init_data(uid=7)}", "X-Admin-Code": totp.code("JBSWY3DPEHPK3PXP")}
    await c.get("/v1/me", headers=other)
    assert (await c.get("/v1/admin/economy", headers=other)).status == 403
