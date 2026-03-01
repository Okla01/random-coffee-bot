"""
Rate limiter для Telegram API с защитой от FloodWait.

Telegram Bot API имеет лимит: 30 сообщений в секунду для одного бота.
Превышение этого лимита приводит к FloodWait ошибке и блокировке бота на несколько часов.

Этот модуль реализует rate limiting с использованием sliding window алгоритма,
который гарантирует соблюдение лимита и предотвращает FloodWait.

Usage:
    from app.services.core.rate_limiter import rate_limited_send
    
    # В async функции:
    await rate_limited_send(bot.send_message, chat_id=123, text="Hello")
    await rate_limited_send(bot.send_photo, chat_id=123, photo=file_id)
"""

import asyncio
import time
import logging
from typing import Callable, TypeVar, ParamSpec

logger = logging.getLogger(__name__)

# Telegram лимит: 30 сообщений в секунду
TELEGRAM_RATE_LIMIT = 30

# Семафор для ограничения параллельных запросов
_semaphore = asyncio.Semaphore(TELEGRAM_RATE_LIMIT)

# Список timestamps последних отправок (sliding window)
_last_send_times: list[float] = []

P = ParamSpec('P')
T = TypeVar('T')


async def rate_limited_send(
    func: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """
    Обёртка для rate limiting Telegram API вызовов.
    
    Гарантирует не более TELEGRAM_RATE_LIMIT запросов в секунду.
    Использует sliding window алгоритм для точного контроля частоты запросов.
    
    Принцип работы:
    1. Захватывает семафор (не более 30 одновременных запросов)
    2. Очищает устаревшие timestamps (старше 1 секунды)
    3. Если достигли лимита, ждёт до освобождения окна
    4. Выполняет запрос
    5. Сохраняет timestamp выполнения
    
    Args:
        func: async функция для вызова (обычно bot.send_message, bot.send_photo и т.д.)
        *args: позиционные аргументы для функции
        **kwargs: именованные аргументы для функции
        
    Returns:
        Результат вызова функции
        
    Example:
        # Отправка сообщения с rate limiting
        await rate_limited_send(bot.send_message, chat_id=123, text="Hello")
        
        # Отправка фото с rate limiting
        await rate_limited_send(bot.send_photo, chat_id=123, photo=photo_file_id)
        
        # Отправка media group с rate limiting
        await rate_limited_send(bot.send_media_group, chat_id=123, media=media_group)
    """
    async with _semaphore:
        now = time.time()
        
        # Очищаем устаревшие timestamps (старше 1 секунды)
        # Это реализует sliding window: учитываются только запросы за последнюю секунду
        _last_send_times[:] = [t for t in _last_send_times if now - t < 1.0]
        
        # Если достигли лимита, ждём освобождения окна
        if len(_last_send_times) >= TELEGRAM_RATE_LIMIT:
            # Вычисляем сколько нужно подождать до освобождения самого старого слота
            oldest = _last_send_times[0]
            sleep_time = 1.0 - (now - oldest)
            
            if sleep_time > 0:
                logger.debug(
                    "Достигнут лимит запросов (%d/%d), ожидание %.3f секунд",
                    len(_last_send_times),
                    TELEGRAM_RATE_LIMIT,
                    sleep_time,
                )
                await asyncio.sleep(sleep_time)
            
            # Удаляем самый старый timestamp
            _last_send_times.pop(0)
        
        # Выполняем запрос
        try:
            result = await func(*args, **kwargs)
        except Exception as e:
            # Для TelegramBadRequest с ошибками file_id логируем на уровне WARNING,
            # так как эти ошибки ожидаемы и обрабатываются выше по стеку
            from aiogram.exceptions import TelegramBadRequest
            if isinstance(e, TelegramBadRequest):
                error_msg = str(e).lower()
                if (
                    "wrong file" in error_msg
                    or "file_reference" in error_msg
                    or "can't unserialize" in error_msg
                    or "wrong remote file identifier" in error_msg
                ):
                    # Это ожидаемая ошибка устаревшего file_id - логируем на уровне WARNING
                    logger.warning(
                        "Запрос с ограничением скорости: невалидный file_id (будет обновлён): %s", e
                    )
                else:
                    # Другие TelegramBadRequest - логируем как обычно
                    logger.exception("Запрос с ограничением скорости завершился ошибкой: %s", e)
            else:
                # Все остальные ошибки - логируем как обычно
                logger.exception("Запрос с ограничением скорости завершился ошибкой: %s", e)
            raise
        
        # Сохраняем timestamp выполнения
        _last_send_times.append(time.time())
        
        return result

