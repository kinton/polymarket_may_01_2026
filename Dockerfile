# Multi-stage Dockerfile for Polymarket Trading Bot
# Uses uv for fast dependency management

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

# Copy dependency files first (for layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies (cached until pyproject.toml/uv.lock change)
RUN uv sync --frozen --no-install-project --no-dev

# Copy application code (invalidates cache only on code changes)
COPY . .

# ============================================================================
# Final runtime image
# ============================================================================
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY --from=builder /app /app

# Create log directory
RUN mkdir -p /app/log

# Set Python path to use venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Health check (optional, checks if Python is working)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default command (can be overridden in docker-compose)
CMD ["python", "main.py", "--live"]
