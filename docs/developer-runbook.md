## NZTA Deregistered VIN Developer Runbook

### Overview
- **Ingest**: Cloud Run service downloads CSVs from FTP, uploads to GCS (`raw` → `processed`/`error`), stages rows in BigQuery, and emails a summary.
- **Sync**: Cloud Run service reads staged rows, finds/updates VINs in SugarCRM, marks rows pushed/failed in BigQuery, and emails a summary.
- Triggers: Cloud Scheduler → Pub/Sub → Cloud Run.

### Services & Schedules (NZT)
- **Ingest Cloud Run**: `all-brands-nzta-deregistered-vins-ingest` (CPU 2, RAM 1Gi, min/max 1). Triggered daily **04:00 NZT** via Pub/Sub topic `all-brands-nzta-deregistered-vins-ingest-trigger`.
- **Sync Cloud Run**: `all-brands-nzta-deregistered-vins-sync` (CPU 2, RAM 1Gi, min/max 1). Triggered daily **06:00 NZT** via Pub/Sub topic `all-brands-nzta-deregistered-vins-sync-trigger`.
- **Links**:
  - Ingest service: https://console.cloud.google.com/run/detail/australia-southeast1/all-brands-nzta-deregistered-vins-ingest?project=adh-data-utopia
  - Sync service: https://console.cloud.google.com/run/detail/australia-southeast1/all-brands-nzta-deregistered-vins-sync/observability/metrics?project=adh-data-utopia
  - Ingest Scheduler job: https://console.cloud.google.com/cloudscheduler/jobs/edit/australia-southeast1/all-brands-nzta-deregistered-vins-ingest-daily?hl=en&project=adh-data-utopia
  - Sync Scheduler job: https://console.cloud.google.com/cloudscheduler/jobs/edit/australia-southeast1/all-brands-nzta-deregistered-vins-sync-daily?hl=en&project=adh-data-utopia

### Data Flow
1) **Ingest**: Scheduler → Pub/Sub → Cloud Run (ingest) → FTP download → GCS `raw` → validate CSV → stage rows to BigQuery `ds_nzta.dl_all_brands_deregistered_vins_stage` → move GCS to `processed`/`error` → email summary.
2) **Sync**: Scheduler → Pub/Sub → Cloud Run (sync) → fetch pending staged rows (age gate) → SugarCRM GET/PUT → update BigQuery status → email summary.
3) **Secrets**: FTP/Sugar/Email loaded from Secret Manager.
4) **Notifications**: SMTP; header/no-file alerts and ingest/sync summaries.

### Configuration / Env Vars
- **Secrets**: `FTP_CONFIG_SECRET`, `SUGAR_CONFIG_SECRET`, optional `EMAIL_SERVER_CONFIG_SECRET`.
- **FTP**: `FTP_REMOTE_PATH`, `FTP_FILE_PATTERN` (default `*.csv`), timeout 20s.
- **GCS**: `GCS_BUCKET` (default `all-brands-nzta-deregistered-vins-temp-do-not-delete`), prefixes `GCS_RAW_PREFIX=raw`, `GCS_PROCESSED_PREFIX=processed`, `GCS_ERROR_PREFIX=error`.
- **BigQuery**: `BQ_STAGE_DATASET=ds_nzta`, `BQ_STAGE_TABLE=dl_all_brands_deregistered_vins_stage`, `BQ_STAGE_LOCATION=australia-southeast1`.
- **Allowed makes**: `ALLOWED_MAKES` (default `HYUNDAI|ISUZU|RENAULT`).
- **Sync age gate**: `SYNC_MIN_PENDING_AGE_MINUTES` (default 30 to avoid streaming buffer conflicts).
- **Email**: `SMTP_HOST/PORT/USERNAME/PASSWORD/USE_TLS`, recipients via `EMAIL_RECIPIENTS`, `SUCCESS_EMAIL_RECIPIENTS`, `ERROR_EMAIL_RECIPIENTS` (pipe/comma separated, deduped).
- **No-file throttle**: `NO_FILE_NOTIFY_COOLDOWN_SEC` (default 600).
- **Console shortcuts**:
  - BigQuery stage table: https://console.cloud.google.com/bigquery?hl=en&inv=1&invt=AbxO9A&project=adh-data-utopia&ws=!1m10!1m4!1m3!1sadh-data-utopia!2sbquxjob_25151bdd_19a952f7cef!3sUS!1m4!4m3!1sadh-data-utopia!2sds_nzta!3sdl_all_brands_deregistered_vins_stage
  - Pub/Sub topics: ingest https://console.cloud.google.com/cloudpubsub/topic/detail/all-brands-nzta-deregistered-vins-ingest-trigger?hl=en&project=adh-data-utopia ; sync https://console.cloud.google.com/cloudpubsub/topic/detail/all-brands-nzta-deregistered-vins-sync-trigger?hl=en&project=adh-data-utopia ; DLQ https://console.cloud.google.com/cloudpubsub/topic/detail/all-brands-nzta-deregistered-vins-dlq?hl=en&project=adh-data-utopia
  - Cloud Build triggers: https://console.cloud.google.com/cloud-build/triggers?hl=en&project=adh-data-utopia
  - Cloud Build history: https://console.cloud.google.com/cloud-build/builds?hl=en&project=adh-data-utopia

