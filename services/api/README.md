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
| `dolab_deleter` | no | Will own the deletion functions (step C). `USAGE` on the schema only |
| `dolab_deploy` | yes | Runs migrations. No rights of its own; may only `SET ROLE` to the two roles above |
| `dolab_app` | yes | The API. Table rights from the migrations, table by table (below) |
| `dolab_reconciler` | yes, 2 connections | The reconciliation job (step C). `USAGE` on the schema only for now |

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
- Photo files are private. They are served only through signed links that expire after 10 minutes; the API hands those links to the owner alone. Deleting a photo, an item or the account deletes the files.
- Items carry `cover_thumb_url`, a signed link to their first photo's thumbnail.

## Not implemented yet

Google and phone sign-in, a real email provider (reset codes are only logged in development), automatic clothing extraction from photos, object storage (photos are on local disk for now), outfit suggestions, market, heritage, admin. The deletion log, deletion functions and reconciliation job (plan 4.10, step C).

## Security notes

- Passwords are hashed with scrypt. Access tokens are signed JWTs that last 15 minutes; refresh tokens are random, stored only as hashes, last 30 days, work once each, and a reused one ends that whole sign-in (theft detection).
- Password reset codes are random, single-use, expire after 30 minutes, and the request answers the same whether or not the email is registered (at most 3 emails an hour per email). Resetting or changing the password signs the user out everywhere else. `DOLAB_JWT_SECRET` must be set in production (the app refuses to start without it).
- Every item, donation and profile query is scoped to the signed-in user. Another user's item id answers 404.
- Body measurements live in a separate `profiles` table and are returned only to their owner.
- Sign-in limits (plan 4.7): 5 wrong passwords for one email from one address block that address for 15 minutes. From 20 failures on an email in an hour (from any addresses), each further attempt waits longer: 30 seconds after the last one, then 1, 2, 4, 8 minutes, at most 15. That is a delay, not a lock, so knowing an email is not enough to lock its owner out.
- The limits are kept in PostgreSQL (`rate_limit_events`), so they hold across server instances and restarts. Each attempt is recorded before it is counted, so parallel guesses cannot pass together. Only keyed hashes are stored, never the email or the address; rows are purged after two hours. Every instance must share the same `DOLAB_JWT_SECRET`, from which the hash key is derived.
- Client address: `DOLAB_TRUSTED_PROXIES` lists the proxies (addresses or CIDR ranges, comma-separated) in front of the API, such as the load balancer and the web server. `X-Forwarded-For` is read only on a connection from one of them, from the right, stepping past trusted proxies only, so an address a browser writes in the header is never used. With the list empty the header is ignored. (The old `DOLAB_TRUSTED_PROXY_HOPS` is refused.)
- CORS is off unless `DOLAB_CORS_ORIGINS` lists exact origins; wildcards are refused.
- A general per-address limit on every route belongs in the reverse proxy (deployment, step H).
- No real secrets or user data belong in this repository.
