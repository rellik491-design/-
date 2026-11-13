# ============================================================
# Stage 1: Build Telegram Mini App Frontend (React + TS + Vite)
# ============================================================
FROM node:20-slim AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ============================================================
# Stage 2: Production Backend API + Bot Engine Runtime
# ============================================================
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen || uv sync

COPY src ./src
COPY assets ./assets
COPY scripts ./scripts
COPY main.py ./
COPY --from=frontend-builder /frontend/dist ./frontend/dist

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

CMD ["uv", "run", "python", "main.py"]
