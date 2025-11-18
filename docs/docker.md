## Dockerfile Reference

| Line | Instruction | Why it exists |
| --- | --- | --- |
| 1 | `FROM python:3.12-slim` | Base Python on Debian slim; small footprint, compatible with `google-cloud-*` libs. |
| 4 | `WORKDIR /app` | Standardizes paths and install locations inside the container. |
| 7 | `ENV PYTHONUNBUFFERED=1` | Flush stdout/stderr immediately so Cloud Run logs stay in order. |
| 10-11 | `COPY requirements.txt ./` then `RUN pip install ...` | Install deps in their own layer for caching; `--no-cache-dir` keeps the image small. |
| 14 | `COPY app ./app` | Copy source after deps so code changes only rebuild the final layer. |
| 17 | `CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--timeout", "180", "app.main:app"]` | Start the Flask app via Gunicorn on the Cloud Run port; single worker and 180s timeout match service config. |

### Build/Run Notes
- Cloud Run already runs as a restricted user; add a non-root user layer only if you deploy elsewhere.
- Add any `apt-get` packages **before** the pip install layer to preserve caching.
- The container expects `app.main:app` and port `8080`; adjust CMD if your entrypoint changes.
