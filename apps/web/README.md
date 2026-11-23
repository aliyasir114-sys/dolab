# DOLAB web (step F, part 1)

Next.js 16 (App Router), Arabic (RTL) first and English (LTR), built on the approved design system
(`app/theme.css` is an unchanged copy of `design-proposals/gemini/styles/theme.css`, PR #25).

```bash
cp .env.example .env.local      # point NEXT_PUBLIC_API_URL at the API (default http://localhost:8000/v1)
npm ci                          # exact versions from package-lock.json
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
  GHSA-f3mh-rxg3-f5g2), accept same-origin requests only, and do not pass browser-supplied
  `X-Forwarded-For` values to the API unless `DOLAB_WEB_BEHIND_PROXY=1` (see step H below).
- **Step C contracts are swappable.** Photos go through the `PhotoStore` interface
  (`lib/api/photos.ts`). Export and account deletion will use the same pattern when their screens
  come.
- **Not in this part:** sync between devices, offline use, version conflicts, the generated client
  (after D and E); no production data before H.
- **Security headers** in `next.config.ts`: CSP (inline scripts still allowed; a nonce-based policy
  is a follow-up), no framing, `nosniff`, same-origin referrer. No Server Actions, Cache Components
  or image optimisation are used.

## For step H (deployment)

- **Visitor address for the API's sign-in limit (step C-1, reviewer's pre-H task (a)).** Without
  it, every visitor reaches the API from this server's address, so 5 wrong passwords for an email
  through the site would block that email for everyone using the site. To pass the real address:
  1. Run the site behind a reverse proxy that **appends** the address it received from to
     `X-Forwarded-For` (nginx `$proxy_add_x_forwarded_for`, most load balancers do by default),
     and set `DOLAB_WEB_BEHIND_PROXY=1` here. Sign-in then passes that chain (last 8 entries) on.
  2. On the API, list this server's address (and any proxy between it and the API) in
     `DOLAB_TRUSTED_PROXIES`. The API reads the chain from the right, past trusted proxies only, so
     whatever the browser wrote on the left is never used.
  Never set `DOLAB_WEB_BEHIND_PROXY=1` without such a proxy: the header would then be the
  browser's own words.
- Set `DOLAB_WEB_ORIGIN` to the public origin, and the API's `DOLAB_CORS_ORIGINS` to the same.
