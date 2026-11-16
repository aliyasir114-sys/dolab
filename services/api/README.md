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

| Area | Endpoints | PRD |
|---|---|---|
| Account | `POST /auth/register`, `POST /auth/login`, `GET /me`, `DELETE /me` | A1, A6 |
| Profile | `GET /me/profile`, `PUT /me/profile` (all fields optional) | P1 |
| Wardrobe | `GET/POST /items`, `GET/PATCH/DELETE /items/{id}`, `POST /items/{id}/wear` | W7–W9, S4 |
| Donations | `POST /donations`, `GET /donations` | D1–D3, D5 |
| Dashboard | `GET /me/dashboard` | F1–F3 |

`GET /items` filters with `?status=`, `?category=`, `?color=`. Cost per wear (F4) is returned on every item as `cost_per_wear`.

## Not implemented yet

Google and phone sign-in, password reset, data export, photo upload and clothing extraction, outfit suggestions, market, heritage, admin, database migrations (tables are created on startup for now).

## Security notes

- Passwords are hashed with scrypt; tokens are signed JWTs. `DOLAB_JWT_SECRET` must be set in production (the app refuses to start without it).
- Every item, donation and profile query is scoped to the signed-in user. Another user's item id answers 404.
- Body measurements live in a separate `profiles` table and are returned only to their owner.
- No real secrets or user data belong in this repository.
