# Stage 1 – runtime image
FROM python:3.12-slim AS base

# Install OS dependencies (none required for this simple service but kept for clarity)

# Set up working directory
WORKDIR /app

# Copy application source *after* declaring workdir so relative paths resolve
COPY ./app ./app

# Install Python dependencies – pinning is optional; omit requirements.txt for now
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-dotenv

# Expose the default FastAPI/Uvicorn port
EXPOSE 8000

# Entrypoint – no reload in production image
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
