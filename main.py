"""
Точка входа приложения Random Coffee Bot.

Содержит функцию main, которая инициализирует асинхронный цикл событий
и запускает бота через функцию run_bot() модуля app.core.bot.

Импортная политика проекта:
- Внутри проекта используются абсолютные импорты формата `from app.xxx import ...`
"""

import asyncio

from app.core.bot import run_bot


if __name__ == "__main__":
        asyncio.run(run_bot())
