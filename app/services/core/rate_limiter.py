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
from typing import Any, Callable, TypeVar, ParamSpec
from functools import wraps

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
                    "Rate limit reached (%d/%d), sleeping %.3f seconds",
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
            logger.exception("Rate limited request failed: %s", e)
            raise
        
        # Сохраняем timestamp выполнения
        _last_send_times.append(time.time())
        
        return result


def rate_limit(func: Callable[P, T]) -> Callable[P, T]:
    """
    Декоратор для автоматического rate limiting функций отправки сообщений.
    
    Использует rate_limited_send внутри для ограничения частоты вызовов.
    Удобен для оборачивания пользовательских функций, которые делают много вызовов API.
    
    Args:
        func: async функция для оборачивания
        
    Returns:
        Обёрнутая функция с rate limiting
    
    Example:
        @rate_limit
        async def send_notification(bot, user_id, text):
            return await bot.send_message(user_id, text)
        
        # Использование:
        await send_notification(bot, 123, "Hello")  # Автоматически с rate limiting
    """
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        return await rate_limited_send(func, *args, **kwargs)
    return wrapper


def get_rate_limit_stats() -> dict[str, Any]:
    """
    Возвращает статистику rate limiter для мониторинга.
    
    Returns:
        dict со следующими ключами:
        - current_window_size: количество запросов в текущем окне (последняя секунда)
        - limit: максимальное количество запросов в секунду
        - utilization: процент использования лимита (0.0 - 1.0)
        - oldest_timestamp: timestamp самого старого запроса в окне (или None)
        - newest_timestamp: timestamp самого нового запроса в окне (или None)
    """
    now = time.time()
    
    # Очищаем устаревшие timestamps для точной статистики
    active_timestamps = [t for t in _last_send_times if now - t < 1.0]
    
    oldest = active_timestamps[0] if active_timestamps else None
    newest = active_timestamps[-1] if active_timestamps else None
    
    return {
        "current_window_size": len(active_timestamps),
        "limit": TELEGRAM_RATE_LIMIT,
        "utilization": len(active_timestamps) / TELEGRAM_RATE_LIMIT,
        "oldest_timestamp": oldest,
        "newest_timestamp": newest,
    }


def reset_rate_limiter() -> None:
    """
    Сбрасывает состояние rate limiter.
    
    Полезно для тестирования или в случае необходимости очистки истории.
    В продакшене обычно не требуется.
    """
    _last_send_times.clear()
    logger.info("Rate limiter state reset")
