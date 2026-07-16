# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r auri && useradd -r -g auri -d /app -s /bin/false auri

# Copy installed deps from builder
COPY --from=builder /root/.local /root/.local

# Copy bot code
COPY bot/ /app/bot/

# Ensure scripts are in PATH
ENV PATH=/root/.local/bin:$PATH \
    PYTHONPATH=/app

EXPOSE 8080

USER auri

CMD ["python", "-m", "bot.main"]
