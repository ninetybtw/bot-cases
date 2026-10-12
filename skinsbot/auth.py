"""Проверка подписи Telegram initData (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE = 24 * 3600


class AuthError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict:
    """Возвращает объект user из initData или бросает AuthError."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("нет подписи")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("подпись не сходится")
    auth_date = int(pairs.get("auth_date", "0"))
    if (now or time.time()) - auth_date > MAX_AGE:
        raise AuthError("данные устарели")
    user = json.loads(pairs.get("user", "{}"))
    if "id" not in user:
        raise AuthError("нет пользователя")
    user["start_param"] = pairs.get("start_param")
    return user


def sign_init_data(fields: dict, bot_token: str) -> str:
    """Для тестов: собрать подписанную строку initData."""
    from urllib.parse import urlencode

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})
