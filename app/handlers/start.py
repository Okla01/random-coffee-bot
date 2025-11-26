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
from app.services.core.users import get_or_create_user
from app.keyboards.kb_auth import (
    kb_auth_code_wait,
)
from app.keyboards.kb_profile import (
    kb_prefilled_data,
)
from app.keyboards.kb_profile import (
    kb_profile_photo,
    kb_profile_review,
    kb_profile_filled,
)
from app.services.onboarding import process_start
from app.services.core.keyboards import clear_last_kb
from app.services.profile.preview import _send_profile_preview
from app.handlers.profile.photo import _send_photos_with_actions

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
    await state.update_data(admin_panel_active=False)

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

        # Далее — только разруливаем, ЧТО отправить пользователю по результату

        # Пользователь заблокирован — только показываем сообщение и выходим
        if result.action == "blocked":
            await message.answer(
                "Доступ временно заблокирован. Свяжитесь с администратором."
            )
            return

        # Регистрация по e-mail — просим ввести корпоративную почту
        if result.action == "ask_email":
            await message.answer(
                "Привет! Давайте зарегистрируемся через корпоративную почту.\n"
                "Отправьте адрес (например, name@corp.com):"
            )
            # Здесь клавиатура не показывается, сбрасываем ссылку на last_kb
            await state.update_data(last_kb_mid=None)
            return

        # Ожидание OTP-кода — показываем клавиатуру с переотправкой/сменой почты
        if result.action == "ask_code":
            sent = await message.answer(
                "Мы уже отправили код подтверждения на вашу почту. Введите код.\n"
                "Если код истёк — воспользуйтесь кнопкой ниже.",
                reply_markup=kb_auth_code_wait(),
            )
            # Сохраняем message_id последней клавиатуры, чтобы потом её погасить
            await state.update_data(last_kb_mid=sent.message_id)
            return

        # Первый шаг анкеты — имя (с возможным предзаполнением из импорта)
        if result.action == "ask_profile_name":
            # Пытаемся вытащить предзаполненное имя из payload
            prefilled = result.payload.get("prefilled_name") if result.payload else None
            if prefilled:
                # Если есть валидное имя из импорта — предлагаем оставить или ввести новое
                sent = await message.answer(
                    f"У нас есть ваше имя из импорта: {prefilled}\n"
                    f"Оставить или ввести новое?",
                    reply_markup=kb_prefilled_data(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
            else:
                # Иначе — обычный сценарий: просим ввести имя
                await message.answer("Давайте заполним анкету! Как вас зовут?")
                await state.update_data(last_kb_mid=None)
            return

        # Загрузка/редактирование фото профиля
        if result.action == "ask_profile_photo":
            # Флаг наличия фото в payload, чтобы решить, что показывать
            has_photos = result.payload.get("has_photos") if result.payload else False
            if has_photos:
                # Фото уже есть — показываем их с кнопками действий
                photos_list = user.photos_json.get("photos", [])
                await _send_photos_with_actions(
                    message.bot, message.chat.id, user, state, photos_list
                )
            else:
                # Фото ещё нет — просим загрузить от 1 до 3 фото или использовать профильное
                sent = await message.answer(
                    "Пришлите пожалуйста фото для анкеты (от 1 до 3 фото), "
                    "либо используйте текущее фото вашего профиля.",
                    reply_markup=kb_profile_photo(),
                )
                await state.update_data(last_kb_mid=sent.message_id)
            return

        # Запрос текста о себе
        if result.action == "ask_profile_bio":
            await message.answer("Расскажите о себе (до 500 символов):")
            await state.update_data(last_kb_mid=None)
            return

        # Запрос возраста
        if result.action == "ask_profile_age":
            await message.answer("Введите ваш возраст (16–50):")
            await state.update_data(last_kb_mid=None)
            return

        # Запрос интересов
        if result.action == "ask_profile_interests":
            await message.answer(
                "Перечислите интересы через запятую (например: Python, музыка, дизайн)."
            )
            await state.update_data(last_kb_mid=None)
            return

        # Предпросмотр анкеты перед подтверждением
        if result.action == "show_profile_review":
            await _send_profile_preview(
                message.bot, message.chat.id, user, state, kb_profile_review()
            )
            return

        # Анкета уже заполнена — показываем финальный предпросмотр с кнопками
        if result.action == "show_profile_filled":
            await _send_profile_preview(
                message.bot, message.chat.id, user, state, kb_profile_filled()
            )
            return
