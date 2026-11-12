FROM python:3.12-slim

# Установка системных зависимостей (ffmpeg для мастеринга голосовых сообщений)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Установка менеджера пакетов uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

WORKDIR /app

# Копирование спецификаций зависимостей
COPY pyproject.toml uv.lock* ./

# Установка зависимостей проекта
RUN uv sync --frozen || uv sync

# Копирование исходного кода и ассетов
COPY src ./src
COPY assets ./assets
COPY scripts ./scripts
COPY main.py ./

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Запуск бота в непрерывном режиме
CMD ["uv", "run", "python", "main.py"]
