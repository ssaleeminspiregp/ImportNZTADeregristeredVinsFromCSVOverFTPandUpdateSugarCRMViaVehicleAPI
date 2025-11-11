## Cloud Build Pipeline Reference

This document explains every section of `cloudbuild.yaml` so future developers understand why each command exists and what IAM it needs.

### Substitutions

- `_REGION`: Single place to switch the GCP region for Cloud Run, Artifact Registry, and Scheduler.
- `_SERVICE`: Cloud Run service name (`all_brands_nzta_deregistered_vins_sync`).
- `_REPO`: Artifact Registry repository that stores tagged images.

### Step Breakdown

1. **Ensure GCS bucket exists**  
   Runs `gsutil ls` and, if needed, `gsutil mb` so the landing bucket (`all_brands_nzta_deregistered_vins_temp_DO_NOT_DELETE`) always exists. Needs `roles/storage.admin`.

2. **Ensure Pub/Sub topic exists**  
   Uses `gcloud pubsub topics describe` with a fallback `create` so later steps (subscription + Scheduler) always have the topic available. Needs `roles/pubsub.admin`.

3. **Docker build**  
   Runs `docker build` to package the Python app (no extra IAM beyond Cloud Build default).

4. **Docker push**  
   Pushes the image to Artifact Registry (`roles/artifactregistry.writer`).

5. **Cloud Run deploy**  
   `gcloud run deploy` publishes the image and wires environment variables to secret names, datasets, buckets, and optional SMTP fields. Requires `roles/run.admin` plus `roles/iam.serviceAccountUser` on the runtime SA so Cloud Build can deploy on its behalf.

6. **Grant Pub/Sub invoker**  
   `gcloud run services add-iam-policy-binding` gives `ib4t-integration@adh-data-utopia.iam.gserviceaccount.com` `roles/run.invoker`, which Pub/Sub uses when pushing events.

7. **Recreate Pub/Sub subscription**  
   Fetches the newly deployed service URL and creates (or replaces) the push subscription `all_brands_nzta_deregistered_vins_runner` that targets it. Ensures we always point at the latest HTTPS endpoint when revisions change. Requires `roles/pubsub.admin`.

8. **Create/Update Scheduler job**  
   Makes sure there is a Cloud Scheduler job (`all_brands_nzta_deregistered_vins_weekly`) that publishes to the trigger topic every Tuesday at 03:00 `Pacific/Auckland`. Uses `gcloud scheduler jobs create/update`, so `roles/cloudscheduler.admin` is required.

### Images Section

Stores a reference to the exact Artifact Registry image built (`$SHORT_SHA` tag). Useful for provenance and debugging rollbacks.

### Required Substitutions (set per build/trigger)

- `_FTP_CONFIG_SECRET`, `_SUGAR_CONFIG_SECRET`
- `_FTP_REMOTE_PATH` (directory path) and `_FTP_FILE_PATTERN` (defaults to `*.csv`)
- `_GCS_BUCKET` (defaults to `all-brands-nzta-deregistered-vins-temp-do-not-delete`), plus optional overrides for `_GCS_RAW_PREFIX`, `_GCS_PROCESSED_PREFIX`, `_GCS_ERROR_PREFIX`
- `_ALLOWED_MAKES`
- `_BQ_STAGE_DATASET`, `_BQ_STAGE_TABLE`, `_BQ_STAGE_LOCATION`
- Optional email/SMTP variables if notifications are enabled

### Runtime IAM Expectations

- Cloud Build SA: `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/pubsub.admin`, `roles/cloudscheduler.admin`, `roles/storage.admin`, `roles/artifactregistry.writer`.
- Runtime SA (`ib4t-integration@adh-data-utopia.iam.gserviceaccount.com`): `roles/run.invoker` (granted in step 6), plus the BigQuery/Secret Manager roles already configured.

### Logging

- Builds send logs exclusively to `gs://ib4t-integration-adh-data-utopia-cloudbuild-logs` (`logsBucket` + `options.logging: GCS_ONLY`). Review build output there anytime, and grant the runtime service account write access as needed.

With this setup, a single `gcloud builds submit` (or trigger) builds, deploys, and wires the weekly automation without manual intervention.
