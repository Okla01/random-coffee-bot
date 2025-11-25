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
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.sqlite import JSON

class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy ORM."""


# ----------------------------- Users ----------------------------- #


class User(Base):
    """
    Пользователь приложения с состоянием авторизации и данными профиля.

    Хранит Telegram ID, статус, стадию прохождения сценариев, email,
    счётчики попыток, данные анкеты (имя, фото, биография, возраст, интересы),
    информацию об импорте и историю активности.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Текущий статус/стадия
    status: Mapped[str] = mapped_column(
        String(16), default="new", index=True
    )  # новый/активный/заблокированный/импортированный
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

    # Импорт
    origin: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True
    )  # 'импорт' | 'сам'
    import_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Аудит
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    otps: Mapped[list["Otp"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["AuthAttempt"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
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
        DateTime(timezone=True), server_default=func.now()
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
        DateTime(timezone=True), server_default=func.now(), index=True
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
    включая Telegram ID администратора, тип действия, данные действия и временную метку.
    """

    __tablename__ = "admin_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
