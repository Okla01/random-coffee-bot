from sqlalchemy.sql import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import Setting


async def get_current_settings(
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, str]:
    """
    Получает текущие настройки из базы данных.
    Возвращает словарь с ключами и значениями.
    """
    async with session_factory() as session:
        result = await session.execute(select(Setting))
        settings = result.scalars().all()

    return {s.key: s.value for s in settings}
