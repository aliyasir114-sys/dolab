# DOLAB API

Backend for دولاب / DOLAB. Python, FastAPI, SQLAlchemy. SQLite for local development and a quick test run; PostgreSQL in production and for the CI run that counts.

## Run locally

```bash
cd services/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional for local use
uvicorn app.main:app --reload # http://127.0.0.1:8000/docs
pytest -q
```

## PostgreSQL (production, and the CI run that counts)

Set up as decided in plan 4.10.1د (decision 20). Every table is in the schema `dolab`.

| Role | Logs in | What it may do |
|---|---|---|
| `dolab_schema_owner` | no | Owns the schema, every table and the migration version table |
| `dolab_deleter` | no | Owns the deletion functions (`dolab.apply_deletion`, `reconcile_apply`, `reconcile_purge_intents`, `reconcile_record_run`; migrations 0005–0007). Column-level `SELECT` on what they read, `DELETE` on the user data tables, `INSERT` on `pending_file_deletions`, `reconciled_entries` and `audit_log`, and `UPDATE (updated_at)` on `users` and `items` only to lock those rows (`FOR UPDATE`), never to write them |
| `dolab_deploy` | yes | Runs migrations. No rights of its own; may only `SET ROLE` to the two roles above |
| `dolab_app` | yes | The API. Table rights from the migrations, table by table (below) |
| `dolab_reconciler` | yes, 2 connections | The reconciliation job (`python -m app.jobs.reconcile`, not scheduled). No right on any table; may only run `reconcile_apply`, `reconcile_purge_intents` and `reconcile_record_run`. `statement_timeout` 30 s |
| `dolab_file_cleaner` | yes, 2 connections | The photo file deletion queue job (`python -m app.jobs.file_deletions`, not scheduled). On `pending_file_deletions` only: `SELECT` and `DELETE`, and `UPDATE` on `attempts`, `next_try_at`, `last_error` and `lease_token` (never `key` or `created_at`). No other table, no function. `statement_timeout` 30 s, `idle_in_transaction_session_timeout` 60 s |

1. **Once per database**, as the infrastructure's admin identity:
   `psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -f dbtools/bootstrap.sql`, then set a password on each login role.
   Safe to run again, also on a live database: it is one transaction and never touches tables or data.
   It also closes the `public` schema, temporary tables and `CONNECT` to everyone not named.
2. **Every deploy, before the server starts**, as `dolab_deploy`:
   `DOLAB_MIGRATION_URL=postgresql+psycopg://dolab_deploy:…@host/dolab alembic upgrade head`.
   The migration switches to `dolab_schema_owner` before touching anything, so `dolab_deploy` never owns an object.
   The whole upgrade is one transaction. Keep these credentials in the deploy job only, never in the API.
   The baseline refuses a database whose DOLAB tables sit outside `dolab` (an old `create_all` database)
   rather than creating empty tables beside the old data.
3. **The server** connects as `dolab_app` (`DOLAB_DATABASE_URL`). Its `search_path` is `pg_catalog, dolab` and
   every query names the schema anyway. On PostgreSQL the server never creates tables; it refuses SQLite when `DOLAB_ENV=production`.

`dolab_app` privileges (migration `0002_app_privileges`, checked by `tests/test_pg_roles.py` against `dbtools/expected_acl.py`):

| Tables | Privileges |
|---|---|
| `users`, `refresh_tokens`, `password_reset_tokens`, `profiles`, `items` | `SELECT`, `INSERT`, `UPDATE`, `DELETE` |
| `item_photos`, `item_status_changes`, `wear_logs`, `donations`, `donation_items` | `SELECT`, `INSERT`, `DELETE` |
| `alembic_version` | none |

Never `TRUNCATE`, `REFERENCES` or `TRIGGER`; no sequences exist (identifiers are UUID4).

**Adding a migration:** `alembic revision -m "…"`, name every object `dolab.<name>`, grant `dolab_app` only what the new
objects need, and add the same grants to `dbtools/expected_acl.py`; the tests fail on any table nobody classified.
Functions owned by `dolab_deleter` are written with `dbtools/deleter_functions.replace_deleter_function`.
Each migration has a `downgrade`, or says why it cannot.

**Running the PostgreSQL tests locally** (needs a PostgreSQL 16 you can administer):

