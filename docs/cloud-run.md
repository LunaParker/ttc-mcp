# Cloud Run build specification

**Status:** built 2026-10-18. Phase 5a gave the image, the in-process Google sign-in gate and the bucket-backed timetable, phase 6 added the trip planner's index (store schema 3) and its live overlays, and phase 5b, the build this document specifies, provisioned Google Cloud and deployed the service, the Job and its Scheduler on image `v2`. Of "Done means" below, items 1, 2 and 4 hold, 6 held in a one-off Job on the image but not yet through a client, and 5 was measured at 12.4 s, over the expectation (spec section 11); 3 holds for claude.ai, with Claude Code, iOS and a refused second account still to check, 7 for the board-period changeover around 2026-10-31 and 8 for a week of billing.

How the documents divide the work:

- [spec.md](spec.md) section 6.9 is normative: the decision, the cloud shape and validation items 14 to 16. When this document and the spec disagree, the spec wins and this document gets fixed.
- This document is the build specification: what gets provisioned, how it is sized, the order deploys follow, what done means and what is still open.
- [deploy.md](deploy.md) is the runbook that carries it out, one block of commands per step, with the project's values as `$PROJECT_ID` and `$PROJECT_NUMBER`.

## What gets built

One image, used twice, in `us-east1`:

| Resource | Name | Role |
|---|---|---|
| Cloud Run service | `ttc-mcp` | The MCP server over streamable HTTP at `/mcp`, behind Google sign-in. Pulls the timetable from the bucket before it listens and every 6 hours; never contacts the City's portal |
| Cloud Run Job | `ttc-mcp-static` | `ttc-mcp-build --bucket`: pulls the bucket's timetable, checks the City's portal by ETag, builds only on a change or a schema change, pushes the feeds then the manifest |
| Cloud Scheduler | `ttc-mcp-static-daily` | Runs the Job at 04:30 `America/Toronto` |
| Cloud Storage bucket | `gs://ttc-mcp-static-$PROJECT_NUMBER` | `manifest.json` and one `static-<feed_version>.sqlite` per timetable; uniform access, public access prevented |
| Firestore | the project's `(default)` database | OAuth state, sanitized, collections prefixed `ttc`, Fernet-encrypted, so sign-ins survive scaling to zero |
| Secret Manager | `ttc-mcp-oauth-client-secret`, `ttc-mcp-jwt-signing-key`, `ttc-mcp-storage-fernet-key` | The gate's three secrets; the server holds no third-party credential |
| Service accounts | `ttc-mcp-sa`, `ttc-mcp-job-sa` | The service reads the bucket, the secrets and Firestore; the Job writes the bucket and is the Scheduler's identity |
| Artifact Registry | repo `ttc-mcp` (docker) | One image tag per deploy, shared by the service and the Job |
| OAuth client | web application `ttc-mcp` | Redirect URI `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/auth/callback` |

Roles keep the split spec 6.9 sets: `roles/storage.objectAdmin` on the bucket for the Job's account only, `roles/storage.objectViewer` for the service's, `roles/secretmanager.secretAccessor` on the three secrets and `roles/datastore.user` for the service, `roles/run.invoker` on the Job for the Job's account. The service is `--allow-unauthenticated` at the Cloud Run level because the gate is the application: every request needs a verified Google account on the allowlist.

## How the pieces talk

1. At 04:30 the Scheduler runs the Job. The Job starts on an empty in-memory disk, pulls the manifest and the feeds it lists, compares the portal's ETags and the stores' schema with them, and builds only when something changed. A build downloads the two GTFS zips, asks ttc.ca for each route's service level, writes the store with the planner's index, and pushes the feeds first and the manifest last, so a reader never sees a manifest naming a feed that has not landed.
2. The service pulls the bucket before it listens and again every `TTC_MCP_PULL_S` seconds (6 hours). A pull keeps a file the local manifest lists with the same `built_at` and size, stages a download in a `.part` file, writes the manifest after the feeds and deletes local stores the manifest no longer lists. The server reopens the store on its next tool call and drops its in-memory timetables and overlays with it.
3. A tool call fetches only what it needs from the TTC, when asked. `connections` loads the timetable's index once per feed (about 0.1 s) and, for a question within an hour of now, fetches the two trip-update feeds and builds its overlay.

## Sizing

### Service: 2 GiB, 1 vCPU, min 0, max 1

Cloud Run's container filesystem lives in memory, so the timetable files in `/data` count against the instance's memory. The worst case is a board-period changeover, when two timetables are loaded:

| Item | Memory | Source |
|---|---|---|
| Two stores in `/data` | 2 x 139 MB | S1000542 at schema 3, built 2026-10-15 |
| A pull's `.part` file while a new feed lands | 139 MB, transient | only when a feed is new or rebuilt; files of the same build are kept |
| Two planner indexes | 2 x 66 MB | loader measurement, 2026-10-14 |
| The process: caches, live feeds, both day overlays | about 150 MB | the live benchmark peaked at 217 MB with one index loaded (2026-10-15) |
| **Total** | **about 700 MB** | about 1.4 GB of headroom under 2 GiB |

