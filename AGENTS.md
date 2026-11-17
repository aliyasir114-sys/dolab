# قواعد العمل المشترك / Working agreement

DOLAB is built by a team of AI assistants and its product owner. Everyone may contribute code within their specialty; these rules keep the work from colliding and keep the final decision with the owner.
يبني دولاب فريق من المساعدين الأذكياء مع مالك المشروع. يحق للجميع المساهمة في الكود وفق تخصصاتهم، وهذه القواعد تمنع التعارض وتُبقي القرار النهائي للمالك.

## 1. Source of truth / المرجع

- Product decisions live in `README.md` (section "القرارات المثبتة"), `docs/PRD.md` and the implementation plan `docs/proposals/phase-1-implementation-plan.md` once the owner approves it. Do not contradict them; propose a change instead (section 6).
- The app is named **دولاب / DOLAB**. Never use "دولاب زول", "Dolab Zol" or "Zol".
- Colours are black `#080808` and white `#F5F5F5` with greys only. No accent colours.
- Arabic (RTL) and English (LTR) are both first-class.

## 2. Roles / الأدوار

Set by the product owner on 2026-11-17.

| Member | Role | Specialty and primary area |
|---|---|---|
| **Project owner (Ali)** | **Final decision** on scope, priorities, approvals and any disagreement | — |
| **Claude Code** | Lead implementation engineer: programming and development, integration between the parts | `services/`, `apps/`, `packages/`, CI, technical docs |
| **Gemini** | Creative director: design and user experience; may also write interface code | `design/`; interface code in `apps/` together with Claude |
| **GitHub Copilot** | Contributing engineer and quality reviewer | Code within an assigned task; reviews |
| **ChatGPT** | Product manager and technical reviewer: guidance, recommendations, reviews | `docs/proposals/`, reviews; code only within an assigned task |

الأدوار (قرار المالك 2026-11-17): **مالك المشروع** صاحب القرار النهائي. **Claude Code** مهندس التنفيذ الرئيسي. **Gemini** المدير الإبداعي للتصميم وتجربة المستخدم، ويشارك في كود الواجهات. **GitHub Copilot** مهندس مساهم ومراجع للجودة. **ChatGPT** مدير المنتج والمراجع التقني، يوجّه ويوصي ويراجع.

The primary area says who is responsible for a folder, not who may touch it. **Everyone may write and change code within their specialty, once the task is defined and permitted (section 3).**

| Path | Contents | Responsible |
|---|---|---|
| `design/` | Screen designs. The current files are `.dc.html` exports from the first design canvas; they need that canvas's `support.js` to render, so new designs may use any reviewable format (images, specs, HTML) | Gemini |
| `docs/` | PRD, plan, work log | Claude (product decisions from the owner) |
| `docs/proposals/` | Reviews and proposals | anyone |
| `services/api/` | Backend (FastAPI) | Claude |
| `apps/web/`, `apps/mobile/` | Next.js site and Flutter app (not started) | Claude, with Gemini on interface code |

**Design handoff:** interfaces are built from what is in `design/` on `main`. If a design is unclear or cannot be built as drawn, ask Gemini first (a comment on Gemini's pull request, or on the task issue) before changing `design/`.

## 3. A defined, permitted task before writing / مهمة محددة ومأذونة قبل الكتابة

Nobody writes to the repository without a task that is **defined** and **permitted by the owner**.

**Defined:** a GitHub issue from the "إخطار قبل الكتابة" template (label `notice`) saying who, what, which paths, and which branch. Issues are used instead of a shared file so two tasks never conflict in a file.

**Permitted** means one of:
1. The owner approves on the issue (a comment, or assigning it).
2. The owner approved it elsewhere (for example in a chat). Whoever opens the issue quotes that approval with its date. If the owner did not write it, the issue counts as unapproved until the owner confirms.
3. The task is a step of a plan the owner has formally approved, and the issue names the step. This is how Claude's planned work is permitted, keeping the freedom the owner gave Claude on 2026-11-17 without asking again for each pull request.

Before starting, check the open `notice` issues and open pull requests. If another task covers the same paths, comment there and agree on an order first. If you cannot agree, the owner decides.

**بالعربية:** قبل أي كتابة يلزم أمران: Issue من قالب «إخطار قبل الكتابة» يحدد المهمة والملفات والفرع، وموافقة المالك (تعليقه على الـ Issue، أو موافقته في محادثة يُنقل نصها وتاريخها، أو كون المهمة خطوة من خطة اعتمدها). راجع المهام المفتوحة أولاً، ولا تعمل على ملفات مهمة أخرى قبل الاتفاق.

## 4. Branches and pull requests / الفروع وطلبات الدمج

1. Pull the latest `main`.
2. Work on your own branch, named `<member>/<short-task>` (for example `gemini/home-screen`, `copilot/item-tests`).
3. **Never change `main` directly. Never force-push.** Never rewrite or delete someone else's files without saying so in the task issue.
4. Open a pull request that links the task issue. The API tests must pass before merging.
5. If the change touches another member's primary area, tag them in the pull request.
6. **Merging:** the author merges their own pull request once the checks pass. Claude merges without waiting for review (owner decision, 2026-11-16); ChatGPT reviews afterwards, and fixes come in a follow-up pull request. A pull request the owner asks to keep open for review is not merged until the owner says so.
7. One topic per commit, with a message that says what changed.

## 5. Reviewing together / المراجعة المشتركة

- Any member may review any pull request. Copilot and ChatGPT review as part of their role; Gemini reviews anything that changes how the product looks or behaves for users.
- Review comments are answered before merging when they arrive in time. Comments that arrive after a merge are handled in a follow-up pull request.
- Conflicts between branches are resolved by the author of the later pull request, together with the author of the earlier one. Nobody resolves a conflict by discarding someone else's work.

## 6. Decisions and disagreements / القرارات والاختلاف

- Approved decisions and agreements are respected. Changing one needs the owner.
- To propose a change, or when members disagree, do not overwrite: add `docs/proposals/<short-name>.md` with the proposed change and the reason, and tag the owner. The owner decides.

## 7. Members without direct GitHub access / الأعضاء دون وصول مباشر إلى GitHub

Do not assume every tool can reach GitHub. A tool's access depends on how the owner has connected it, and it can change.

- A member who cannot open issues or pull requests delivers its work to the owner (text, a patch, files). The owner, or a member the owner names, opens the task issue and the pull request on its behalf, says in the description who wrote the work, and adds a `Co-authored-by:` line when the tool has a known identity.
- Reviews from a member without access reach the pull request the same way: posted by the owner or a named member, quoting the reviewer.
- Work delivered this way follows the same rules: a defined, permitted task; its own branch; a pull request; no direct changes to `main`.
- The table below records access. It is updated only after access has been checked.

| Member | Direct GitHub access | Checked on |
|---|---|---|
| Claude Code | Yes: reads, pushes branches, opens and merges pull requests | 2026-11-16 |
| Gemini | Not checked | — |
| GitHub Copilot | Not checked | — |
| ChatGPT | Not checked | — |

## 8. Work log / سجل العمل

Important changes, decisions and finished tasks are recorded in `docs/WORKLOG.md`, one line each, when the work is finished.
