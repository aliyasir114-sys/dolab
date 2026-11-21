# DOLAB web (step F, part 1)

Next.js 16 (App Router), Arabic (RTL) first and English (LTR), built on the approved design system
(`app/theme.css` is an unchanged copy of `design-proposals/gemini/styles/theme.css`, PR #25).

```bash
cp .env.example .env.local      # point NEXT_PUBLIC_API_URL at the API (default http://localhost:8000/v1)
npm install
npm run dev                     # http://localhost:3000 -> /ar
npm test                        # unit tests (Node's own runner, no extra packages)
npm run build
```

The API must allow this site's origin: `DOLAB_CORS_ORIGINS=http://localhost:3000` on the API.

## What is here

| Screen | Path | API |
|---|---|---|
| Sign in | `/[locale]/sign-in` | session route → `POST /auth/login` |
| Create account | `/[locale]/sign-up` | `POST /auth/register`, then sign in |
| Forgot password | `/[locale]/forgot-password` | `POST /auth/password/forgot` |
| New password | `/[locale]/reset-password` | `POST /auth/password/reset` |
| Wardrobe | `/[locale]/wardrobe` | `GET /items` (status filter, paging, `X-Total-Count`) |
| Add an item | `/[locale]/wardrobe/new` | `POST /items`, then `POST /items/{id}/photos` |

The sign-in screens and the add-item form have no design in `design/` yet: they use the design
system's components only and are waiting for Gemini's review. The wardrobe follows
`design/WebWardrobe` (the "add from photos" button is "add item": the pilot is manual entry).

## Rules this code keeps (Ali, 2026-11-21, and plan 4.1)

- **One API layer.** Pages call `lib/api/*.ts` only; `lib/api/client.ts` is the only `fetch` to the API.
  The hand-written types in `lib/api/types.ts` are replaced by the generated client after step E.
- **Session.** The refresh token is an `HttpOnly; Secure; SameSite=Strict` cookie (`__Host-dolab_rt`)
  that only `app/api/session/[action]` reads; the access token lives in memory. Nothing is written
  to `localStorage`, `sessionStorage`, IndexedDB or a readable cookie (P10). The session routes run
  on the Node.js runtime and set the cookie with `cookies().set()` (Next.js advisory
  GHSA-f3mh-rxg3-f5g2), accept same-origin requests only, and pass `X-Forwarded-For` on to the API
  so its sign-in limits see the visitor (the API's `DOLAB_TRUSTED_PROXY_HOPS` must count the web
  server; to set in step H).
- **Step C contracts are swappable.** Photos go through the `PhotoStore` interface
  (`lib/api/photos.ts`). Export and account deletion will use the same pattern when their screens
  come.
- **Not in this part:** sync between devices, offline use, version conflicts, the generated client
  (after D and E); no production data before H.
- **Security headers** in `next.config.ts`: CSP (inline scripts still allowed; a nonce-based policy
  is a follow-up), no framing, `nosniff`, same-origin referrer. No Server Actions, Cache Components
  or image optimisation are used.

## For step H (deployment)

- The session routes pass `X-Forwarded-For` on as received. Put the site behind a proxy that sets
  it, and set the API's `DOLAB_TRUSTED_PROXY_HOPS` to count that proxy and the web server. If the
  site were reachable directly, a visitor could forge the header and dodge the per-address sign-in
  limit (the per-email limit still applies).
- Set `DOLAB_WEB_ORIGIN` to the public origin, and the API's `DOLAB_CORS_ORIGINS` to the same.
