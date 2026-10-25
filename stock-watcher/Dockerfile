FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY watcher.py .

# Environment defaults
ENV PYTHONUNBUFFERED=1

CMD ["python", "watcher.py"]
