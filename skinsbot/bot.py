"""Telegram-бот: вход в мини-приложение, рефералы, оплата Stars, уведомления, /terms и /paysupport."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from . import rules
from .config import Config
from .service import Game

log = logging.getLogger(__name__)

TERMS = (
    "<b>Условия</b>\n\n"
    "• Игровые рубли есть только внутри бота: их нельзя купить за деньги, вывести или обменять на рубли, Stars "
    "или настоящие скины. Валюта твоей страны на экране это только пересчёт по игровому курсу.\n"
    "• Предметы — игровые копии скинов CS2 с ценами рынка, вывода в Steam нет.\n"
    "• Шансы открыты на экране «Шансы», каждый бросок проверяется на экране «Честная игра».\n"
    "• Покупки за Stars: попытки, удача, VIP и места. Месячный лимит 500 ⭐. После выдачи покупка окончательная, "
    "неиспользованные попытки и удачу можно вернуть через /paysupport.\n"
    "• Уведомлений не больше 2 в день, с 21:00 до 8:00 их нет."
)


def build_dispatcher(game: Game, config: Config) -> Dispatcher:
    dp = Dispatcher()

    def play_keyboard() -> InlineKeyboardMarkup:
        rows = []
        if config.webapp_url:
            rows.append([InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=config.webapp_url))])
        rows.append([InlineKeyboardButton(text="👥 Позвать друга", callback_data="invite")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @dp.message(CommandStart())
    async def start(message: Message, command: CommandObject):
        referrer = None
        if command.args and command.args.startswith("ref_") and command.args[4:].isdigit():
            referrer = int(command.args[4:])
        u = message.from_user
        game.ensure_user(u.id, u.first_name or "", u.username, u.language_code, referrer)
        await message.answer(
            f"<b>{config.bot_name}</b>\n\n"
            f"Открывай кейсы со скинами CS2: {len(game.catalog.case_list)} своих сборок от «Халявы» до «Мажора», "
            "апгрейды, контракты с любой редкостью, ножи и перчатки, цены с рынка.\n\n"
            f"🎁 У тебя {rules.CFG['energy']['start']} бесплатных спина. Жми «Играть»!",
            reply_markup=play_keyboard(),
        )

    @dp.callback_query(F.data == "invite")
    async def invite(callback: CallbackQuery):
        me = await callback.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
        await callback.message.answer(
            f"Отправь другу ссылку. Когда он откроет {rules.CFG['referral']['qualify_spins']} кейсов, "
            f"ты получишь 3 бесплатных спина, а он 3 спина на старте.\n\n{link}")
        await callback.answer()

    @dp.message(Command("terms"))
    async def terms(message: Message):
        await message.answer(TERMS)

    @dp.message(Command("paysupport"))
    async def paysupport(message: Message):
        await message.answer(
            "Вопросы по оплате Stars: опиши проблему одним сообщением и приложи номер покупки из чека. "
            "Ответим в течение суток. Неиспользованные попытки и удачу вернём через возврат Telegram Stars.")
        for admin in config.admin_ids:
            try:
                await message.bot.send_message(admin, f"/paysupport от {message.from_user.id} (@{message.from_user.username})")
            except Exception:  # noqa: BLE001
                pass

    @dp.message(Command("help"))
    async def help_(message: Message):
        await message.answer("/start — открыть игру\n/terms — условия\n/paysupport — вопросы по оплате",
                             reply_markup=play_keyboard())

    @dp.pre_checkout_query()
    async def pre_checkout(query: PreCheckoutQuery):
        error = game.precheckout(query.invoice_payload, query.from_user.id, query.total_amount)
        await query.answer(ok=error is None, error_message=error)

    @dp.message(F.successful_payment)
    async def paid(message: Message):
        sp = message.successful_payment
        res = game.grant_payment(sp.invoice_payload, sp.telegram_payment_charge_id, sp.total_amount)
        if res.get("granted"):
            await message.answer(f"Готово: {res['title']}. Вернись в игру, покупка уже там.")
        elif res.get("reason") == "unknown_payload":
            log.error("оплата без счёта: %s", sp.invoice_payload)
            await message.answer("Оплата получена, но покупку выдать не удалось. Напиши /paysupport, мы разберёмся.")

    return dp


async def setup_bot(bot: Bot, config: Config) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Открыть игру"),
        BotCommand(command="terms", description="Условия"),
        BotCommand(command="paysupport", description="Вопросы по оплате"),
    ])
    if config.webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=config.webapp_url)))


async def send_notifications(bot: Bot, game: Game, config: Config) -> int:
    """Очередь уведомлений: не больше 2 в сутки, тишина 21:00–08:00 (правила в game.pending_notifications)."""
    sent = 0
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=config.webapp_url))]]) \
        if config.webapp_url else None
    for n in game.pending_notifications():
        try:
            await bot.send_message(n["user_id"], n["text"], reply_markup=kb)
            game.mark_sent(n["id"], True)
            sent += 1
        except Exception as e:  # noqa: BLE001 — заблокировал бота и т.п.
            log.info("уведомление %s не отправлено: %s", n["id"], e)
            game.mark_sent(n["id"], False)
    return sent
