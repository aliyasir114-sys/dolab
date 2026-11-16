# قواعد العمل المشترك / Working agreement

This repository is edited by more than one AI assistant (Claude, ChatGPT) and by human developers. Follow these rules so work does not collide.
يعمل على هذا المستودع أكثر من مساعد ذكي (Claude و ChatGPT) ومطورون. هذه القواعد تمنع التعارض.

## 1. Source of truth / المرجع

- Product decisions live in `README.md` (section "القرارات المثبتة") and `docs/PRD.md`. Do not contradict them; propose a change instead (rule 4).
- The app is named **دولاب / DOLAB**. Never use "دولاب زول", "Dolab Zol" or "Zol".
- Colours are black `#080808` and white `#F5F5F5` with greys only. No accent colours.
- Arabic (RTL) and English (LTR) are both first-class.

## 2. Roles / الأدوار

Decided by the product owner on 2026-11-16:

- **Claude implements the whole project**: design, documentation and all code (`design/`, `docs/`, `services/`, `apps/`).
- **ChatGPT is a consultant only**: it reviews and advises. It does not commit code or edit existing files. Its advice goes in `docs/proposals/<short-name>.md` or as review comments on pull requests.
- **Ali (product owner)** decides when advice and implementation disagree.

كلود ينفّذ المشروع كله. ChatGPT مستشار فقط: يراجع وينصح عبر `docs/proposals/` أو تعليقات على طلبات الدمج، ولا يعدّل الملفات. القرار النهائي لمالك المنتج.

| Path | Contents |
|---|---|
| `design/` | Screen sources exported from the design canvas (edits here are overwritten on the next export) |
| `docs/` | PRD, work log, proposals |
| `services/api/` | Backend (FastAPI) |
| `apps/web/`, `apps/mobile/` | Next.js site and Flutter app (not started) |

## 3. Before you write / قبل أي تعديل

1. Pull the latest `main`.
2. Read `docs/WORKLOG.md` to see what is done and what is in progress.
3. Code changes go on a branch and a pull request; the API tests must pass before merging. Claude merges its own pull requests once the checks pass (owner decision, 2026-11-16).
4. Never force-push. Never rewrite or delete another contributor's files.

## 4. Disagreements / عند الاختلاف

Do not overwrite. Add a file `docs/proposals/<short-name>.md` with the proposed change and the reason. The product owner (Ali) decides.

## 5. Commits

One topic per commit, with a message that says what changed. Add an entry to `docs/WORKLOG.md` when you finish.
