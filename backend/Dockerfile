FROM python:3.11-slim

WORKDIR /app

# Install dependencies in a separate layer for Docker cache efficiency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY static/ static/

# Cloud Run injects $PORT at runtime (default 8080)
ENV PORT=8080

CMD uvicorn main:app --host 0.0.0.0 --port $PORT