```bash
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -f dbtools/bootstrap.sql   # then set the login roles' passwords to $PW
export DOLAB_MIGRATION_URL=postgresql+psycopg://dolab_deploy:$PW@localhost/dolab
alembic upgrade head && alembic check
DOLAB_DATABASE_URL=postgresql+psycopg://dolab_app:$PW@localhost/dolab \
DOLAB_TEST_PG_ADMIN_URL=postgresql+psycopg://postgres:…@localhost/dolab DOLAB_TEST_PG_PASSWORD=$PW pytest -q
```

SQLite stays for local development and the quick CI run: the server creates its tables, foreign keys are switched on,
and the schema name is dropped. A local SQLite file made before a model change must be deleted and recreated.

## What is implemented (v0.1)

All endpoints are under `/v1` (only `/health` is at the root).

| Area | Endpoints | PRD |
|---|---|---|
| Account | `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET/PATCH /me`, `GET /me/export`, `DELETE /me` | A1, A6 |
| Password | `POST /auth/password/forgot`, `POST /auth/password/reset`, `POST /me/password` | A5 |
| Profile | `GET/PUT/DELETE /me/profile` (all fields optional, never asked at sign-up) | PROF-1 |
| Wardrobe | `GET/POST /items`, `GET/PATCH/DELETE /items/{id}`, `POST /items/{id}/wear`, `GET /items/{id}/history` | W7–W9, S4 |
| Photos | `POST/GET /items/{id}/photos`, `DELETE /items/{id}/photos/{photo_id}`, `GET /media/{photo_id}/{variant}` | W1 |
| Donations | `POST /donations`, `GET /donations` | D1–D3, D5 |
| Dashboard | `GET /me/dashboard` | F1–F3 |

- `GET /items` filters with `?status=`, `?category=`, `?color=`, pages with `?limit=` (1–200, default 50) and `?offset=`, and returns the total in the `X-Total-Count` header.
- Cost per wear (F4) is returned on every item as `cost_per_wear`. Money is stored as exact decimals, never floating point.
- Record ids are random UUIDs, not sequential numbers.
- Wearing an item is counted once per day, so a double tap cannot inflate the count.
- Every status change is kept in a history table, with how it happened (`created`, `user`, `donation`, …).
- Status changes follow one table (`app/transitions.py`, plan 4.9): `PATCH` moves between available, laundry, repair, stored and lost; `donated` is reached only through a donation and never left through `PATCH`; `for_sale` is reserved for the market.
- "Today" is the user's day in their time zone (`PATCH /me` with an IANA name), for wear dates and the dashboard month.
- Photos: JPEG, PNG or WebP up to 8 MB, at most 6 per item. The type is decided from the file's content, not its name. Each photo is re-encoded, which removes location and device data, and a thumbnail is made.
- Photo files are private. They are served only through signed links that expire after 10 minutes; the API hands those links to the owner alone. Deleting a photo, an item or the account deletes their records at once and queues their files in the same transaction. After the commit the server deletes the files and removes them from the queue. A file it cannot delete (storage down, an invalid key, any other error) stays queued for the file deletion queue job, and the request still reports the deletion as done (see "Photo file deletion queue" below).
- Items carry `cover_thumb_url`, a signed link to their first photo's thumbnail.

## Not implemented yet

Google and phone sign-in, a real email provider (reset codes are only logged in development), automatic clothing extraction from photos, outfit suggestions, market, heritage, admin. The background jobs (`app.jobs.orphans`, `app.jobs.file_deletions`, `app.jobs.reconcile`) exist but are not scheduled in any environment: until they are, nothing processes the file deletion queue on its own, and reconciliation stays disabled (see the conditions below). Photos are stored on local disk or in S3-compatible object storage (`DOLAB_STORAGE`); sync tombstones (step D) and the ledger's `profile` entries (v2) are not built yet.

## Security notes

