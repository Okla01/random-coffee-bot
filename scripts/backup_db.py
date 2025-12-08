#!/usr/bin/env python3
"""
Скрипт для создания резервных копий SQLite базы данных с инкрементальной логикой.
Особенности:
- Использует VACUUM INTO для создания целостной копии
- Инкрементальный бэкап: создаёт бэкап только если БД изменилась с последнего бэкапа
- Хранит бэкапы в формате YYYY-MM-DD.db
- Удаляет копии старше 7 дней
- Проверяет целостность после копирования
- Ежесуточный запуск (настраивается через cron/scheduler)
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.database.utils import now_msk


def _db_changed(src_path: Path, backup_path: Path) -> bool:
    """
    Проверяет, изменилась ли БД с момента последнего бэкапа.
    
    Args:
        src_path: Путь к исходной БД
        backup_path: Путь к последнему бэкапу
        
    Returns:
        True если БД изменилась или бэкапа нет, False если не изменилась
    """
    if not backup_path.exists():
        return True
    
    # Получаем метаданные исходной БД
    src_stat = src_path.stat()
    src_mtime = src_stat.st_mtime
    src_size = src_stat.st_size
    
    # Получаем метаданные бэкапа
    backup_stat = backup_path.stat()
    backup_mtime = backup_stat.st_mtime
    backup_size = backup_stat.st_size
    
    # БД изменилась, если изменилось время модификации или размер
    # Учитываем небольшую погрешность (1 секунда) для файловых систем
    changed = abs(src_mtime - backup_mtime) > 1.0 or src_size != backup_size
    
    return changed


def backup_database(
    src_path: str | Path,
    backup_dir: str | Path,
    days_to_keep: int = 7,
    force: bool = False,
) -> None:
    """
    Создаёт резервную копию SQLite базы с инкрементальной логикой и удаляет старые копии.

    Args:
        src_path: Путь к исходной базе данных
        backup_dir: Директория для хранения бэкапов
        days_to_keep: Сколько дней хранить бэкапы
        force: Принудительно создать бэкап даже если БД не изменилась
    """
    src_path = Path(src_path)
    backup_dir = Path(backup_dir)

    if not src_path.exists():
        print(f"Ошибка: файл БД не найден: {src_path}")
        sys.exit(1)

    # Создаём директорию для бэкапов если нужно
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Имя файла бэкапа в формате YYYY-MM-DD.db
    today = now_msk().strftime("%Y-%m-%d")
    backup_path = backup_dir / f"{today}.db"

    # Инкрементальная проверка: создаём бэкап только если БД изменилась
    need_backup = True
    if not force and backup_path.exists():
        if not _db_changed(src_path, backup_path):
            print(f"БД не изменилась с последнего бэкапа. Пропускаем создание нового бэкапа.")
            print(f"Последний бэкап: {backup_path}")
            need_backup = False
        else:
            print(f"БД изменилась. Создаём новый бэкап...")
            # Удаляем старый бэкап за сегодня, если он есть
            backup_path.unlink()
    elif backup_path.exists() and force:
        print(f"Принудительное создание бэкапа (старый будет перезаписан)...")
        backup_path.unlink()
    else:
        print(f"Создаём новый бэкап...")

    # Создаём бэкап только если нужно
    if need_backup:
        try:
            # Открываем исходную БД
            src_conn = sqlite3.connect(src_path)

            # Проверяем целостность перед копированием
            src_check = src_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if src_check != "ok":
                print(f"Ошибка: исходная БД повреждена: {src_check}")
                src_conn.close()
                sys.exit(1)

            # Создаём бэкап через VACUUM INTO (атомарная операция)
            # Используем абсолютный путь для надёжности
            backup_path_abs = backup_path.resolve()
            # Экранируем одинарные кавычки в пути (удваиваем их для SQLite)
            backup_path_escaped = str(backup_path_abs).replace("'", "''")
            src_conn.execute(f"VACUUM INTO '{backup_path_escaped}'")
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
        except Exception as e:
            print(f"Ошибка при создании бэкапа: {e}")
            sys.exit(1)

    # Удаляем старые бэкапы (выполняем всегда, даже если новый бэкап не создавался)
    try:
        cutoff = now_msk() - timedelta(days=days_to_keep)
        # Преобразуем cutoff в naive datetime для сравнения с датой из имени файла
        cutoff_naive = cutoff.replace(tzinfo=None)
        deleted_count = 0
        for old_backup in backup_dir.glob("*.db"):
            try:
                # Парсим дату из имени файла (naive datetime)
                backup_date = datetime.strptime(old_backup.stem, "%Y-%m-%d")
                if backup_date < cutoff_naive:
                    old_backup.unlink()
                    print(f"Удалён старый бэкап: {old_backup}")
                    deleted_count += 1
            except ValueError:
                # Пропускаем файлы с неправильным форматом имени
                continue
        
        if deleted_count == 0:
            print("Старые бэкапы для удаления не найдены.")
    except Exception as e:
        print(f"Ошибка при удалении старых бэкапов: {e}")
        # Не завершаем выполнение, т.к. основной бэкап уже создан


if __name__ == "__main__":
    # Пути настраиваются через переменные окружения
    DB_PATH = os.getenv("DB_PATH", "./data/db.sqlite3")
    BACKUP_DIR = os.getenv("BACKUP_DIR", "./data/backups")
    DAYS_TO_KEEP = int(os.getenv("BACKUP_DAYS", "7"))
    FORCE_BACKUP = os.getenv("FORCE_BACKUP", "false").lower() in ("true", "1", "yes")

    backup_database(DB_PATH, BACKUP_DIR, DAYS_TO_KEEP, force=FORCE_BACKUP)
