# DOLAB API

Backend for دولاب / DOLAB. Python, FastAPI, SQLAlchemy. SQLite for local development and tests, PostgreSQL in production.

## Run locally

```bash
cd services/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # optional for local use
uvicorn app.main:app --reload # http://127.0.0.1:8000/docs
pytest -q
```

## What is implemented (v0.1)

All endpoints are under `/v1` (only `/health` is at the root).

| Area | Endpoints | PRD |
|---|---|---|
| Account | `POST /auth/register`, `POST /auth/login`, `GET /me`, `GET /me/export`, `DELETE /me` | A1, A6 |
| Profile | `GET/PUT/DELETE /me/profile` (all fields optional, never asked at sign-up) | PROF-1 |
| Wardrobe | `GET/POST /items`, `GET/PATCH/DELETE /items/{id}`, `POST /items/{id}/wear`, `GET /items/{id}/history` | W7–W9, S4 |
| Photos | `POST/GET /items/{id}/photos`, `DELETE /items/{id}/photos/{photo_id}`, `GET /media/{photo_id}/{variant}` | W1 |
| Donations | `POST /donations`, `GET /donations` | D1–D3, D5 |
| Dashboard | `GET /me/dashboard` | F1–F3 |

- `GET /items` filters with `?status=`, `?category=`, `?color=`, pages with `?limit=` (1–200, default 50) and `?offset=`, and returns the total in the `X-Total-Count` header.
- Cost per wear (F4) is returned on every item as `cost_per_wear`. Money is stored as exact decimals, never floating point.
- Record ids are random UUIDs, not sequential numbers.
- Wearing an item is counted once per day, so a double tap cannot inflate the count.
- Every status change is kept in a history table.
- Photos: JPEG, PNG or WebP up to 8 MB, at most 6 per item. The type is decided from the file's content, not its name. Each photo is re-encoded, which removes location and device data, and a thumbnail is made.
- Photo files are private. They are served only through signed links that expire after 10 minutes; the API hands those links to the owner alone. Deleting a photo, an item or the account deletes the files.
- Items carry `cover_thumb_url`, a signed link to their first photo's thumbnail.

## Not implemented yet

Google and phone sign-in, password reset, refresh tokens, automatic clothing extraction from photos, object storage (photos are on local disk for now), outfit suggestions, market, heritage, admin, database migrations (tables are created on startup for now).

## Security notes

- Passwords are hashed with scrypt; tokens are signed JWTs. `DOLAB_JWT_SECRET` must be set in production (the app refuses to start without it).
- Every item, donation and profile query is scoped to the signed-in user. Another user's item id answers 404.
- Body measurements live in a separate `profiles` table and are returned only to their owner.
- Five wrong passwords for one email lock sign-in for 15 minutes. This limiter is in-memory and protects one process only; it must move to a shared store before running several workers.
- No real secrets or user data belong in this repository.
