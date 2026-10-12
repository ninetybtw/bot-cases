"""Запуск: python -m skinsbot — HTTP-сервер мини-приложения и бот (long polling) в одном процессе."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .config import Config
from .service import Catalog, Game
from .web import create_app


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config.from_env()
    game = Game(config.db_path, Catalog(config.catalog_path))

    runner = web.AppRunner(create_app(game, config))
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    logging.info("мини-приложение: http://%s:%s", config.host, config.port)

    if not config.bot_token:
        logging.warning("BOT_TOKEN не задан: работает только веб (для разработки включи DEV_MODE=1)")
        await asyncio.Event().wait()
        return

    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties

    from .bot import build_dispatcher, setup_bot

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    await setup_bot(bot, config)
    await build_dispatcher(game, config).start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
