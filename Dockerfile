FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source first so pip can find the package
COPY pyproject.toml .
COPY src/ src/

# Install dependencies
RUN pip install --no-cache-dir .

# Download YAMNet model
RUN mkdir -p models && \
    curl -L 'https://tfhub.dev/google/lite-model/yamnet/classification/tflite/1?lite-format=tflite' \
    -o models/yamnet.tflite

# Create data directory for SQLite
RUN mkdir -p /app/data

ENV PYTHONPATH=/app/src

EXPOSE 5000

CMD ["python", "-m", "barkup.main"]
