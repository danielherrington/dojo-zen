FROM python:3.12-slim

WORKDIR /app

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PORT=8080
ENV USE_FIRESTORE=true

# Copy package configuration, docs, and source
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["sh", "-c", "python -m uvicorn dojo.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
