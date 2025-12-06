import asyncio
import random
from datetime import timedelta

from faker import Faker

from app.services.core import Settings
from app.database import User, Match, lifespan_db
from app.database.utils import now_msk

faker = Faker("ru_RU")


def _rand_dt_within_days(days_back: int = 60):
    """Случайная дата 'в прошлом' в пределах days_back дней."""
    base = now_msk()
    return base - timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )


async def seed_fake_users_and_matches(
    users_n: int = 50,
    matches_n: int = 450,
) -> None:
    settings = Settings.load()

    async with lifespan_db(settings) as session_factory:
        async with session_factory() as session:
            # 1) Пользователи
            users: list[User] = []
            for i in range(users_n):
                user = User(
                    telegram_id=10_000_000 + i,
                    username=f"test_user_{i}",
                    name=faker.name(),
                    status=random.choices(
                        ["active", "not_active", "blocked"],
                        weights=[70, 20, 10],
                        k=1,
                    )[0],
                    stage="profile_filled",
                )
                session.add(user)
                users.append(user)

            # нужно, чтобы появились user.id
            await session.flush()

            user_ids = [u.id for u in users]

            # 2) Матчи (уникальные пары)
            # максимальное число уникальных пар: users_n * (users_n - 1) / 2
            max_pairs = users_n * (users_n - 1) // 2
            if matches_n > max_pairs:
                matches_n = max_pairs  # чтобы не уйти в бесконечный цикл

            used_pairs: set[tuple[int, int]] = set()
            matches: list[Match] = []

            statuses = ["pending_response", "matched", "completed", "canceled"]
            responses = ["none", "accepted", "declined"]

            while len(matches) < matches_n:
                a, b = random.sample(user_ids, 2)
                pair = (a, b) if a < b else (b, a)  # нормализуем
                if pair in used_pairs:
                    continue
                used_pairs.add(pair)

                created_at = _rand_dt_within_days(90)
                updated_at = created_at + timedelta(days=random.randint(0, 14))

                status = random.choices(
                    statuses,
                    weights=[55, 25, 15, 5],
                    k=1,
                )[0]

                # ответы пользователей
                user_a_response = random.choices(responses, weights=[55, 35, 10], k=1)[
                    0
                ]
                user_b_response = random.choices(responses, weights=[55, 35, 10], k=1)[
                    0
                ]

                last_reminder_at = None
                if status == "pending_response" and random.random() < 0.6:
                    last_reminder_at = updated_at - timedelta(
                        hours=random.randint(1, 72)
                    )

                match = Match(
                    user_a_id=a,
                    user_b_id=b,
                    created_at=created_at,
                    updated_at=updated_at,
                    status=status,
                    jaccard_score=round(random.random(), 3),
                    user_a_response=user_a_response,
                    user_b_response=user_b_response,
                    last_reminder_at=last_reminder_at,
                    last_message_id_a=None,
                    last_message_id_b=None,
                )
                matches.append(match)

            session.add_all(matches)
            await session.commit()

    print(f"Добавлено пользователей: {users_n}")
    print(f"Добавлено матчей: {matches_n}")


if __name__ == "__main__":
    asyncio.run(seed_fake_users_and_matches(users_n=50, matches_n=450))
