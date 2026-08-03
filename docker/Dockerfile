FROM python:3.11-slim

WORKDIR /app

# Dépendances système pour PostgreSQL et MetaTrader5
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /app/logs /app/data

EXPOSE 8000

CMD ["uvicorn", "arty_trading.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
