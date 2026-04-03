FROM python:3.10-slim

WORKDIR /app

# Install system deps and Playwright (requires root)
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps

# Create non-root user for running the app
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Copy project files and set ownership
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
