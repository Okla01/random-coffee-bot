FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости для сборки
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения
COPY . .

# Создаём директории для данных и бэкапов
RUN mkdir -p data/backups

# Задаём переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/app.db \
    BACKUP_DIR=/app/data/backups

# Запускаем приложение
CMD ["python", "-u", "main.py"]