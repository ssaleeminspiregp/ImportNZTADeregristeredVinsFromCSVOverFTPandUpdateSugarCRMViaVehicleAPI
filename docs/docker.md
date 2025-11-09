## Dockerfile Reference

| Line | Instruction | Why it exists |
| --- | --- | --- |
| 1 | `FROM python:3.12-slim` | Base image with current Python runtime plus Debian slim footprint—small attack surface yet compatible with `google-cloud-*` libraries required for BigQuery, Storage, Secret Manager. |
| 4 | `WORKDIR /app` | Keeps code and dependency installs rooted under `/app`, so Cloud Run expects files in a predictable location. |
| 7 | `ENV PYTHONUNBUFFERED=1` | Forces stdout/stderr to flush immediately, ensuring Cloud Run logs appear in order without buffering delays. |
| 10–11 | `COPY requirements.txt ./` then `RUN pip install ...` | Installs Python dependencies in their own layer; Docker can reuse this layer when only app code changes, speeding up builds. The `--no-cache-dir` flag keeps the image small. |
| 14 | `COPY app ./app` | Copies application sources after dependencies so edits only invalidate the final layer. |
| 17 | `CMD ["gunicorn", ...]` | Starts the Flask API via Gunicorn, binding to `0.0.0.0:8080`, the default Cloud Run port. The target `app.main:app` references the Flask object exposed in `app/main.py`. |

### Build/Run Notes
- The Dockerfile intentionally omits non-root users because Cloud Run runs containers with a locked-down runtime user already; add one if running elsewhere.
- If you add OS-level packages (e.g., `libffi`), insert an `apt-get` layer **before** installing pip dependencies to keep caching effective.
- Gunicorn workers default to one per CPU in this setup; adjust via `--workers` if you expect higher concurrency.
