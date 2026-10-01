# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# constraints.txt pins the tested version of every package (see its header).
COPY bot/requirements.txt backend/constraints.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt -c constraints.txt

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r auri && useradd -r -g auri -d /app -s /bin/false auri

# Copy installed deps from builder. /root defaults to 700 (root-only), which
# blocks the non-root `auri` user below from even traversing into
# /root/.local/bin — chmod it open so `auri` can execute the installed
# console scripts.
COPY --from=builder /root/.local /root/.local
RUN chmod 755 /root

# Copy bot code. Some source files on the host checkout carry non-standard
# restrictive modes (git only tracks the executable bit, not full mode) —
# COPY preserves those verbatim, so re-assert world-readable regardless of
# what the host filesystem had.
COPY bot/ /app/bot/
RUN chmod -R a+rX /app/bot

# Ensure scripts are in PATH and their --user-installed packages are importable
# (auri's home is /app, not /root, so PYTHONPATH needs this spelled out).
ENV PATH=/root/.local/bin:$PATH \
    PYTHONPATH=/app:/root/.local/lib/python3.11/site-packages

EXPOSE 8080

USER auri

CMD ["python", "-m", "bot.main"]
