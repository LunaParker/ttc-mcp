# Deploying ttc-mcp to Cloud Run (the phase 5b runbook)

**What is deployed:** since 2026-10-18, the service, the Job and its Scheduler in `us-east1`, on image `v2`. Phase 5a built the container and everything the service needs in code (spec 6.9, "Implemented"); phase 5b was the GCP provisioning and the first deploy, `docs/cloud-run.md` is its build specification, and this document is its runbook. Each step is one code block to paste into a terminal, in order, from the repository root with `gcloud` signed in as the project owner. The first run (2026-10-18, spec section 11) fixed what it found: the image builds locally (step 8), ttc.ca's firewall refuses most of the Job's route-detail requests (step 9b), and the console's OAuth pages moved (step 6). Validation items 14 and 15 are closed; item 16 waits for the connectors (step 12).

## Values (spec 6.9)

| Item | Value |
|---|---|
| Project | `$PROJECT_ID`, number `$PROJECT_NUMBER`: step 1 sets both, so run every step in that shell |
| Region | `us-east1` for everything: service, Job, bucket, Artifact Registry, Scheduler |
| Service | Cloud Run `ttc-mcp`: `--allow-unauthenticated` (the gate is the app), min 0 / max 1, 2 GiB, 1 CPU, cpu-boost, timeout 300 s, gen2, CPU allocated only during requests (the default) |
| URL | `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app`; the MCP endpoint is `/mcp`; the OAuth redirect URI is `/auth/callback` |
| Service accounts | `ttc-mcp-sa` (the service: reads the bucket, the three secrets and Firestore); `ttc-mcp-job-sa` (the Job: writes the bucket; the Scheduler runs the Job as it) |
| Secrets | `ttc-mcp-oauth-client-secret`, `ttc-mcp-jwt-signing-key`, `ttc-mcp-storage-fernet-key` |
| Bucket | `gs://ttc-mcp-static-$PROJECT_NUMBER` |
| Image | Artifact Registry repo `ttc-mcp` (docker, us-east1): `us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v1` |
| Job | Cloud Run Job `ttc-mcp-static`, the same image, 2 GiB, 1 CPU, command `/app/.venv/bin/ttc-mcp-build --bucket ttc-mcp-static-$PROJECT_NUMBER` |
| Scheduler | `ttc-mcp-static-daily`, `30 4 * * *` in `America/Toronto`, calling the Job's run endpoint |
| Allowlist | `ALLOWED_EMAILS=lunaparker33@gmail.com` |
| Billing | the project's hard-cap budget and `billing-killswitch`; no new budget wiring; the service should bill nothing at this scale in us-east1 (spec 6.9, cost) |

The two accounts match spec 6.9's Identity row and keep its role split: `roles/storage.objectAdmin` on the bucket for the Job's account only, `roles/storage.objectViewer` for the service's. The Job needs no secret and the service never writes the bucket.

## 1. Account, project, billing headroom

The cap is project-wide, so other services' spend counts against it. Open the budget's spend in the console (Billing > Reports, this month, this project) and make sure there is room; the amounts below are what the budget allows, not what is spent. `gcloud billing budgets list` may need the Billing Budget API (`billingbudgets.googleapis.com`) enabled on the project, and refuses without `--billing-project` ("requires a quota project"); if it still refuses, the console check is enough.

```bash
gcloud auth list
export PROJECT_ID=your-project-id
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud config set project $PROJECT_ID
gcloud billing projects describe $PROJECT_ID --format='value(billingAccountName)'
gcloud billing budgets list --billing-account="$(gcloud billing projects describe $PROJECT_ID --format='value(billingAccountName)' | sed 's#billingAccounts/##')" --billing-project=$PROJECT_ID --format='table(displayName,amount.specifiedAmount.units,thresholdRules[].thresholdPercent)'
```

