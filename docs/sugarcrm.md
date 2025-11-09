## SugarCRM Integration Flow

1. **Authenticate** – `SugarCrmClient.authenticate()` sends a password-grant request to `rest/v11_6/oauth2/token` using the credentials stored in Secret Manager (`api-sugarcrm-ib4tintegration-user-config`). The returned access token is cached for all subsequent calls in the same run.

2. **Search for VIN** – For every staged BigQuery record, the pipeline calls `rest/v11_20/VHE_Vehicle` with `filter[0][vin_c][$equals]={VIN}` and `max_num=1`. If no record is returned, the VIN is flagged as a failure (`Vehicle not found in SugarCRM`) and the staged row remains in `pending` status with that error message.

3. **Update record** – When an ID is found, the pipeline issues a `PUT` to `rest/v11_20/VHE_Vehicle/{id}` and passes the update fields as query parameters (no JSON body). Currently the integration sends:
   - `vehicle_status_c = Deregistered`
   - `latest_dereg_date_c` (already normalized to `YYYY-MM-DD`)

The update payload lives in `app/sugar_client.py::update_vehicle`.

### Error handling
- Network/API errors raise exceptions, captured in `_process_single_file`, and the FTP file is moved to the `error` prefix.
- VIN-not-found responses are treated as logical failures (no exception). The staged row is updated with the message for later review.

### Configuration
- OAuth credentials, base URL, and endpoints live in the Secret Manager JSON (`api-sugarcrm-ib4tintegration-user-config`).
- The OAuth platform (`GcpNztaVinDeregIntegration`) is hardcoded in `AppConfig`.
