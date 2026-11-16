# قواعد العمل المشترك / Working agreement

This repository is edited by more than one AI assistant (Claude, ChatGPT) and by human developers. Follow these rules so work does not collide.
يعمل على هذا المستودع أكثر من مساعد ذكي (Claude و ChatGPT) ومطورون. هذه القواعد تمنع التعارض.

## 1. Source of truth / المرجع

- Product decisions live in `README.md` (section "القرارات المثبتة") and `docs/PRD.md`. Do not contradict them; propose a change instead (rule 4).
- The app is named **دولاب / DOLAB**. Never use "دولاب زول", "Dolab Zol" or "Zol".
- Colours are black `#080808` and white `#F5F5F5` with greys only. No accent colours.
- Arabic (RTL) and English (LTR) are both first-class.

## 2. Ownership / من يملك ماذا

| Path | Owner | Others may |
|---|---|---|
| `design/` | Claude (exported from the design canvas) | read only — edits here are overwritten on the next export |
| `docs/PRD.md` | Claude | propose changes via `docs/proposals/` |
| `README.md`, `AGENTS.md` | shared | small additive edits |
| `docs/proposals/` | anyone | add new files freely |
| `web/`, `android/`, `backend/` | unassigned — claim in `docs/WORKLOG.md` before starting | — |

## 3. Before you write / قبل أي تعديل

1. Pull the latest `main`.
2. Read `docs/WORKLOG.md`. If someone has claimed the area, do not work on it.
3. Add a line to `docs/WORKLOG.md` saying what you are about to do.
4. Never force-push. Never rewrite or delete another contributor's files.

## 4. Disagreements / عند الاختلاف

Do not overwrite. Add a file `docs/proposals/<short-name>.md` with the proposed change and the reason. The product owner (Ali) decides.

## 5. Commits

One topic per commit, with a message that says what changed. Add an entry to `docs/WORKLOG.md` when you finish.