## 2. APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com firestore.googleapis.com storage.googleapis.com iam.googleapis.com
```

## 3. Service accounts

```bash
gcloud iam service-accounts create ttc-mcp-sa --display-name="ttc-mcp service"
gcloud iam service-accounts create ttc-mcp-job-sa --display-name="ttc-mcp static build Job"
```

## 4. The bucket

```bash
gcloud storage buckets create gs://ttc-mcp-static-$PROJECT_NUMBER --location=us-east1 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding gs://ttc-mcp-static-$PROJECT_NUMBER --member=serviceAccount:ttc-mcp-job-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/storage.objectAdmin
gcloud storage buckets add-iam-policy-binding gs://ttc-mcp-static-$PROJECT_NUMBER --member=serviceAccount:ttc-mcp-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/storage.objectViewer
```

## 5. The three secrets, two of them generated here

The values are piped straight into Secret Manager and never print. Run this from the repository so `uv run` has `cryptography` (a transitive dependency of FastMCP, already in the lock).

```bash
gcloud secrets create ttc-mcp-jwt-signing-key --replication-policy=automatic
gcloud secrets create ttc-mcp-storage-fernet-key --replication-policy=automatic
gcloud secrets create ttc-mcp-oauth-client-secret --replication-policy=automatic
uv run python -c "import secrets; print(secrets.token_hex(32), end='')" | gcloud secrets versions add ttc-mcp-jwt-signing-key --data-file=-
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode(), end='')" | gcloud secrets versions add ttc-mcp-storage-fernet-key --data-file=-
for s in ttc-mcp-jwt-signing-key ttc-mcp-storage-fernet-key ttc-mcp-oauth-client-secret; do gcloud secrets add-iam-policy-binding "$s" --member=serviceAccount:ttc-mcp-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor; done
```

## 6. The OAuth client (console) and its secret

In the console, Google Auth Platform > Clients > Create client (`https://console.cloud.google.com/auth/clients/create?project=$PROJECT_ID`): application type **Web application**, name `ttc-mcp`, one authorised redirect URI `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/auth/callback`, no authorised JavaScript origin, and "Use this client for an AI-powered agent" left unchecked. (If the project has no OAuth consent screen yet, the console asks for one first: create it with your own account as the only test user.) The dialog that follows is the only place the client secret is shown, with a copy button beside it and beside the client ID: copy the secret with its button, run the first line below before anything else touches the clipboard, then clear the clipboard. Copy the client ID (not secret) into the shell variable the deploy step reads.

```bash
pbpaste | tr -d '\n' | gcloud secrets versions add ttc-mcp-oauth-client-secret --data-file=-
pbcopy </dev/null
CLIENT_ID=PASTE-THE-CLIENT-ID.apps.googleusercontent.com
```

## 7. Firestore for the OAuth state

The gate stores OAuth state in the project's default Firestore database (`GOOGLE_CLOUD_PROJECT` names the project; `gate.client_storage` names no database). Create it only if the list shows no `(default)` row; if one exists in another region, use it as it is.

```bash
gcloud firestore databases list --format='table(name,locationId,type)'
gcloud firestore databases create --location=us-east1 --type=firestore-native   # only when no (default) database exists
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:ttc-mcp-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/datastore.user
```

## 8. Artifact Registry and the image

Cloud Build's Docker builder has no BuildKit, so `gcloud builds submit` rejects the Dockerfile's cache mounts (`RUN --mount=type=cache`: "the --mount option requires BuildKit", 2026-10-18). Build the image locally for amd64 and push it. On Apple silicon the amd64 build runs under emulation (OrbStack, or Docker Desktop with Rosetta); the base image pin is the multi-arch index (linux/amd64 and linux/arm64), so it resolves. `configure-docker` adds Artifact Registry's credential helper to `~/.docker/config.json`.

```bash
gcloud artifacts repositories create ttc-mcp --repository-format=docker --location=us-east1 --description="ttc-mcp images"
gcloud auth configure-docker us-east1-docker.pkg.dev
docker buildx build --platform linux/amd64 --push -t us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v1 .
```

## 9. The Job, run once so the bucket has a store before the service starts

The image's command is the server; the Job overrides it with the installed build script (never `python -m ttc_mcp.build`, which from `/app` imports the uncompiled source copy). With `--bucket` (or `TTC_MCP_STATIC_BUCKET`, the flag's default, so `--set-env-vars TTC_MCP_STATIC_BUCKET=ttc-mcp-static-$PROJECT_NUMBER` with no `--args` is the same thing) the build first pulls the bucket's manifest and the feeds it lists into `/data`, then compares CKAN's ETags with that manifest and rebuilds only on change, then pushes the feeds, then the manifest. A Job starts with an empty disk, so the pull is what lets the ETag skip and the overlap pruning see the bucket's state; a failed pull stops the Job. The report carries `pulled` (the feed versions the bucket had) beside `pushed`. The Job's filesystem is in memory: both zips (about 84 MB and 83 MB), the pulled store and the built one (about 139 MB per feed at schema 3, two feeds during a board-period overlap) and the `.part` files count against `--memory` beside the build's own peak (622 MB on the live feed, 2026-10-14), which sums to about 1.0 to 1.2 GB, so the Job runs with `--memory 2Gi`. `--max-retries 1` keeps a broken build from hitting CKAN four times.

