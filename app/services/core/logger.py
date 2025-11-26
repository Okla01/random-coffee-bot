"""
Конфигурация системы логирования приложения.

Обеспечивает единую точку настройки логирования для всего приложения.
Использует консольный вывод с форматированием временных меток, уровня логирования,
имени модуля и сообщения.
"""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    """
    Инициализирует корневой логгер приложения.

    Если логгер уже имеет обработчики, не переинициализирует его.
    Устанавливает уровень логирования и добавляет консольный обработчик
    с форматированием, включающим временную метку, уровень, имя модуля и сообщение.

    Args:
        level (str): уровень логирования (по умолчанию "INFO").
                     Преобразуется в верхний регистр перед применением.

    Returns:
        None: ничего не возвращает.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)

