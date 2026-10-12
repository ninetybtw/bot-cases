from __future__ import annotations

import os
from dataclasses import dataclass
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

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        return cls(
            bot_token=os.environ.get("BOT_TOKEN", ""),
            webapp_url=os.environ.get("WEBAPP_URL", ""),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8080")),
            db_path=os.environ.get("DB_PATH", str(ROOT / "data" / "game.sqlite3")),
            catalog_path=Path(os.environ.get("CATALOG_PATH", ROOT / "data" / "catalog.json")),
            dev_mode=os.environ.get("DEV_MODE", "0") == "1",
            bot_name=os.environ.get("BOT_NAME", "Кейсы CS2"),
            bot_username=os.environ.get("BOT_USERNAME", "").lstrip("@"),
        )
