"""
Типы результатов обработки полей профиля.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldResultType = Literal[
    "validation_error",  # ошибка валидации (нужно показать сообщение об ошибке)
    "field_updated_continue",  # поле обновлено, переходим к следующему шагу
    "field_updated_review",  # поле обновлено, возвращаемся в предпросмотр
]


@dataclass
class FieldResult:
    """
    Результат обработки поля профиля.
    """

    result_type: FieldResultType
    error_message: str | None = None
    next_stage: str | None = None
    is_editing: bool = False
