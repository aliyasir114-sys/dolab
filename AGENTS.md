# قواعد العمل المشترك / Working agreement

This repository is edited by more than one AI assistant (Claude, ChatGPT) and by human developers. Follow these rules so work does not collide.
يعمل على هذا المستودع أكثر من مساعد ذكي (Claude و ChatGPT) ومطورون. هذه القواعد تمنع التعارض.

## 1. Source of truth / المرجع

- Product decisions live in `README.md` (section "القرارات المثبتة") and `docs/PRD.md`. Do not contradict them; propose a change instead (rule 4).
- The app is named **دولاب / DOLAB**. Never use "دولاب زول", "Dolab Zol" or "Zol".
- Colours are black `#080808` and white `#F5F5F5` with greys only. No accent colours.
- Arabic (RTL) and English (LTR) are both first-class.

## 2. Roles / الأدوار

Decided by the product owner on 2026-11-17 (replaces the 2026-11-16 roles):

| Member | Role | Writes to | Does not |
|---|---|---|---|
| **Ali** | Product owner. **Final decision** on scope, priorities and any disagreement | — | — |
| **ChatGPT** | Product manager and technical supervisor: vision, architecture, security, quality review, recommendations | `docs/proposals/`, review comments on pull requests | Commit code or edit existing files; make final decisions |
| **Claude Code** | Lead engineer: builds the system, implements specifications, integrates the parts | `services/`, `apps/`, `packages/`, `docs/` (except `docs/proposals/` written by others), CI | Edit `design/` |
| **Gemini** | Creative and design lead: UI design, motion and animation, visual consistency, user experience | `design/` (owns it; edits files directly) | Edit code or other folders |
| **GitHub Copilot** | Reviewer and assistant engineer: code review, bug finding, test suggestions, performance review | Review comments on pull requests | Commit code or open pull requests |

الأدوار (قرار المالك 2026-11-17): **Ali** صاحب القرار النهائي. **ChatGPT** مدير المنتج والمشرف التقني، يراجع وينصح عبر `docs/proposals/` وتعليقات طلبات الدمج ولا يعدّل الملفات. **Claude Code** المهندس الرئيسي، ينفّذ الكود والتكامل ولا يعدّل `design/`. **Gemini** مسؤول التصميم ويملك `design/` ويعدّله مباشرة. **GitHub Copilot** مراجع فقط عبر تعليقات طلبات الدمج.

| Path | Contents | Owner |
|---|---|---|
| `design/` | Screen designs. The current files are `.dc.html` exports from the first design canvas; they need that canvas's `support.js` to render, so new designs may use any reviewable format (images, specs, HTML) | Gemini |
| `docs/` | PRD, plan, work log | Claude (product decisions from Ali) |
| `docs/proposals/` | Reviews and proposals | anyone |
| `services/api/` | Backend (FastAPI) | Claude |
| `apps/web/`, `apps/mobile/` | Next.js site and Flutter app (not started) | Claude, built from Gemini's designs |

**Design handoff:** Claude builds interfaces from what is in `design/` on `main`. If a design is unclear or cannot be built as drawn, Claude asks in a pull request comment or a file in `docs/proposals/` instead of changing `design/`.

## 3. Before you write / قبل أي تعديل

1. Pull the latest `main`.
2. Read `docs/WORKLOG.md` to see what is done and what is in progress.
3. Code changes go on a branch and a pull request; the API tests must pass before merging. Claude merges its own pull requests once the checks pass (owner decision, 2026-11-16). Review comments from ChatGPT or Copilot are answered before merging when they arrive in time; disagreements go to Ali.
4. Never force-push. Never rewrite or delete another contributor's files.

## 4. Disagreements / عند الاختلاف

Do not overwrite. Add a file `docs/proposals/<short-name>.md` with the proposed change and the reason. The product owner (Ali) decides.

## 5. Commits

One topic per commit, with a message that says what changed. Add an entry to `docs/WORKLOG.md` when you finish.
