"""Telegram-бот: вход в мини-приложение, условия, поддержка по оплатам."""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from .config import Config
from .service import Game

TERMS = (
    "<b>Условия</b>\n\n"
    "• Игровые доллары ($ игр.) существуют только внутри бота: их нельзя купить за деньги, "
    "вывести или обменять на рубли, Stars или настоящие скины.\n"
    "• Предметы — игровые копии скинов CS2, их нельзя вывести в Steam.\n"
    "• Шансы каждого кейса открыты на экране «Шансы», каждый спин можно проверить на экране «Честная игра».\n"
    "• Не больше 2 уведомлений в день, ночью уведомлений нет."
)


def build_dispatcher(game: Game, config: Config) -> Dispatcher:
    dp = Dispatcher()

    def play_keyboard(start_param: str = "") -> InlineKeyboardMarkup:
        url = config.webapp_url + (f"?startapp={start_param}" if start_param else "")
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Играть", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="👥 Позвать друга", callback_data="invite")],
        ])

    @dp.message(CommandStart())
    async def start(message: Message, command: CommandObject):
        referrer = None
        if command.args and command.args.startswith("ref_") and command.args[4:].isdigit():
            referrer = int(command.args[4:])
        user = message.from_user
        game.ensure_user(user.id, user.first_name or user.username or "", referrer)
        cases = len(game.catalog.case_list)
        await message.answer(
            f"<b>{config.bot_name}</b>\n\n"
            f"Открывай настоящие кейсы CS2: {cases} кейсов, ножи и перчатки, износ и StatTrak™, цены с рынка.\n\n"
            f"🎁 У тебя 3 бесплатных спина. Жми «Играть»!",
            reply_markup=play_keyboard(),
        )

    @dp.callback_query(F.data == "invite")
    async def invite(callback):
        me = await callback.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
        await callback.message.answer(
            "Отправь другу ссылку. Когда он откроет 10 кейсов, ты получишь 3 бесплатных спина.\n\n" + link
        )
        await callback.answer()

    @dp.message(Command("terms"))
    async def terms(message: Message):
        await message.answer(TERMS)

    @dp.message(Command("paysupport"))
    async def paysupport(message: Message):
        await message.answer(
            "Вопросы по оплате в Telegram Stars: опиши проблему одним сообщением, мы ответим в течение суток. "
            "Сейчас в боте нет платных покупок."
        )

    @dp.message(Command("help"))
    async def help_(message: Message):
        await message.answer(
            "/start — открыть игру\n/terms — условия\n/paysupport — вопросы по оплате", reply_markup=play_keyboard()
        )

    return dp


async def setup_bot(bot: Bot, config: Config) -> None:
    await bot.set_my_commands([
        BotCommand(command="start", description="Открыть игру"),
        BotCommand(command="terms", description="Условия"),
        BotCommand(command="paysupport", description="Вопросы по оплате"),
    ])
    if config.webapp_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Играть", web_app=WebAppInfo(url=config.webapp_url)))