```bash
gcloud run jobs deploy ttc-mcp-static --image us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v1 --region us-east1 --service-account ttc-mcp-job-sa@$PROJECT_ID.iam.gserviceaccount.com --command /app/.venv/bin/ttc-mcp-build --args=--bucket,ttc-mcp-static-$PROJECT_NUMBER --memory 2Gi --cpu 1 --task-timeout 900 --max-retries 1
gcloud run jobs execute ttc-mcp-static --region us-east1 --wait
gcloud storage ls -l gs://ttc-mcp-static-$PROJECT_NUMBER/
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="ttc-mcp-static"' --freshness=1h --limit=80 --format='value(textPayload)'
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="ttc-mcp-static" AND jsonPayload.reason:*' --freshness=1h --limit=1 --format='json(jsonPayload)'
```

Expected, first execution (empty bucket), in time order (the log prints newest first, so it reads bottom up): first `[bucket] no manifest in the bucket; ...`, then the two download lines, `[ttcapi] 200 of 235 route details failed: HTTP 403 x200` and `[refresh] ttc.ca service levels: 35 of 235 routes` (the counts vary a little; step 9b), the three `[check]` lines (spec 8 items 3 and 9) and `[bucket] pushed static-S<feed>.sqlite (...)`. The JSON report is the last line on stdout, which Cloud Run logs as `jsonPayload` rather than text, so the last command above shows it: it carries `"reason": "built"`, `"pulled": []` and `"pushed": ["static-S<feed>.sqlite", "manifest.json"]`; no `Traceback`. The bucket then lists `manifest.json` and one `static-S<feed>.sqlite` of about 139 MB (the execution took 1 min 35 s on 2026-10-18, the build itself 68 s). Second execution with nothing new on CKAN: `[bucket] pulled static-S<feed>.sqlite (...)` first, then `"reason": "etags unchanged"`, `"pulled": ["S<feed>"]`, `"pushed": ["manifest.json"]`, and the bucket's feed file is untouched. During a board-period overlap (the first is due about 2026-10-31): the pulled manifest keeps the outgoing feed, the build adds the incoming one and `pushed` lists the new feed file and the manifest. `push` never deletes, so the old feed object stays in the bucket indefinitely, and the manifest keeps listing it until the first rebuild after `Manifest.prune`'s cutoff (a feed that ended before yesterday; prune runs only inside `refresh._register`), so the service keeps pulling both feeds until then.

### 9b. Reseed the service levels from a machine ttc.ca answers

`www.ttc.ca` sits behind an Azure Application Gateway whose firewall lets the first 35 or so route-detail requests from Cloud Run through and answers 403 to the rest, and to every request for a few minutes after (2026-10-18; from a home connection all 235 answer). A rebuild keeps each refused route's service level from the store it pulled, so this is needed once, after the first execution, and again only when a board period brings a route the store has never had a level for: build from your own machine into the bucket, forced, with Application Default Credentials (`gcloud auth application-default login`, once) and the project named for the storage client, which outside Cloud Run cannot find it by itself.

```bash
GOOGLE_CLOUD_PROJECT=$PROJECT_ID uv run ttc-mcp-build --cache "$(mktemp -d)" --bucket ttc-mcp-static-$PROJECT_NUMBER --force
```

Expected: `[bucket] pulled ...`, the two downloads, `[refresh] ttc.ca service levels: 235 of 235 routes` and `[bucket] pushed static-S<feed>.sqlite (...)`. Push and pull compare a feed's `built_at` as well as its size, so a rebuild is pushed even at the same size, and the service takes it at its next cold start or pull. A later Job rebuild logs `[refresh] ttc.ca service levels: 35 of 235 routes; 200 carried over from the loaded store`.

## 10. The service