### File Handling
- CSVs read with `utf-8-sig` (BOM stripped). Headers must match **exactly**: `VEHICLE_MAKE,VEHICLE_MODEL,VIN,DEREG_DATE,REGNO`.
- Values cleaned (BOM/whitespace removed). Disallowed makes or blank VINs are skipped; dates normalized.
- Header failure: one alert email (to success+failure lists); file moves to error GCS.
- No-file case: one notification; throttled by cooldown.

### Running Manually
- Trigger ingest: publish `{}` to topic `all-brands-nzta-deregistered-vins-ingest-trigger`.
- Trigger sync: run Scheduler job or publish `{}` to topic `all-brands-nzta-deregistered-vins-sync-trigger`.
- Check staged counts:
  ```bash
  bq query --nouse_legacy_sql \
  'SELECT status, COUNT(*) FROM `adh-data-utopia.ds_nzta.dl_all_brands_deregistered_vins_stage` GROUP BY status'
  ```
  Per-VIN status:
  ```bash
  bq query --nouse_legacy_sql \
  "SELECT vin, status, COUNT(*) AS count FROM `adh-data-utopia.ds_nzta.dl_all_brands_deregistered_vins_stage` GROUP BY vin, status ORDER BY vin"
  ```

### Deployment (Cloud Build)
- `cloudbuild.yaml`:
  - Builds/pushes image, deploys both services with `--cpu=2 --memory=1Gi --min-instances=1 --max-instances=1`.
  - Recreates Pub/Sub subs (with DLQ) and updates Scheduler jobs (cron above).
- Adjust schedules via `_INGEST_SCHEDULE` / `_SYNC_SCHEDULE` substitutions (currently 04:00 / 06:00 NZT).
- Repo links:
  - Cloud Build repo: https://console.cloud.google.com/cloud-build/repositories/1st-gen;region=australia-southeast1?project=adh-data-utopia
  - Cloud Build triggers: https://console.cloud.google.com/cloud-build/triggers?hl=en&project=adh-data-utopia
  - Cloud Build history: https://console.cloud.google.com/cloud-build/builds?hl=en&project=adh-data-utopia

### Current substitution values (adh-data-utopia)
- `_ALLOWED_MAKES`: `HYUNDAI|ISUZU|RENAULT`
- `_BQ_STAGE_DATASET`: `ds_nzta`
- `_BQ_STAGE_TABLE`: `dl_all_brands_deregistered_vins_stage`
- `_EMAIL_SENDER`: `no-reply@ib4t.co`
- `_EMAIL_SERVER_CONFIG_SECRET`: `email-server-services-ib4t-config`
- `_ERROR_EMAIL_RECIPIENTS`: `mmohammed@hyundai.co.nz|sdsouza@ib4t.co|ssaleem@ib4t.co`
- `_FTP_CONFIG_SECRET`: `ftp-hyundai-dereg-user-config`
- `_FTP_FILE_PATTERN`: `*.csv`
- `_FTP_REMOTE_PATH`: `/`
- `_GCS_BUCKET`: `all-brands-nzta-deregistered-vins-temp-do-not-delete`
- `_GCS_ERROR_PREFIX`: `error`
- `_GCS_PROCESSED_PREFIX`: `processed`
- `_GCS_RAW_PREFIX`: `raw`
- `_LOG_LEVEL`: `DEBUG`
- `_SMTP_DEBUG`: `true`
- `_SMTP_USE_TLS`: `true`
- `_SUCCESS_EMAIL_RECIPIENTS`: `mmohammed@hyundai.co.nz|ssaleem@ib4t.co`
- `_SUGAR_CONFIG_SECRET`: `api-sugarcrm-ib4tintegration-user-config`

### Troubleshooting
- **Sync processed 0 rows**: Pending rows may be younger than `SYNC_MIN_PENDING_AGE_MINUTES`. Lower it or wait.
- **Streaming buffer errors**: Age gate + retries are in place; ensure BQ location `australia-southeast1`.
- **SugarCRM drops**: Connection/read timeouts are retried; repeated failures are marked in BigQuery and email summaries.
- **Worker timeouts/SIGKILL**: Long runs on single instance; reduce backlog (dedupe staged rows), increase timeout/resources/instances if needed.

### Sample Data
- `Sample.csv` in repo shows expected format; only allowed makes are staged (e.g., HYUNDAI rows). Others are skipped.
