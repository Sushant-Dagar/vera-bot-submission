FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

# ANTHROPIC_API_KEY is optional — the bot runs fully deterministic without it.
# Set LLM_MODE=off to force deterministic mode even if a key is present.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
