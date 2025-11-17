# Base image: lightweight Python with system libs for Cloud Run.
FROM python:3.12-slim

# All subsequent commands run from /app to keep paths predictable.
WORKDIR /app

# Force stdout/stderr to flush immediately (better Cloud Run logging).
ENV PYTHONUNBUFFERED=1

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source after dependencies to avoid reinstalling.
COPY app ./app

# Launch the Flask app via gunicorn on the Cloud Run port with a single worker to keep one execution environment active.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "180", "app.main:app"]
