# Базовый образ с Python 3.10
FROM python:3.10-slim

# Устанавливаем зависимости для сборки
RUN apt-get update && apt-get install -y gcc libpq-dev build-essential

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY requirements.txt .

# Ставим зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем всё приложение
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]
