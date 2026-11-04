# Plan 2: Separate test and live environments

Status: **parked** (2026-10-24). Nothing built.

**Decided instead:** one database. Before release, **all data is removed** (orders, customers, drivers, products,
everything test). Nothing to do now; the user will ask when release comes. At that point, propose a reset
script (backup first, typed confirmation, order numbers back to #1, Traccar test devices too) and confirm what,
if anything, to keep (e.g. user logins so someone can still sign in; remembered driving times). Separate
test/live environments below can be revisited after release if regular testing is needed.

## The need
- Before release, everything is tested; all data now is test data.
- Test data must never appear in real use (real drivers, real customers, learned stop times, reports).
- After release, new features still need testing, without touching real data.

## Opinion: two separate environments, not a flag
A "test" flag on records in one database is risky: every query must remember to filter it. One missed filter
and a test order is offered to a real driver, a test customer shows up in a real search, or ~18-second test
deliveries distort the learned time at a stop. Instead, run **two complete copies side by side**, each with its
own data, which can't mix.

| | Live (real) | Test |
|---|---|---|
| Setting | `APP_ENV=live` | `APP_ENV=test` |
| Settings file | `.env` | `.env.test` |
| Database | `dispatch` | `dispatch_test` (same PostgreSQL server) |
| Telegram bot | the real bot | a **second bot** from @BotFather (e.g. "… Test Bot"); real drivers never see test offers |
| Web / API ports | 8090 / 8100 | 8091 / 8110 |
| Code | `main` | the branch being tested (own folder, see below) |
| Google | real key, normal limits | estimates by default (no cost); key can be switched on with a small daily limit |
| Looks | normal | orange **TEST** bar on every page, "[TEST]" in the tab title, bot messages start with "🧪 TEST" |

## Details
1. **Two folders, one code base.** Live runs `main` from `telegram-dispatch-system`; test runs any branch from
   `telegram-dispatch-system-test` (a git worktree of the same repository). Both can run at the same time.
2. **Start scripts:** `start.ps1` starts live, `start.ps1 -Env test` starts test; `setup.ps1 -Env test` creates
   the test database and settings.
3. **Safety checks at startup:** live refuses to run against `dispatch_test`; test refuses to run against
   `dispatch` or with the live bot's token. A mix-up stops the program instead of mixing data.
4. **GPS in test:** a **GPS simulator** in the test web app (put a test driver at a point, or move them along a
   route), so testing needs no phones. Optional: real test phones, by having live pass on positions of devices it
   doesn't know to the test environment (Traccar can forward to only one address).
5. **Reset test data:** `reset-test.ps1` empties the test database (keeps users and products) to start a test
   round clean.

## Release
- The current database (all test data) **becomes the test database** (`dispatch_test`).
- Live starts with a **fresh, empty database**.
- Optional "copy to live" for chosen things only: **users, products, customers**; remembered driving times can
  be copied too (they're real road times and save Google costs). **Never** orders, history, deliveries or test
  drivers. Real drivers are added in live and connect to the real bot.

## After release
New features are built on a branch, run in **test** (same database changes applied there first), and only after
testing merged into `main` and started in **live**.

## Questions for the user
1. Two separate environments (recommended) rather than a flag?
2. Names: `live` / `test` (or `real` / `unreal`)?
3. Can you create a second Telegram bot with @BotFather for testing?
4. Test GPS: simulator only, or also real phones via live passing positions on?
5. At release: copy users, products and customers to live, or start completely empty?

## Relation to Plan 1
Plan 1 (route sequencing) continues on its branch; step 4 is agreed (as proposed: on-the-way status swap,
"best gap first" with reshuffle only when it saves ≥ 3 min) but not started. With Plan 2 in place, Plan 1 would be
tested in the test environment with `ROUTE_OPTIMIZATION` on while live keeps it off.
