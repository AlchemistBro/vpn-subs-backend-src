"""Telegram-бот на aiogram 3. Запуск: python -m vas.bot.main

Бот — ещё один клиент тех же сервисов: регистрирует, выдаёт код входа в приложение,
показывает статус и ссылку на оплату. Своей логики оплаты у него нет.
"""

import asyncio
import uuid

import structlog
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from vas.accounts.service import invite_code
from vas.billing.periods import is_active
from vas.core.log import setup_logging
from vas.core.problems import Problem
from vas.runtime import Runtime, build_runtime
from vas.settings import get_settings

log = structlog.get_logger()


def build_router(rt: Runtime) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject) -> None:
        assert message.from_user is not None
        user, created = await rt.accounts.start_from_bot(message.from_user.id, command.args)
        greeting = (
            f"Привет! Дарим {rt.settings.trial_days} дня бесплатного доступа."
            if created
            else "С возвращением!"
        )
        await message.answer(
            f"{greeting}\n\n/login — код для входа в приложение\n/status — срок доступа\n"
            f"/buy — продлить\n\nПригласить друга: t.me/{(await message.bot.me()).username}"  # type: ignore[union-attr]
            f"?start={invite_code(user.id)}"
        )

    @router.message(Command("login"))
    async def login(message: Message) -> None:
        assert message.from_user is not None
        try:
            code = await rt.accounts.bot_login_code(message.from_user.id)
        except Problem as exc:
            await message.answer(f"Не получилось: {exc.message}")
            return
        await message.answer(f"Код для входа: <code>{code}</code>\nДействует 5 минут.")

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        assert message.from_user is not None
        user, _ = await rt.accounts.start_from_bot(message.from_user.id)
        sub, _ = await rt.billing.overview(user.id)
        if is_active(sub.paid_until, rt.clock.now()):
            assert sub.paid_until is not None
            renew = "включено" if sub.autorenew else "выключено"
            text = f"Доступ до {sub.paid_until:%d.%m.%Y %H:%M} UTC. Автопродление {renew}."
        else:
            text = "Доступа сейчас нет. /buy — оплатить."
        await message.answer(text)

    @router.message(Command("buy"))
    async def buy(message: Message) -> None:
        plans = await rt.billing.plans()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{p['title']} — {p['price']} ₽", callback_data=f"buy:{p['code']}"
                    )
                ]
                for p in plans
            ]
        )
        await message.answer("Выберите тариф:", reply_markup=keyboard)

    @router.callback_query(F.data.startswith("buy:"))
    async def buy_plan(query: CallbackQuery) -> None:
        assert query.data is not None
        user, _ = await rt.accounts.start_from_bot(query.from_user.id)
        # id нажатия — ключ идемпотентности: двойной тап не создаст два платежа
        key = f"tg:{uuid.uuid5(uuid.NAMESPACE_URL, query.id)}"
        try:
            result = await rt.billing.checkout(
                user_id=user.id, plan_code=query.data[4:], client_key=key, save_card=True
            )
        except Problem as exc:
            await query.answer(exc.message, show_alert=True)
            return
        await query.answer()
        if query.message is not None and result.pay_url:
            await query.message.answer(f"Оплатить: {result.pay_url}")

    return router


async def main() -> None:
    settings = get_settings()
    setup_logging(json=settings.log_json)
    if settings.bot_token is None:
        raise SystemExit("VAS_BOT_TOKEN is not set")
    rt = build_runtime(settings)
    bot = Bot(
        settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()
    dp.include_router(build_router(rt))
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await rt.close()


if __name__ == "__main__":
    asyncio.run(main())
