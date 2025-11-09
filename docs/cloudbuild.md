## Cloud Build Pipeline Reference

This document explains every section of `cloudbuild.yaml` so future developers understand why each command exists and what IAM it needs.

### Substitutions

- `_REGION`: Single place to switch the GCP region for Cloud Run, Artifact Registry, and Scheduler.
- `_SERVICE`: Cloud Run service name (`all_brands_nzta_deregistered_vins_sync`).
- `_REPO`: Artifact Registry repository that stores tagged images.

### Step Breakdown

1. **Ensure Pub/Sub topic exists**  
   Uses `gcloud pubsub topics describe` with a fallback `create` so later steps (subscription + Scheduler) always have the topic available. Needs `roles/pubsub.admin`.

2. **Docker build**  
   Runs `docker build` to package the Python app. No GCP IAM needed beyond Cloud Build’s default.

3. **Docker push**  
   Pushes the image to Artifact Registry using `docker push`. Requires `roles/artifactregistry.writer` (granted via `Artifact Registry Create-on-Push Writer`).

4. **Cloud Run deploy**  
   `gcloud run deploy` publishes the image and wires environment variables to secret names, datasets, and optional SMTP fields. Requires `roles/run.admin` plus `roles/iam.serviceAccountUser` on the runtime SA so Cloud Build can deploy on its behalf.

5. **Grant Pub/Sub invoker**  
   `gcloud run services add-iam-policy-binding` gives `ib4t-integration@…` `roles/run.invoker`, which Pub/Sub uses when pushing events. Without this, push requests would be rejected with 403.

6. **Recreate Pub/Sub subscription**  
   Fetches the newly deployed service URL and creates (or replaces) the push subscription `all_brands_nzta_deregistered_vins_runner` that targets it. Ensures we always point at the latest HTTPS endpoint when revisions change. Requires `roles/pubsub.admin`.

7. **Create/Update Scheduler job**  
   Makes sure there is a Cloud Scheduler job (`all_brands_nzta_deregistered_vins_weekly`) that publishes to the trigger topic every Tuesday at 03:00 `Pacific/Auckland`. Uses `gcloud scheduler jobs create/update`, so `roles/cloudscheduler.admin` is required.

### Images Section

Stores a reference to the exact Artifact Registry image built (`$SHORT_SHA` tag). Useful for provenance and debugging rollbacks.

### Required Substitutions (set per build/trigger)

- `FTP_CONFIG_SECRET`, `SUGAR_CONFIG_SECRET`
- `FTP_REMOTE_PATH`, `GCS_BUCKET`, `GCS_PREFIX`
- `ALLOWED_MAKES`
- `BQ_STAGE_DATASET`, `BQ_STAGE_TABLE`, `BQ_STAGE_LOCATION`
- Optional email/SMTP variables if notifications are enabled

### Runtime IAM Expectations

- Cloud Build SA: `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/pubsub.admin`, `roles/cloudscheduler.admin`, `roles/artifactregistry.writer`.
- Runtime SA (`ib4t-integration@…`): `roles/run.invoker` (granted in step 5), plus the BigQuery/Secret Manager roles already configured.

With this setup, a single `gcloud builds submit` (or trigger) builds, deploys, and wires the weekly automation without manual intervention.
