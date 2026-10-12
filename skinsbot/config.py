from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


@dataclass
class Config:
    bot_token: str
    webapp_url: str
    host: str
    port: int
    db_path: str
    catalog_path: Path
    dev_mode: bool
    bot_name: str
    bot_username: str
    app_short_name: str
    admin_ids: set[int] = field(default_factory=set)
    admin_totp_secret: str = ""
    share_secret: str = ""
    price_update: bool = False
    raffle_channel: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        e = os.environ.get
        return cls(
            bot_token=e("BOT_TOKEN", ""),
            webapp_url=e("WEBAPP_URL", ""),
            host=e("HOST", "0.0.0.0"),
            port=int(e("PORT", "8080")),
            db_path=e("DB_PATH", str(ROOT / "data" / "game.sqlite3")),
            catalog_path=Path(e("CATALOG_PATH", str(ROOT / "data" / "catalog.json"))),
            dev_mode=e("DEV_MODE", "0") == "1",
            bot_name=e("BOT_NAME", "Кейсы CS2"),
            bot_username=e("BOT_USERNAME", "").lstrip("@"),
            app_short_name=e("APP_SHORT_NAME", ""),
            admin_ids={int(x) for x in e("ADMIN_IDS", "").replace(" ", "").split(",") if x},
            admin_totp_secret=e("ADMIN_TOTP_SECRET", ""),
            share_secret=e("SHARE_SECRET", "") or e("BOT_TOKEN", "dev"),
            price_update=e("PRICE_UPDATE", "0") == "1",
            raffle_channel=e("RAFFLE_CHANNEL", ""),
        )
