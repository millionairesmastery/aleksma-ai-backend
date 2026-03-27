# Aleksma AI — FastAPI Backend
# For production with Modal backend: CadQuery NOT needed in this image
# (all geometry execution happens on Modal.com)

FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps (production — no CadQuery needed when using Modal backend)
COPY requirements.production.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Port
EXPOSE 8000

# Start
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
