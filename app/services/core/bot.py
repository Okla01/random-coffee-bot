"""
Сборка бота: конфиг, БД, логирование, middlewares, регистрация роутеров.

Политика импортов: внутри модулей проекта используем абсолютные импорты
`from app.package import ...`. Это облегчает понимание зависимостей и
позволяет переименовывать корневой пакет без правки внутренних модулей.

Порядок роутеров важен: profile перед registration, чтобы текст анкеты не
перехватывался регистрацией.
"""

from __future__ import annotations

import signal
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from .config import Settings
from .logger import setup_logging
from app.database import lifespan_db
from app.middlewares import DbSessionMiddleware, BlockedUserMiddleware

# импортируем роутеры в нужном порядке
from app.handlers import start_router
from app.handlers.profile.editing import router as editing_router
from app.handlers.profile.photo import router as photo_router
from app.handlers.profile.delete_me import router as delete_me_router
from app.handlers.auth.registration import router as registration_router
from app.handlers.admin import router as commands_router
from app.handlers.admin.export_excel import router as export_excel_router
from app.handlers.admin.users import router as users_router


async def create_dispatcher(settings: Settings) -> Dispatcher:
    """
    Инициализирует диспетчер и регистрирует все роутеры обработчиков.

    Настраивает логирование, создаёт объект Dispatcher и подключает роутеры
    в порядке приоритета: start, profile, registration, admin.
    Порядок важен — роутеры профиля должны быть выше регистрации.

    Args:
        settings (Settings): объект конфигурации приложения.

    Returns:
        Dispatcher: инициализированный диспетчер с зарегистрированными роутерами.
    """
    setup_logging(settings.log_level)
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(editing_router)  # важно: анкета выше
    dp.include_router(photo_router)  # фото должны быть выше регистрации
    dp.include_router(delete_me_router)  # удаление профиля
    dp.include_router(registration_router)  # регистрация ниже
    dp.include_router(commands_router)
    dp.include_router(export_excel_router)
    dp.include_router(users_router)

    return dp


async def run_bot() -> None:
    """
    Главная точка запуска бота.

    Инициализирует конфигурацию, создаёт бота и диспетчер, регистрирует
    middleware для БД, устанавливает обработчики сигналов (SIGINT, SIGTERM)
    и запускает polling для получения обновлений от Telegram API.

    Args:
        None

    Returns:
        None: выполняет блокирующий цикл polling до завершения программы.

    Raises:
        RuntimeError: если не удаётся загрузить конфигурацию (например, нет BOT_TOKEN).
    """
    settings = Settings.load()
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode='HTML'))
    dp = await create_dispatcher(settings)

    # Регистрируем middleware/контекст БД
    # Порядок важен: последний зарегистрированный outer_middleware выполняется первым (LIFO)
    # Регистрируем BlockedUserMiddleware последним, чтобы он выполнился первым в цепочке
    async with lifespan_db(settings) as session_factory:
        dp.update.outer_middleware(DbSessionMiddleware(session_factory))
        dp.update.outer_middleware(BlockedUserMiddleware(session_factory))
        dp["settings"] = settings
        await bot.delete_webhook(drop_pending_updates=True)

        loop = asyncio.get_running_loop()

        def _make_signal_handler(sig_name: str):
            def _handler(signum, frame):
                # Запускаем остановку polling в event loop
                try:
                    loop.call_soon_threadsafe(asyncio.create_task, dp.stop_polling())
                except Exception:
                    # если loop уже закрыт или недоступен — просто игнорируем
                    pass

            return _handler

        # Регистрируем обработчики через signal.signal — это работает и в Windows
        for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, _make_signal_handler(sig.name))
            except Exception:
                # в редких окружениях регистрация сигналов может провалиться
                # (например, в некоторых контейнерах) — тогда полагаемся на
                # поведение aiogram (он попытается сам обработать сигналы)
                pass

        # Запускаем polling и разрешаем нашему коду корректно остановить его
        # через dp.stop_polling(). Отключаем внутреннюю обработку сигналов
        # чтобы не было двойной регистрации.
        await dp.start_polling(bot, settings=settings, handle_signals=False)

