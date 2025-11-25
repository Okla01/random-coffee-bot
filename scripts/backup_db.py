#!/usr/bin/env python3
"""
Скрипт для создания резервных копий SQLite базы данных.
Особенности:
- Использует VACUUM INTO для создания целостной копии
- Хранит бэкапы в формате YYYY-MM-DD.db
- Удаляет копии старше 7 дней
- Проверяет целостность после копирования
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def backup_database(
    src_path: str | Path,
    backup_dir: str | Path,
    days_to_keep: int = 7,
) -> None:
    """
    Создаёт резервную копию SQLite базы и удаляет старые копии.

    Args:
        src_path: Путь к исходной базе данных
        backup_dir: Директория для хранения бэкапов
        days_to_keep: Сколько дней хранить бэкапы
    """
    src_path = Path(src_path)
    backup_dir = Path(backup_dir)

    if not src_path.exists():
        print(f"Ошибка: файл БД не найден: {src_path}")
        sys.exit(1)

    # Создаём директорию для бэкапов если нужно
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Имя файла бэкапа в формате YYYY-MM-DD.db
    today = datetime.now().strftime("%Y-%m-%d")
    backup_path = backup_dir / f"{today}.db"

    try:
        # Открываем исходную БД
        src_conn = sqlite3.connect(src_path)

        # Проверяем целостность перед копированием
        src_check = src_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if src_check != "ok":
            print(f"Ошибка: исходная БД повреждена: {src_check}")
            sys.exit(1)

        # Создаём бэкап через VACUUM INTO (атомарная операция)
        src_conn.execute(f"VACUUM INTO '{backup_path}'")
        src_conn.close()

        # Проверяем целостность бэкапа
        backup_conn = sqlite3.connect(backup_path)
        backup_check = backup_conn.execute("PRAGMA integrity_check").fetchone()[0]
        backup_conn.close()

        if backup_check != "ok":
            print(f"Ошибка: бэкап повреждён: {backup_check}")
            backup_path.unlink()  # удаляем повреждённый файл
            sys.exit(1)

        print(f"Бэкап создан успешно: {backup_path}")

        # Удаляем старые бэкапы
        cutoff = datetime.now() - timedelta(days=days_to_keep)
        for old_backup in backup_dir.glob("*.db"):
            try:
                # Парсим дату из имени файла
                backup_date = datetime.strptime(old_backup.stem, "%Y-%m-%d")
                if backup_date < cutoff:
                    old_backup.unlink()
                    print(f"Удалён старый бэкап: {old_backup}")
            except ValueError:
                # Пропускаем файлы с неправильным форматом имени
                continue

    except Exception as e:
        print(f"Ошибка при создании бэкапа: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Пути настраиваются через переменные окружения
    DB_PATH = os.getenv("DB_PATH", "./data/app.db")
    BACKUP_DIR = os.getenv("BACKUP_DIR", "./data/backups")
    DAYS_TO_KEEP = int(os.getenv("BACKUP_DAYS", "7"))

    backup_database(DB_PATH, BACKUP_DIR, DAYS_TO_KEEP)