`BASE_URL` is the origin, no path. `$CLIENT_ID` comes from step 6 (set it again if this is a new shell). One email is one value; to allow a second, `--set-env-vars` needs gcloud's delimiter syntax (`--set-env-vars '^;^ALLOWED_EMAILS=a@x,b@y;BASE_URL=...'`) because it splits on commas.

```bash
gcloud run deploy ttc-mcp --image us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v1 --region us-east1 --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 2Gi --cpu 1 --cpu-boost --timeout 300 --execution-environment gen2 --service-account ttc-mcp-sa@$PROJECT_ID.iam.gserviceaccount.com --set-env-vars BASE_URL=https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app,ALLOWED_EMAILS=lunaparker33@gmail.com,GOOGLE_OAUTH_CLIENT_ID=$CLIENT_ID,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,TTC_MCP_STATIC_BUCKET=ttc-mcp-static-$PROJECT_NUMBER --set-secrets GOOGLE_OAUTH_CLIENT_SECRET=ttc-mcp-oauth-client-secret:latest,JWT_SIGNING_KEY=ttc-mcp-jwt-signing-key:latest,STORAGE_ENCRYPTION_KEY=ttc-mcp-storage-fernet-key:latest
gcloud run services describe ttc-mcp --region us-east1 --format='value(status.url)'
curl -s https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/.well-known/oauth-protected-resource/mcp; echo
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
gcloud run services logs read ttc-mcp --region us-east1 --limit 30
```

Expected: the metadata answers 200 with `"resource": "https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp"`; the bare POST answers `401` (the gate, before any JSON-RPC handling); the log shows `[ttc-mcp] bucket pull: manifest S...` before uvicorn's `Application startup complete`. A missing gate variable (`RuntimeError` from `config.gate_settings`) or a bucket the account cannot read (the Google client's exception from the first pull) shows up here as a container that exits at start with a traceback in the log, and the deploy fails; that is the design (spec 6.9): fix the variable or the binding and deploy again.

## 11. The Scheduler

The Job's run endpoint is Google's own API, so the Scheduler authenticates with an OAuth token for the Job's account (`--oauth-service-account-email`, as Google's "Execute jobs on a schedule" page shows), and that account needs `roles/run.invoker` on the Job. The last two lines fire it once by hand and show the execution; the execution can take a minute to appear (on 2026-10-18 it started about a minute after the run, then reported `etags unchanged` and pushed only the manifest in 21 s).

```bash
gcloud run jobs add-iam-policy-binding ttc-mcp-static --region us-east1 --member=serviceAccount:ttc-mcp-job-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/run.invoker
gcloud scheduler jobs create http ttc-mcp-static-daily --location us-east1 --schedule "30 4 * * *" --time-zone America/Toronto --uri "https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/us-east1/jobs/ttc-mcp-static:run" --http-method POST --oauth-service-account-email ttc-mcp-job-sa@$PROJECT_ID.iam.gserviceaccount.com
gcloud scheduler jobs run ttc-mcp-static-daily --location us-east1
gcloud run jobs executions list --job ttc-mcp-static --region us-east1
```

## 12. The connectors (validation item 16)

- **claude.ai:** Settings > Connectors > Add custom connector: name `TTC`, URL `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp`, the advanced OAuth client ID and secret fields left empty (the gate registers clients dynamically). Connect, sign in with lunaparker33@gmail.com, accept the consent once (`require_authorization_consent="remember"`). Ask "What is the TTC service status right now?" and expect `service_status` to answer. Then connect with another Google account from a second browser profile and expect "This server is restricted; ... is not authorized."
- **Claude Code:** in a project's `.mcp.json`, then `claude` and `/mcp` to authenticate in the browser:

```json
{ "mcpServers": { "ttc": { "type": "http", "url": "https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp" } } }
```

- **iOS:** the connector added on claude.ai is the same one in the Claude iOS app; enable it in a new chat's tools and ask for the service status.

## 13. Every TTC host from us-east1 (validation item 14)

