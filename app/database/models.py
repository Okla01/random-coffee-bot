"""
Модели данных для работы с базой данных SQLAlchemy (асинхронный режим).

Содержит модели для таблиц: User (пользователи и их профили), Otp (одноразовые коды),
AuthAttempt (попытки авторизации), Role (роли доступа), UserRole (назначение ролей),
AdminLog (журнал действий администраторов).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.sqlite import JSON

from app.database.utils import now_msk

class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy ORM."""


# ----------------------------- Users ----------------------------- #


class User(Base):
    """
    Пользователь приложения с состоянием авторизации и данными профиля.

    Хранит Telegram ID, статус, стадию прохождения сценариев, email,
    счётчики попыток, данные анкеты (имя, фото, биография, возраст, интересы),
    и историю активности.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Текущий статус/стадия
    status: Mapped[str] = mapped_column(
        String(16), default="new", index=True
    )  # новый/активный/заблокированный
    stage: Mapped[str] = mapped_column(String(32), default="new", index=True)

    # Авторизация через e-mail
    email: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True
    )
    email_attempts: Mapped[int] = mapped_column(Integer, default=0)
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)

    # Анкета
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    photos_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )  # {"фото": [{"file_id":..., "ts":...}, ...]}
    bio: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    interests_json: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )  # {"интересы": [...]}

    # Аудит и временные метки
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )  # Дата регистрации
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )  # Последняя активность
    last_match_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )  # Дата последнего матча
    last_pairing_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )  # Дата последней подборки пары (создания Match)

    # Relationships
    otps: Mapped[list["Otp"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["AuthAttempt"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    matches_as_a: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.user_a_id", back_populates="user_a"
    )
    matches_as_b: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.user_b_id", back_populates="user_b"
    )
    complaints_as_reporter: Mapped[list["Complaint"]] = relationship(
        "Complaint", foreign_keys="Complaint.reporter_id", back_populates="reporter"
    )
    complaints_as_reported: Mapped[list["Complaint"]] = relationship(
        "Complaint", foreign_keys="Complaint.reported_id", back_populates="reported"
    )


# ------------------------------ OTP ------------------------------ #


class Otp(Base):
    """
    Одноразовый пароль для подтверждения email.

    Хранит код, привязку к пользователю и сессии, счётчик переотправок,
    время последней отправки, время создания, время истечения, время использования.
    Поддерживает TTL коды, cooldown 120 секунд и лимит ≤3 переотправок на сессию.
    """

    __tablename__ = "otp"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    code: Mapped[str] = mapped_column(String(12))
    session_id: Mapped[str] = mapped_column(
        String(32), index=True
    )  # логическая «сессия» для контроля переотправок
    resend_count: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="otps")

    __table_args__ = (
        UniqueConstraint("user_id", "session_id", name="uq_otp_user_session"),
    )


# ------------------------ Auth Attempts -------------------------- #


class AuthAttempt(Base):
    """
    Логирование последних попыток ввода учётных данных.

    Сохраняет последние значения email и OTP-кодов, введённые пользователем,
    для истории при проверке администраторами. Хранит до 3 последних значений на пользователя/тип.
    """

    __tablename__ = "auth_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    type: Mapped[str] = mapped_column(String(16))  # "email" | "otp"
    value: Mapped[str] = mapped_column(String(255))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )

    user: Mapped["User"] = relationship(back_populates="attempts")


# ------------------------------ Roles ---------------------------- #


class Role(Base):
    """
    Роль доступа в системе.

    Определяет типы ролей для управления доступом (например, 'admin' для админ-панели).
    """

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, index=True)


class UserRole(Base):
    """
    Назначение роли пользователю.

    Связь между пользователем и ролью для управления доступом и разрешениями в системе.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True, index=True
    )

    user: Mapped["User"] = relationship(back_populates="roles")
    role: Mapped["Role"] = relationship()


# --------------------------- Admin log --------------------------- #


class AdminLog(Base):
    """
    Логирование всех действий в административной панели.

    Сохраняет информацию о действиях администратора (открытие панели, блокировка пользователя и т.д.),
    включая ID администратора, тип действия, данные действия и временную метку.
    """

    __tablename__ = "admin_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, index=True)  # ID администратора (telegram_id)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )


# ------------------------------ Matches ----------------------------- #


class Match(Base):
    """
    Матч между двумя пользователями.

    Хранит информацию о паре пользователей, которые были сопоставлены,
    дату создания матча и его статус.
    """

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_a_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    user_b_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, onupdate=now_msk, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), default="pending_response", index=True
    )
    jaccard_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    user_a_response: Mapped[str] = mapped_column(
        String(16), default="none", index=True
    )
    user_b_response: Mapped[str] = mapped_column(
        String(16), default="none", index=True
    )
    meeting_start_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    meeting_end_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reminder_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # ID последних сообщений с клавиатурами (для последующего удаления)
    last_message_id_a: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    last_message_id_b: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )

    user_a: Mapped["User"] = relationship("User", foreign_keys=[user_a_id], back_populates="matches_as_a")
    user_b: Mapped["User"] = relationship("User", foreign_keys=[user_b_id], back_populates="matches_as_b")


class MatchSlot(Base):
    """
    Интервалы времени, выбранные пользователями для конкретного матча.
    """

    __tablename__ = "match_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    time_from: Mapped[str] = mapped_column(String(5))
    time_to: Mapped[str] = mapped_column(String(5))

    match: Mapped["Match"] = relationship()
    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "match_id",
            "user_id",
            "date",
            "time_from",
            "time_to",
            name="uq_matchslot_unique",
        ),
    )


# ----------------------------- Complaints --------------------------- #


class Complaint(Base):
    """
    Жалоба пользователя на другого пользователя.

    Хранит информацию о жалобе: кто подал жалобу, на кого, текст жалобы,
    ответ администратора, время создания и статус обработки.
    """

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # ID пользователя, который подал жалобу
    reported_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )  # ID пользователя, на которого пожаловались
    text: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)  # Текст жалобы
    admin_response: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)  # Ответ администратора
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_msk, index=True
    )  # Время создания жалобы
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending, reviewed, resolved, rejected

    reporter: Mapped["User"] = relationship("User", foreign_keys=[reporter_id], back_populates="complaints_as_reporter")
    reported: Mapped["User"] = relationship("User", foreign_keys=[reported_id], back_populates="complaints_as_reported")


# ----------------------------- Settings ---------------------------- #


class Setting(Base):
    """
    Настройки приложения, хранящиеся в базе данных.

    Хранит ключ-значение пары для конфигурации приложения.
    Используется для хранения настроек, которые могут изменяться без перезапуска приложения.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    value: Mapped[str] = mapped_column(String(512))