- Passwords are hashed with scrypt. Access tokens are signed JWTs that last 15 minutes; refresh tokens are random, stored only as hashes, last 30 days, work once each, and a reused one ends that whole sign-in (theft detection).
- Password reset codes are random, single-use, expire after 30 minutes, and the request answers the same whether or not the email is registered (at most 3 emails an hour per email). Resetting or changing the password signs the user out everywhere else. `DOLAB_JWT_SECRET` must be set in staging and production (the app refuses to start without it).
- Every item, donation and profile query is scoped to the signed-in user. Another user's item id answers 404.
- Body measurements live in a separate `profiles` table and are returned only to their owner.
- Sign-in limits (plan 4.7): 5 wrong passwords for one email from one address block that address for 15 minutes. From 20 failures on an email in an hour (from any addresses), each further attempt waits longer: 30 seconds after the last one, then 1, 2, 4, 8 minutes, at most 15. That is a delay, not a lock, so knowing an email is not enough to lock its owner out.
- The limits are kept in PostgreSQL (`rate_limit_events`), so they hold across server instances and restarts. Each attempt is recorded before it is counted, so parallel guesses cannot pass together. Only keyed hashes are stored, never the email or the address; rows are purged after two hours. Every instance must share the same `DOLAB_JWT_SECRET`, from which the hash key is derived.
- Client address: `DOLAB_TRUSTED_PROXIES` lists the proxies (addresses or CIDR ranges, comma-separated) in front of the API, such as the load balancer and the web server. `X-Forwarded-For` is read only on a connection from one of them, from the right, stepping past trusted proxies only, so an address a browser writes in the header is never used. With the list empty the header is ignored. (The old `DOLAB_TRUSTED_PROXY_HOPS` is refused.)
- CORS is off unless `DOLAB_CORS_ORIGINS` lists exact origins; wildcards are refused.
- A general per-address limit on every route belongs in the reverse proxy (deployment, step H).
- Photo storage (plan 4.6): `DOLAB_STORAGE=local` keeps files under `DOLAB_MEDIA_DIR` (development and tests); `DOLAB_STORAGE=s3` uses any S3-compatible storage: `DOLAB_S3_BUCKET` (private, no public access), `DOLAB_S3_ENDPOINT_URL` (empty for AWS), `DOLAB_S3_REGION`, and `DOLAB_S3_ENCRYPTION` (`AES256` by default, or `aws:kms`; `none` only for a local MinIO). Credentials come from the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` or the platform's role, never from the repository; give them only `GetObject`, `PutObject`, `DeleteObject` and `ListBucket` on that bucket. Staging and production refuse local storage, unencrypted storage, and custom endpoints that do not use HTTPS. Photos are still served by the API through its signed links, never by storage links, and uploads still pass through the API. If storage is down, reading or uploading a photo answers 503 with `Retry-After`; a photo file that cannot be deleted after its record is gone stays in the file deletion queue (below), and the orphan clean-up job removes any file the queue missed.
- Exports (PRD A6): `GET /v1/me/export` returns everything as JSON; `GET /v1/me/export.zip` returns a ZIP with the same `data.json`, every photo's cleaned original under `photos/<item_id>/<photo_id>.<ext>`, and `manifest.json` (files and sizes, the snapshot's photo count and bytes, `missing_photos`). It reads one consistent snapshot (`REPEATABLE READ` on PostgreSQL), builds in a private temporary file under `DOLAB_EXPORT_TMP_DIR` (mode 0600; put it on an encrypted disk), reads photos in 1 MB blocks, and sends the file only when complete; a failure deletes it and answers 503. Limits, all from the same snapshot and before any photo is read: `DOLAB_EXPORT_MAX_BYTES` (500 MB of photos), `DOLAB_EXPORT_MAX_FILES` (3000 photos), `DOLAB_EXPORT_MAX_DATA_BYTES` (8 MB of data.json), answered with 413. `DOLAB_EXPORT_PER_HOUR` (3 per user, shared by all instances; 429, failed exports do not count), `DOLAB_EXPORT_CONCURRENCY` (2 at once per instance; 503), `DOLAB_EXPORT_BUILD_SECONDS` (120; the build stops and its file is deleted; 503). data.json is written row by row, so an export stays within about 16 MB of memory. Temporary files left by a crash are removed at start-up (untouched for 10 minutes) and every 10 minutes (older than an hour); files still being built or sent are never removed.
- `DOLAB_ENV` must be `development` (the default), `test`, `staging` or `production`; anything else (`prod`, `Production`…) stops the server, so a misspelt name cannot skip the production checks. Staging and production both require a JWT secret, PostgreSQL, encrypted S3 photo storage, and HTTPS for a custom S3 endpoint.
- Deletion ledger (plan 4.10.1): deleting an item, a photo or an account first records an intent (`deletion_intents`), then writes one signed entry (`docs/specs/ledger-entry-v1.md`, Ed25519) and waits for it (at most three tries within an absolute two-second deadline; S3 connect/read timeouts are capped to the remaining budget and SDK retries are disabled); only then is the deletion committed, with the intent removed in the same transaction, and the photo files deleted. If the ledger cannot be written, nothing is deleted and the answer is 503. Settings: `DOLAB_LEDGER_SIGNING_KEY` (32-byte seed, base64url; a secret), `DOLAB_LEDGER_KEY_ID`, `DOLAB_LEDGER_STORAGE` (`s3`, required in staging and production, with `DOLAB_LEDGER_BUCKET`, a bucket of its own where the server has `PutObject` only; or `local` with `DOLAB_LEDGER_DIR`). Staging and production refuse to start without a key or with the published test keys (SIG10); development and tests sign with test key `test-1`. On PostgreSQL the deletion itself is the function `dolab.apply_deletion` (migration 0005, replaced in 0007 to queue the photo files), the same one the reconciliation function `dolab.reconcile_apply` uses; on SQLite its Python twin `app.deletion.python_deletion` (a PostgreSQL test keeps the two identical). The reconciliation job's role `dolab_reconciler` can do nothing but run `reconcile_apply`, `reconcile_purge_intents` and `reconcile_record_run` (no privilege on any table), and `reconcile_apply` only completes a deletion the server recorded an intent for (at most 30 days old). **Before reconciliation is enabled anywhere,** `python -m app.jobs.orphans` must run on a schedule there, or the file deletion queue job must (`python -m app.jobs.file_deletions`); the queue exists but its job is not scheduled anywhere yet, so nothing processes it on its own. **Reconciliation job** (`python -m app.jobs.reconcile [--dry-run]`; not scheduled or enabled anywhere): completes deletions the server recorded in the ledger but could not finish. It connects as `dolab_reconciler` (`DOLAB_RECONCILER_DATABASE_URL`), trusts only the public keys in `DOLAB_LEDGER_PUBLIC_KEYS`, refuses to start if `DOLAB_LEDGER_SIGNING_KEY` is in its environment, and reads the ledger with credentials that may only list and read it (`ops/ledger-reader-policy.json`, with the production bucket's name). A run lists the last 31 days in full, verifies every entry, calls `reconcile_apply` for the valid ones, purges intents older than 31 days only if everything was valid, nothing failed and no entry named an entity its user does not own, and records the run (as failed otherwise). Exit codes: 0 done, 1 invalid entries, errors or an owner mismatch, 2 ledger listing failed, 3 another run in progress, 4 refused to start. **Photo file deletion queue** (plan 4.10.3; migration 0007): every deletion queues its photo files in `pending_file_deletions` in its own transaction (inside `dolab.apply_deletion`; one statement, each key once). `apply_deletion` first locks the item (item deletion) or the account (account deletion) `FOR UPDATE`, and takes the keys from the `DELETE ... RETURNING` of the photo rows. So an upload in progress is waited for and its photo deleted and queued too, an upload that comes after waits and then answers 404 before it saves anything, and no deleted photo row ever misses the queue. Every path takes the user's row before any other lock: an account deletion locks it `FOR UPDATE` (exclusive) first, then deletes the user's photos, then their items; an item or photo deletion, and the item routes that lock an item (photo upload, status change, wear log), first take it `FOR KEY SHARE`, then the item. Because the user's row comes first everywhere, no two of them wait on each other in a cycle. A deletion that loses the race to an account deletion finds its entity gone and answers 404, and its intent is removed. A deletion that PostgreSQL still ends to break a deadlock (`40P01`) is tried once more, in a new transaction; a second one, or any other error, is raised. (`40001` cannot happen: the deletion runs in READ COMMITTED.) Only photo keys exactly as the server makes them (`<user id>/<photo id>_original.<ext>` or `_thumb.jpg`, nothing before or after, no newline) are ever sent to storage, by the server, a failed upload's cleanup, the queue job and the orphan job alike; any other key stays queued. After the commit the server deletes the files and removes their rows; files it could not delete stay queued. Each file gets up to three tries (waits of 0.2 s and 0.6 s); a failing file does not stop the others. The attempt stops when two files in a row have failed, or once the time spent on failures (failed storage calls on a monotonic clock, plus the waits) reaches two seconds; a storage call already running ends within the storage client's own timeouts, so the time lost to a failing storage is two seconds plus at most one such call. Removing the rows waits at most 2 s for a row lock (`lock_timeout`); if it gives up, the rows stay for the job. A new row is first due five minutes after the deletion. `python -m app.jobs.file_deletions [--dry-run]` (not scheduled anywhere) retries the due rows as `dolab_file_cleaner` (`DOLAB_FILE_CLEANER_DATABASE_URL`), which may only read and delete rows of that table and change only `attempts`, `next_try_at`, `last_error` and `lease_token`. It leases a batch of rows in one short statement (FOR UPDATE SKIP LOCKED) under a fresh `lease_token`, and holds no transaction or row lock while it deletes files; rows of a run that stops come back when their 30-minute lease ends. Before each file it renews its lease on that row, and it deletes or reschedules a row only while the token is still its own. The token guards against a takeover, not against the lease running out as such: a run whose lease ran out may still finish a row nobody else has taken (harmless, and intended), but once another run has leased the row under a new token, the first run changes nothing on that row and starts no new storage call for it (`lease lost`, exit 1). The token cannot stop a storage call the first run had already started: a run that passed its last check before the takeover and then stopped for longer than its lease (a suspended machine, say) can still send that `DeleteObject` after the other run. That is harmless: deleting a missing object is a no-op on S3 and MinIO, and a queued key is never reused - it names a photo whose row is gone, and every upload makes a new photo id (uuid4), so the call can never reach a newer file or another item's file. What is not guaranteed: that each file gets exactly one storage call; and anything that brings old objects back under their old keys (a future backup restore) must not restore the files of deleted photos. It uses storage credentials that may only delete photo files. Their policy is rendered per environment from `ops/file-cleaner-policy.template.json` with that environment's photo bucket: `python ops/render_policy.py --photo-bucket "$DOLAB_S3_BUCKET" --ledger-bucket "$DOLAB_LEDGER_BUCKET"` gives one statement, `s3:DeleteObject` on that bucket's objects only. The script refuses wildcards, paths, invalid bucket names and the ledger bucket. Attach the result to an identity only this job uses, and give it nothing else. The job sends only photo keys (`<user id>/<photo id>_original.<ext>` or `_thumb.jpg`) to storage. Any other key stays queued with `last_error` `invalid_key`, set aside for 6 hours and reported; it neither stops the batch nor counts as a storage failure. So does a key the deleter itself refuses (`ValueError`; locally, a photo key that resolves outside the media folder through a symlink). The job refuses to start with `DOLAB_JWT_SECRET` or `DOLAB_LEDGER_SIGNING_KEY` in its environment. Exit codes: 0 done; 1 storage failures, invalid keys, or a row queued for more than 24 hours; 4 refused to start; 5 the database could not be reached (nothing read or changed). Logs carry counts and error classes only. Migration 0007's downgrade locks the queue `ACCESS EXCLUSIVE`, then refuses while it holds any row, so queued files are never dropped: a row being inserted is waited for and counted, and an insert that comes later fails once the table is gone, without committing. **Not yet covered:** clearing body measurements (`DELETE /me/profile`) writes no entry until format v2 adds `profile`, so a restored backup could bring them back.
- Dependencies: `requirements.txt` holds the accepted ranges; `constraints.txt` pins the exact versions CI tests (`pip install -r requirements-dev.txt -c constraints.txt`).
- Orphan photo files (files no record points at, left by a failed delete or upload) are removed by `python -m app.jobs.orphans` (run it on a schedule, daily for example). It lists the whole storage and then the database, and stops without deleting anything (exit 2) if either fails; leaves files younger than an hour alone (uploads in progress); deletes at most `--batch` (500) files per run; only touches photo-shaped keys; and is safe to run again. `--dry-run` reports without deleting. Exit 1 means some deletes failed and will be retried by the next run.
- No real secrets or user data belong in this repository.
