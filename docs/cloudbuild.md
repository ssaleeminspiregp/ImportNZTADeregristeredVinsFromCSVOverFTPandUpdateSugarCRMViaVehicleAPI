## Cloud Build Pipeline Reference

This document explains `cloudbuild.yaml` so future developers understand the deployment steps, schedules, resources, and IAM requirements.

### Substitutions (key ones)
- `_REGION`: Region for Cloud Run/Artifact Registry/Scheduler (australia-southeast1).
- `_REPO`: Artifact Registry repo (`all-brands-nzta-deregistered-vins`).
- `_IMAGE_NAME`: Image name (shared by ingest/sync).
- Services: `_INGEST_SERVICE`, `_SYNC_SERVICE`.
- Pub/Sub: topics `_INGEST_TOPIC`, `_SYNC_TOPIC`; subs `_INGEST_SUB`, `_SYNC_SUB`; DLQ `_DLQ_TOPIC`.
- Scheduler: jobs `_INGEST_JOB`, `_SYNC_JOB`; cron `_INGEST_SCHEDULE` (**04:00 NZT**), `_SYNC_SCHEDULE` (**06:00 NZT**).
- Secrets/vars: FTP/Sugar/email/BQ/GCS/env defaults passed via `--set-env-vars`.

### Step Breakdown
1. **Enable required APIs**  
   `gcloud services enable appengine.googleapis.com cloudscheduler.googleapis.com`.
2. **Ensure Artifact Registry repo exists**  
   Describe/create repo in `_REGION`.
3. **Ensure GCS bucket exists**  
   `gsutil ls` / `gsutil mb` for landing bucket (default `all-brands-nzta-deregistered-vins-temp-do-not-delete`).
4. **Ensure Pub/Sub topics exist**  
   Describe/create ingest and sync topics.
5. **Ensure DLQ topic + IAM**  
   Create `_DLQ_TOPIC` if missing; grant Pub/Sub SA `roles/pubsub.publisher`.
6. **Docker build**  
   Build image tagged with `$SHORT_SHA`.
7. **Docker push**  
   Push to Artifact Registry.
8. **Deploy Ingest Cloud Run**  
   `gcloud run deploy $_INGEST_SERVICE` with `--cpu=2 --memory=1Gi --timeout=900 --min-instances=1 --max-instances=1`, env vars for secrets/buckets/BQ/SMTP, SA `ib4t-integration@adh-data-utopia.iam.gserviceaccount.com`.
9. **Deploy Sync Cloud Run**  
   Same resource/env setup as ingest, `SERVICE_MODE=sync`.
10. **Grant Pub/Sub invoker on services**  
    Add `roles/run.invoker` for the runtime SA on both services.
11. **Recreate ingest push subscription**  
    Delete/recreate `_INGEST_SUB` pointing to current ingest URL, with DLQ and `--max-delivery-attempts=5` (min allowed) and zero retry delay.
12. **Recreate sync push subscription**  
    Same as ingest sub for sync.
13. **Create/Update Scheduler jobs**  
    Ingest job at 04:00 NZT to `_INGEST_TOPIC`; Sync job at 06:00 NZT to `_SYNC_TOPIC`.

### Required Substitutions (set per build/trigger)
- Secrets: `_FTP_CONFIG_SECRET`, `_SUGAR_CONFIG_SECRET`, optional `_EMAIL_SERVER_CONFIG_SECRET`.
- Data: `_FTP_REMOTE_PATH`, `_FTP_FILE_PATTERN` (default `*.csv`), `_GCS_BUCKET`/prefix overrides, `_ALLOWED_MAKES`, `_BQ_STAGE_DATASET`, `_BQ_STAGE_TABLE`, `_BQ_STAGE_LOCATION`.
- Email/SMTP vars if notifications enabled.

### Runtime IAM Expectations
- Cloud Build SA: `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/pubsub.admin`, `roles/cloudscheduler.admin`, `roles/storage.admin`, `roles/artifactregistry.writer`.
- Runtime SA (`ib4t-integration@adh-data-utopia.iam.gserviceaccount.com`): `roles/run.invoker` (granted in step 10), plus existing BQ/Secret Manager access.

### Logging
- Build logs go to `gs://ib4t-integration-adh-data-utopia-cloudbuild-logs` (`options.logging: GCS_ONLY`).

With this setup, `gcloud builds submit` builds, deploys both Cloud Run services with resources/secrets/envs, recreates Pub/Sub push subs (with DLQ), and updates Scheduler jobs for daily automation.
