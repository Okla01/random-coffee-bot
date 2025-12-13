"""
Обработчики одобрения/отклонения заявок на доступ к анкетированию.

Обрабатывает callback-запросы для одобрения/отклонения заявок пользователей на доступ
к анкете после ввода имени. При одобрении переводит пользователя на этап загрузки фото
и уведомляет его. При отклонении возвращает пользователя на этап ввода имени с сообщением.
Обновляет исходное сообщение в админ-чате с указанием администратора и убирает inline-клавиатуру.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.core import Settings
from app.database import User
from app.services.admin import is_admin
from app.services.profile.editing import process_save_profile
from app.services.const import USER_STATUS_ACTIVE, USER_STATUS_NOT_ACTIVE

router = Router()


@router.callback_query(
    F.data.startswith("admin:name:approve:") | F.data.startswith("admin:name:reject:")
)
async def admin_name_approval_callbacks(
    cq: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """
    Обрабатывает callback-запросы для одобрения/отклонения заявок на доступ к анкете.

    Интерпретирует callback data формата 'admin:name:approve:ID' или 'admin:name:reject:ID'.
    Проверяет права администратора, получает пользователя из БД. При одобрении переводит
    пользователя на этап profile_photo, уведомляет его и запрашивает фото. При отклонении
    возвращает пользователя на этап profile_name с сообщением. Обновляет исходное сообщение
    в админ-чате с указанием администратора и убирает inline-клавиатуру.

    Args:
        cq (CallbackQuery): объект callback-запроса.
        session_factory (async_sessionmaker[AsyncSession]): фабрика БД сессий.
        settings (Settings): конфигурация приложения.

    Returns:
        None: ничего не возвращает.
    """
    data = cq.data or ""

    async with session_factory() as session:
        # Проверка прав администратора
        if not await is_admin(session, settings, cq.from_user.id):
            await cq.answer("Нет прав")
            return

        _, _, action, user_id_str = data.split(
            ":"
        )  # admin:name:approve:ID или admin:name:reject:ID
        target_id = int(user_id_str)

        user = (
            await session.execute(select(User).where(User.id == target_id))
        ).scalar_one_or_none()

        if not user:
            await cq.answer("Пользователь не найден")
            return

        reviewed_by = cq.from_user.username or str(cq.from_user.id)

        if action == "approve":
            # Одобрение заявки - финализируем анкету
            await process_save_profile(session, user)
            user.status = USER_STATUS_ACTIVE
            user.profile_approved = True
            await session.commit()

            username_display = (
                f"@{user.username}" if user.username else f"ID:{user.telegram_id}"
            )
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователю {username_display} анкета одобрена."
                + f"\n👨‍💻Рассмотрел: @{reviewed_by}",
                reply_markup=None,
            )

            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        "✨Вуаля✨ Твоя анкета одобрена! ✅\n"
                        "Теперь ты автоматически участвуешь в следующем подборе друллеги!🤗",
                    )
                except Exception:
                    pass

        else:
            # Отклонение заявки - очищаем профиль и возвращаем на ввод ФИО
            # Сохраняем username для отображения в сообщении
            username_for_display = user.username
            user.name = None
            # username не очищаем, чтобы можно было использовать в сообщениях
            user.email = None
            user.bio = None
            user.age = None
            user.photos_json = None
            user.interests_json = None
            user.stage = "profile_name"
            user.status = USER_STATUS_NOT_ACTIVE
            user.profile_approved = False
            await session.commit()

            username_display = (
                f"@{username_for_display}" if username_for_display else f"ID:{user.telegram_id}"
            )
            await cq.message.edit_text(
                cq.message.text
                + f"\n\nРешение: Пользователю {username_display} отказано, профиль очищен и пользователь возвращён к заполнению анкеты."
                + f"\n👨‍💻Рассмотрел: @{reviewed_by}",
                reply_markup=None,
            )

            if user.telegram_id:
                try:
                    await cq.message.bot.send_message(
                        user.telegram_id,
                        "⚠️ Твоя анкета отклонена.\nПожалуйста, заполни анкету заново - /start",
                    )
                except Exception:
                    pass

        # Закрываем "часы" у колбэка
        await cq.answer()
