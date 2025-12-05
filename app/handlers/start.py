"""
Обработчик команды /start для инициализации и восстановления сценария онбординга.

Отвечает только за:
- получение/создание пользователя;
- гашение старых клавиатур;
- вызов бизнес-логики онбординга (process_start);
- отправку пользователю нужных сообщений и клавиатур на основе результата.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.services.core.config import Settings
from app.database.db import get_or_create_user
from app.services.onboarding import handle_start_result, process_start
from app.keyboards.utils import clear_last_kb
from app.handlers.fsm import FSMDataKeys

# Роутер для регистрации хендлеров текущего модуля
router = Router()


@router.message(Command("start", "profile"))
async def cmd_start(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает команду /start и /profile и делегирует бизнес-логику в сервис онбординга.

    Последовательность действий:
    1. Открываем сессию БД и получаем/создаём пользователя.
    2. Гасим старые клавиатуры (если были).
    3. Вызываем process_start, который на основе stage/status пользователя
       возвращает, что именно нужно сделать дальше.
    4. В зависимости от result.action отправляем нужные сообщения и клавиатуры.

    Args:
        message (Message): входящее сообщение с командой /start.
        state (FSMContext): контекст FSM для хранения служебных данных (последняя клавиатура и т.п.).
        session_factory (async_sessionmaker[AsyncSession]): фабрика асинхронных сессий БД.
        settings (Settings): объект настроек приложения.

    Returns:
        None
    """
    # Очистка предыдущей клавиатуры (при наличии)
    await clear_last_kb(state, message.chat.id, message.bot)

    # Сбрасываем флаг админ-панели при возврате к регистрации
    await state.update_data(**{FSMDataKeys.ADMIN_PANEL_ACTIVE: False})

    # Открываем асинхронную сессию БД в контекстном менеджере
    async with session_factory() as session:
        # Получаем пользователя или создаём нового при первом заходе
        user = await get_or_create_user(
            session, message.from_user.id, message.from_user.username
        )

        # Гасим старые кнопки, если где-то висела клавиатура
        await clear_last_kb(state, message.chat.id, message.bot)

        # Вызываем бизнес-логику онбординга, которая решит, что делать дальше
        result = await process_start(session, user, settings)

        await handle_start_result(message, state, user, result)
