import asyncio

from faker import Faker
from faker.generator import random

from app.services.core import Settings
from app.database import User, lifespan_db

faker = Faker("ru_RU")


async def add_fake_users(n: int = 50) -> None:
    settings = Settings.load()

    async with lifespan_db(settings) as session_factory:
        async with session_factory() as session:
            for i in range(n):
                user = User(
                    telegram_id=10_000_000 + i,
                    username=f"test_user_{i}",
                    name=faker.name(),
                    status=random.choices(
                        ["active", "not_active", "blocked"],
                        weights=[70, 20, 10],
                        k=1
                    )[0],
                    stage="profile_filled",
                )
                session.add(user)

            await session.commit()

    print(f"Добавлено {n} тестовых пользователей")


if __name__ == "__main__":
    asyncio.run(add_fake_users())