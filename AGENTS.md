# قواعد العمل المشترك / Working agreement

This repository is edited by more than one AI assistant (Claude, ChatGPT) and by human developers. Follow these rules so work does not collide.
يعمل على هذا المستودع أكثر من مساعد ذكي (Claude و ChatGPT) ومطورون. هذه القواعد تمنع التعارض.

## 1. Source of truth / المرجع

- Product decisions live in `README.md` (section "القرارات المثبتة") and `docs/PRD.md`. Do not contradict them; propose a change instead (rule 4).
- The app is named **دولاب / DOLAB**. Never use "دولاب زول", "Dolab Zol" or "Zol".
- Colours are black `#080808` and white `#F5F5F5` with greys only. No accent colours.
- Arabic (RTL) and English (LTR) are both first-class.

## 2. Roles / الأدوار

Decided by the product owner on 2026-11-17:

| Member | Role | Primary area |
|---|---|---|
| **Ali** | Product owner. **Final decision** on scope, priorities and any disagreement | — |
| **ChatGPT** | Product manager and technical supervisor: vision, architecture, security, quality. Reviews Claude's work after it lands | `docs/proposals/`, reviews |
| **Claude Code** | Lead engineer: builds the system, implements specifications, integrates the parts. Full freedom to work, reviewed afterwards by ChatGPT | `services/`, `apps/`, `packages/`, `docs/`, CI |
| **Gemini** | Design and interfaces: UI design, motion and animation, visual consistency, user experience | `design/` |
| **GitHub Copilot** | Assistant engineer and reviewer: code review, bug finding, tests, performance | reviews |

**Everyone may write anywhere in the repository, but must notify first (section 3).** The primary area says who is responsible for a folder, not who is allowed to touch it.

الأدوار (قرار المالك 2026-11-17): **Ali** صاحب القرار النهائي. **ChatGPT** مدير المنتج والمشرف التقني ويراجع عمل Claude بعد دمجه. **Claude Code** المهندس الرئيسي بحرية كاملة في العمل. **Gemini** للتصميم والواجهات. **GitHub Copilot** مساعد ومراجع للكود. **الجميع لديه صلاحية الكتابة في أي مكان، بشرط الإخطار أولاً.**

| Path | Contents | Owner |
|---|---|---|
| `design/` | Screen designs. The current files are `.dc.html` exports from the first design canvas; they need that canvas's `support.js` to render, so new designs may use any reviewable format (images, specs, HTML) | Gemini |
| `docs/` | PRD, plan, work log | Claude (product decisions from Ali) |
| `docs/proposals/` | Reviews and proposals | anyone |
| `services/api/` | Backend (FastAPI) | Claude |
| `apps/web/`, `apps/mobile/` | Next.js site and Flutter app (not started) | Claude, built from Gemini's designs |

**Design handoff:** Claude builds interfaces from what is in `design/` on `main`. If a design is unclear or cannot be built as drawn, Claude asks Gemini first (a comment on Gemini's pull request, or a notice tagging the design file) before changing `design/` itself.

## 3. Before you write: notify first / قبل الكتابة: الإخطار أولاً

1. Pull the latest `main`.
2. **Check open notices:** the GitHub issues labelled `notice`, and open pull requests. If someone has announced work on the same paths, do not start on them: comment on their notice and agree on an order first. If you cannot agree, Ali decides.
3. **Open a notice:** a GitHub issue from the "إخطار قبل الكتابة" template, with who, what, which paths, and which branch. Notices live in GitHub issues, not in a file, so two notices never conflict with each other.
4. Work on your own branch. **Never push directly to `main` and never force-push.** Never rewrite or delete someone else's files without saying so in your notice.
5. Open a pull request that links the notice. The API tests must pass before merging.
6. **Merging:** the author merges their own pull request once the checks pass. Claude merges without waiting for review (owner decision); ChatGPT reviews afterwards, and fixes come in a follow-up pull request. If a change touches someone else's primary area, tag them in the pull request.
7. Close the notice and add a line to `docs/WORKLOG.md` when finished.

**الإخطار:** قبل أي كتابة، افتح Issue من قالب «إخطار قبل الكتابة» تذكر فيه من وماذا وأي ملفات. راجع الإخطارات المفتوحة أولاً، ولا تعمل على ملفات أعلن غيرك العمل عليها قبل الاتفاق. لا دفع مباشر إلى `main`. صاحب طلب الدمج يدمجه بعد نجاح الاختبارات، وChatGPT يراجع عمل Claude بعد الدمج.

## 4. Disagreements / عند الاختلاف

Do not overwrite. Add a file `docs/proposals/<short-name>.md` with the proposed change and the reason. The product owner (Ali) decides.

## 5. Commits

One topic per commit, with a message that says what changed. Add an entry to `docs/WORKLOG.md` when you finish.