None of the TTC's hosts publish access rules, and four of them 403 the default Python User-Agent; the service sends `ttc-mcp/<version> (+https://github.com/LunaParker/ttc-mcp)` (`TTC_MCP_UA` is not set). Through any of the three clients, call `feed_status` with `probe: true`: that is one live request per source (bustime.ttc.ca's per-stop XML and its two GTFS-RT feeds, ntas.ttc.ca, alerts.ttc.ca, gtfsrt.ttc.ca's alerts, detour and subway trip feeds, www.ttc.ca's route detail). Every `probed` entry must be `null` and no `sources` row may carry `403` or `429` in `status_counts`. Then call `arrivals` for a stop, `next_trains` for a station, `detours` and `service_changes` once each. The Job's log (step 9) proves CKAN from the same region; ttc.ca's route detail answers only the start of the Job's burst of 235 requests (step 9b), while the service's one request per source stays far below that. Before a client is connected, the same calls can run from us-east1 in a one-off Job on the image, through `fastmcp.Client(create_app())` after a `bucket.pull` into `/data` (spec section 11, 2026-10-18). Record the result as a dated line in spec section 11 and close item 14 in 6.9.

## 14. Cold start with the real store (validation item 15)

With `--min-instances 0` the instance goes away after idling; wait fifteen minutes after the last call, then time the first request. The 401 is produced only once the container is up and the pull is done, so an unauthenticated POST measures the cold start. Spec 6.9 expected 3 to 8 s (container start, manifest read, one regional download of about 139 MB per feed); the first measurement was 12.4 s (2026-10-18: the pull ended 8.0 s after the instance started, the gate's setup took 3.0 s more, uvicorn 1.1 s). If it grows well past that, 6.9's alternative is a gen2 GCS volume mount of the bucket with SQLite opened read-only. Record the number in section 11 either way and close item 15.

```bash
curl -s -o /dev/null -w '%{http_code} %{time_total}s\n' -X POST https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
gcloud run services logs read ttc-mcp --region us-east1 --limit 20
```

## 15. Later deploys and rollback

A new tag per deploy, built as in step 8; the service keeps its environment and secrets across deploys unless they are passed again, and the Job takes the same tag. Rollback is traffic to the previous revision, and every traffic move starts the target revision cold (9 to 10 s on 2026-10-18). To see a build change run in the cloud without waiting for a CKAN change, execute the Job once with its arguments overridden: `--args=--bucket,ttc-mcp-static-$PROJECT_NUMBER,--force`. After a deploy that bumps the store schema (`static/schema.SCHEMA_VERSION`, 3 since phase 6), the service answers only once the Job has run on the new image: the server skips a store with another schema (every tool answers `StoreUnavailable`) and the Job rebuilds it (`"reason": "schema changed"`). So update and execute the Job first, then deploy the service straight after, since the old revision skips the newer store at its next bucket pull.

```bash
docker buildx build --platform linux/amd64 --push -t us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v2 .
gcloud run jobs update ttc-mcp-static --image us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v2 --region us-east1
gcloud run jobs execute ttc-mcp-static --region us-east1 --wait   # after a schema bump; otherwise the daily run is enough
gcloud run deploy ttc-mcp --image us-east1-docker.pkg.dev/$PROJECT_ID/ttc-mcp/ttc-mcp:v2 --region us-east1
gcloud run revisions list --service ttc-mcp --region us-east1
gcloud run services update-traffic ttc-mcp --region us-east1 --to-revisions PREVIOUS-REVISION-NAME=100
gcloud run services update-traffic ttc-mcp --region us-east1 --to-latest   # back to the newest revision
```

## 16. Tearing it all down

In this order, so nothing keeps running or billing. The OAuth client is deleted in the console (Google Auth Platform > Clients), and the `ttc`-prefixed collections in Firestore by hand if they should go too.

```bash
gcloud scheduler jobs delete ttc-mcp-static-daily --location us-east1 --quiet
gcloud run jobs delete ttc-mcp-static --region us-east1 --quiet
gcloud run services delete ttc-mcp --region us-east1 --quiet
gcloud storage rm -r gs://ttc-mcp-static-$PROJECT_NUMBER
gcloud artifacts repositories delete ttc-mcp --location=us-east1 --quiet
for s in ttc-mcp-oauth-client-secret ttc-mcp-jwt-signing-key ttc-mcp-storage-fernet-key; do gcloud secrets delete "$s" --quiet; done
gcloud iam service-accounts delete ttc-mcp-sa@$PROJECT_ID.iam.gserviceaccount.com --quiet
gcloud iam service-accounts delete ttc-mcp-job-sa@$PROJECT_ID.iam.gserviceaccount.com --quiet
```