That would fit 1 GiB with about 300 MB to spare, but the service runs with 2 GiB, the same size as the Job (the maintainer's call, 2026-10-15). At 1 vCPU, 2 GiB uses up the free tier's GiB-seconds (360,000) at the same rate as its vCPU-seconds (180,000), so the free tier covers the same number of instance-hours as it did at 1 GiB. Past the free tier, an instance-second costs about 10% more.

Latency the service should show:

- First call after idle: 3 to 8 seconds expected (container start, manifest read, one regional download of about 139 MB per feed; spec 6.9). Measuring it is validation item 15.
- `connections`: the committed benchmark's p95 is 28 ms for one instant, 144 ms for a 30-minute window and 224 ms for arrive-by, inside the 250 ms budget (spec 6.10, validation item 17). A question within the hour also pays the overlay build when the trip feeds are fresh, about 80 to 120 ms, so live calls with the overlay built cold measured 158 to 354 ms p95 at night (spec section 11, 2026-10-15). The request timeout stays at 300 s.
- `--cpu-boost` helps the cold start; CPU is allocated only during requests, which is what keeps an idle instance free.

### Job: 2 GiB, 1 vCPU, task timeout 900 s, one retry

The Job's disk is in memory too, and a rebuild holds both zips (about 84 and 83 MB), the pulled store and the new one (139 MB each, plus a second pulled store during a changeover) beside the build's own peak (622 MB on the live feed, 2026-10-14). That is about 1.0 to 1.2 GB, hence 2 GiB. A build takes about 18 s from the zips on a laptop, about a minute with the portal download and the ttc.ca calls. `--max-retries 1` keeps a broken build from hitting the portal repeatedly.

### Bucket

One object per timetable, about 139 MB each, plus the manifest. Nothing deletes old feed objects (CLAUDE.md's known deferred items), so the bucket grows by one store per board period; clean it by hand before it nears the 5 GB always-free allowance.

## Configuration

Baked into the image: `TTC_MCP_TRANSPORT=http`, `TTC_MCP_SKIP_REFRESH=1`, `TTC_MCP_CACHE=/data`, `PORT=8080`, and the command `/app/.venv/bin/ttc-mcp`.

Set on the service:

| Variable | Value |
|---|---|
| `BASE_URL` | `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app` (origin, no path) |
| `ALLOWED_EMAILS` | the Google account allowed in, `lunaparker33@gmail.com` |
| `GOOGLE_OAUTH_CLIENT_ID` | the OAuth client's id; setting it turns the gate on |
| `GOOGLE_CLOUD_PROJECT` | `$PROJECT_ID`, so OAuth state goes to Firestore |
| `TTC_MCP_STATIC_BUCKET` | `ttc-mcp-static-$PROJECT_NUMBER` |
| `GOOGLE_OAUTH_CLIENT_SECRET`, `JWT_SIGNING_KEY`, `STORAGE_ENCRYPTION_KEY` | from Secret Manager, `:latest` |

Left unset on purpose: `TTC_MCP_UA` (the default names the project, and four TTC hosts refuse Python's own), `TTC_MCP_PULL_S` (6 hours), `TTC_MCP_NOW`.

The Job overrides the command with `/app/.venv/bin/ttc-mcp-build --bucket ttc-mcp-static-$PROJECT_NUMBER` and needs no secret. Never `python -m ttc_mcp...`: inside `/app` it would import the source copy instead of the installed package.

A half-configured gate refuses to start, and so does a bucket the service cannot read: both show as a container that exits at start, which is the intended failure.

## Deploy order

**First deploy:** image, then the Job, executed once so the bucket has a timetable, then the service, then the Scheduler, then the connectors. A service started on an empty bucket runs, but every tool except `feed_status` says the timetable isn't built yet.

**Later deploys:** a new image tag, then update the Job to it, then deploy the service. When the deploy changes the store schema (`static/schema.SCHEMA_VERSION`, 3 since phase 6), execute the Job on the new image and wait for it before deploying the service: a server skips a store whose schema differs from its own, so the service answers only once the Job has rebuilt the store (`"reason": "schema changed"`).

**Rollback:** traffic to the previous revision. Across a schema change, roll back the Job too and execute it before moving the traffic, because an older server skips a newer store just as a newer one skips an older store.

## Security

- The gate runs in-process: FastMCP's Google provider with dynamic client registration and remembered consent, then an email allowlist checked on every request. Anything else gets a JSON-RPC authorization error, not a 500.
- The only secrets are the gate's own, in Secret Manager, readable by the service's account only. The Job holds none.
- The data is public and read-only, and no tool can act on the TTC or on anyone's accounts.
- The bucket is private with uniform access; the service can only read it.
- The HTTP transport is stateless JSON, so no session state lives on the instance and an idle client cannot keep it running.

## Cost

Sized for one person inside Google Cloud's free tiers (figures to re-check at deploy time, spec 6.9): Cloud Run's monthly free requests, vCPU-seconds and GiB-seconds cover a request-billed service and a daily Job that usually finishes in under a minute; a bucket under 1 GB sits inside the 5 GB always-free storage; Cloud Scheduler gives three free jobs; one image fits the Artifact Registry allowance; Firestore traffic for sign-ins is negligible. With no free tier at all, a month would cost about $0.10 (spec 6.10). The project's hard-cap budget and `billing-killswitch` cover it, but the cap is project-wide: check its headroom before provisioning.

## Done means

1. **The Job works.** The first execution reports `"reason": "built"`, `"pulled": []`, `"pushed": ["static-S<feed>.sqlite", "manifest.json"]`, logs the build's `[check]` lines (`patterns split for overtaking`, `stop_times with arrival != departure: 0`) and no traceback; the bucket lists a store of about 139 MB. A second execution reports `"etags unchanged"` and pushes only the manifest.
2. **The service starts behind the gate.** `/.well-known/oauth-protected-resource/mcp` answers 200, a bare POST to `/mcp` answers 401, and the log shows the bucket pull before the server's startup line.
3. **Every client connects (validation item 16).** claude.ai's custom connector, Claude Code and the iOS app each sign in with the allowlisted account and get a `service_status` answer; a second Google account is refused.
4. **Every TTC host answers from us-east1 (item 14).** `feed_status` with `probe: true` shows every `probed` entry `null` and no 403 or 429 in any `status_counts`, the two trip-update feeds (`bustime trips`, `gtfsrt trips/subway`) included; `arrivals`, `next_trains`, `detours` and `service_changes` each answer once.
5. **Cold start is measured (item 15).** The first request after 15 idle minutes, timed; 3 to 8 s expected. Well above that, consider spec 6.9's alternative, a GCS volume mount with SQLite opened read-only.
6. **The trip planner works hosted.** During service hours, `connections` from Cherry St and Front St East (43.6527, -79.3581) to Union answers options with exact times, `meta.source` names `bustime trips`, and the first call after a cold start answers well inside the timeout.
7. **Memory holds through a changeover.** The service's memory utilisation stays under about 35% of 2 GiB (the 700 MB estimate above) while two timetables are loaded (the next changeover is due around 2026-10-31).
8. **It costs nothing.** After a week, the billing report shows no charge for Cloud Run, Cloud Storage, Scheduler, Artifact Registry or Firestore beyond the free tiers.

Each result goes into spec section 11 as a dated line, and items 14 to 16 close in spec 6.9.

## Open risks

- **The 6-hour pull on a warm instance.** The pull runs in a worker thread outside any request, and with CPU allocated only during requests Cloud Run throttles that work. The instance usually scales to zero long before 6 hours and the next cold start pulls anyway; check in the logs that pulls complete. If they stall, decide then between living with it (a warm instance catches up at its next cold start) and CPU always allocated, which bills.
- **The TTC's hosts from cloud addresses.** None publish access rules, and four refuse Python's default User-Agent. If one refuses Google's egress addresses, its tools degrade as the README's "When service is degraded" describes; there is no workaround in scope. On 2026-10-18 every source answered the service's calls from us-east1, but `www.ttc.ca`'s firewall refused all but about 35 of the Job's 235 route-detail requests, so a rebuild keeps refused routes' service levels from the pulled store and runbook step 9b reseeds them once from a home connection.
- **The overlay's cold build.** Each fresh fetch of the trip feeds costs about 100 ms before planning. Acceptable for one person; CLAUDE.md lists three levers not taken.
- **Old feed objects in the bucket** accumulate until cleaned by hand.
- **Cloud Build and BuildKit cache mounts.** Cloud Build rejected them on 2026-10-18, so images build locally for amd64 and push (runbook step 8).
- **Free-tier figures** change; re-check them before the deploy.

## Not part of this build

- More than one instance or user: `max-instances 1` and a single allowlisted account (spec 6.9).
- A custom domain: the `run.app` URL is deterministic, so `BASE_URL` and the redirect URI are known before the first deploy.
- Automated deploys: the runbook deploys by hand, one image tag at a time.
- Alerting beyond Cloud Run's own logs and metrics.

## After the deploy

Update the first line of `deploy.md`, the "nothing is deployed" sentences in spec 6.9 and `CLAUDE.md`, the README's note that the runbook hasn't been run, and add the section 11 entry with the results above. The project's real values stay in the untracked `CLAUDE.local.md`.
