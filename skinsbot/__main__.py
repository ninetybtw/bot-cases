"""Запуск: python -m skinsbot — API мини-приложения, бот (long polling) и worker в одном процессе."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

from aiohttp import web

from .config import ROOT, Config
from .service import Catalog, Game
from .share import ShareCards
from .web import create_app

log = logging.getLogger("skinsbot")


async def worker(game: Game, config: Config, bot=None) -> None:
    """Фоновые задачи раз в минуту; цены CS2 и курс ЦБ — раз в сутки в 04:00 МСК (если PRICE_UPDATE=1)."""
    from .bot import send_notifications

    while True:
        try:
            now = game.now_msk()
            day = now.strftime("%Y-%m-%d")
            if config.price_update and now.hour >= 4 and game._job_due("prices.cs2", day):
                proc = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "tools" / "build_catalog.py"))
                if await proc.wait() == 0:
                    with game.tx():
                        game._job_done("prices.cs2", day)
            result = game.run_jobs()
            if bot:
                await send_notifications(bot, game, config)
                if config.raffle_channel:
                    for draw in result["raffle"]:
                        lines = "\n".join(f"• {w['name']} — {w['prize']}" for w in draw["winners"]) or "билетов не было"
                        await bot.send_message(config.raffle_channel,
                                               f"Розыгрыш {draw['day']}\n{lines}\n\nЗерно: <code>{draw['seed']}</code>\n"
                                               f"Хеш, опубликованный заранее: <code>{draw['seed_hash']}</code>")
        except Exception:  # noqa: BLE001
            log.exception("worker")
        await asyncio.sleep(60)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config.from_env()
    game = Game(config.db_path, Catalog(config.catalog_path))
    share = ShareCards(config.share_secret, config.bot_name)

    bot = None
    if config.bot_token:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties

        bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
        if not config.bot_username:
            config.bot_username = (await bot.get_me()).username

    runner = web.AppRunner(create_app(game, config, bot, share))
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    log.info("мини-приложение: http://%s:%s", config.host, config.port)
    tasks = [asyncio.create_task(worker(game, config, bot))]

    if not bot:
        log.warning("BOT_TOKEN не задан: работает только веб (для разработки включи DEV_MODE=1)")
        await asyncio.gather(*tasks)
        return

    from .bot import build_dispatcher, setup_bot

    await setup_bot(bot, config)
    await build_dispatcher(game, config).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
