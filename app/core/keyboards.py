"""
Операции с inline-клавиатурами и управление состоянием сообщений.

Содержит функции для удаления клавиатур из сообщений, гашения кнопок
и сохранения ID сообщений для последующего редактирования.
"""

from __future__ import annotations

from aiogram.fsm.context import FSMContext


async def clear_last_kb(state: FSMContext, chat_id: int, bot) -> None:
    """
    Удаляет inline-клавиатуру из последнего отправленного сообщения.

    Получает сохранённый ID сообщения из FSM-состояния и пытается удалить
    клавиатуру (заменить на None). Если сообщение не найдено или ошибка,
    ошибка игнорируется. Очищает сохранённый ID сообщения в состоянии.
    """
    data = await state.get_data()
    mid = data.get("last_kb_mid")
    if mid:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=mid,
                reply_markup=None,
            )
        except Exception:
            pass
        await state.update_data(last_kb_mid=None)
