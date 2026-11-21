# Handover: Timetable

Where the project stands, how it works, what is missing, and the safe way to
put it live. Written 2026-11-21. Keep it up to date as things change.

---

## 1. What it is, in one minute

A website for the college timetable office.

1. The office enters **rooms, teachers, classes and subjects** (Build page).
2. They press **Generate**. A solver (Google OR-Tools CP-SAT) searches for a
   week where nobody and nothing is in two places at once and every rule holds.
3. A **separate checker** (`verify.py`) re-checks the answer from scratch.
   A timetable that fails it can never be published.
4. They press **Publish**. Teachers and students sign in and see only their
   own week.

It is one Python program with one SQLite database file. It runs directly
(`tt serve`) or in Docker.

---

## 2. Where the code is

| PR | Branch | What it adds | State |
|---|---|---|---|
| #3 | `aniruktha/core-correctness` → `main` | Solver rules, home classrooms, re-planning, explanations | Open |
| #4 | `aniruktha/v1-product` → #3 | Sign-in, roles, publishing, portal, Docker, deploy guide | Open |
| #5 | `aniruktha/multi-department` → #4 | One live timetable per department, no cross-department clashes | Open |
| — | same branch as #5, **pushed: no** | Free-period limit (H20); publish race fix; solve cap; this file | Local commits |

**Merge order: push the local commits, then merge #3 → #4 → #5.** Each PR's
base switches to `main` when the one below it merges. Then run
`uv run pytest` on `main`: expect 92 passed.

---

## 3. Code map

| File | Job |
|---|---|
| `src/ttgen/data.py` | Reads a setup (YAML or the browser's JSON) into an `Instance`; rejects bad data with a clear message |
| `src/ttgen/config.py` | The day's periods, lab blocks, and the weights of the "nice to have" rules |
| `src/ttgen/preflight.py` | Arithmetic checks *before* solving ("this teacher has 42 hours but only 40 periods exist") |
| `src/ttgen/model.py` | The solver model: hard rules H1–H20 and soft rules S1–S13; `explain()` names conflicting rules when nothing fits |
| `src/ttgen/verify.py` | Independent re-check of every hard rule on a finished timetable |
| `src/ttgen/baseline.py` | Re-planning: stay close to a published timetable, keep locked periods |
| `src/ttgen/elsewhere.py` | Other departments' live timetables: which periods shared teachers/rooms are already busy |
| `src/ttgen/report.py` | CSV and printable HTML sheets per class, teacher, room |
| `src/ttgen/web.py` | The web API: solve, inspect, samples, saved setups |
| `src/ttgen/publish.py` | Publishing, versions, going live, the teacher/student portal |
| `src/ttgen/accounts.py` | Sign-in, sessions, roles, the user admin API, login throttling |
| `src/ttgen/store.py` | The SQLite database: users, sessions, publications |
| `src/ttgen/settings.py` | Settings from environment variables (`TTGEN_*`) |
| `src/ttgen/cli.py` | The `tt` command: `check`, `workload`, `solve`, `serve`, `user` |
| `src/ttgen/static/` | The pages: `index.html` (Build), `published.html`, `people.html`, `portal.html`, `login.html` |
| `tests/` | 92 tests: solver rules, the site over real HTTP, departments, free periods, safety |
| `data/` | Example setups. `amcs_sem2.yaml` is transcribed from the real AMCS Sem-2 timetable |
| `docs/USER_GUIDE.md`, `docs/DEPLOY.md` | For the timetable office, and for whoever runs the server |

The rule list with numbers is in `README.md` ("Hard constraints").

---

## 4. What is done

**Timetabling**
- 20 hard rules, each enforced by the solver and re-checked by the checker:
  - no class, teacher or room in two places at once
  - labs inside two-period blocks, on different days
  - elective groups at the same time
  - teacher availability, daily limit and longest run
  - home classrooms; pinned and locked periods
  - other departments' bookings (H19)
  - at most N free periods a week per class (H20)
- Soft rules: free periods pushed to the ends of days, fewer days on campus for
  visiting staff, fewer room changes, labs spread out.
- When no timetable is possible it says why, in words.
- Mid-term change: reopen a published version and change as little as possible.

**The site**
- Three roles: administrator, teacher, student. Temporary passwords must be
  changed at first sign-in. Bulk student import. Reset and disable accounts.
- Every published version is kept; roll back; take down; CSV and print export.
- Several departments, each with its own live timetable. Shared teachers and
  rooms never clash. A teacher who teaches in several departments sees one week.

**Security already in place**
- Passwords stored as salted hashes; session tokens stored hashed.
- Sign-in cookie is HttpOnly and SameSite=Lax; `Secure` once
  `TTGEN_SECURE_COOKIES=1`.
- Requests from other websites are refused (Origin check).
- Repeated wrong passwords are slowed down; the error does not reveal whether a
  username exists.
- Every API call checks the role. Teachers and students cannot see other
  people's weeks or any admin page.
- The last administrator cannot remove themself.
- Publishing a clash is refused, and the clash check plus going live is one
  atomic step (fixed 2026-11-21: two departments published at once could both
  go live).
- At most `TTGEN_MAX_SOLVES` (default 2) generations at once, so the server
  cannot run out of memory and take the portal down.

**Tested**
- 92 automated tests pass.
- On a local server:
  - a 32-step end-to-end test
  - a two-department test
  - a free-period test: AMCS with a 7-period day gives every class 2 or fewer
    free periods in 3 runs out of 3
- Pages checked in a browser.

---

## 5. Not done yet, most important first

### Must fix before real students use it
1. **Docker has never been run.** It needs installing (sudo). Run the steps in
   `docs/DEPLOY.md` once on a test machine.
2. **HTTPS.** Put Caddy, nginx or the college proxy in front, then set
   `TTGEN_SECURE_COOKIES=1`. Without it passwords cross the network in clear.
3. **Backups are not automatic.** Add a nightly job (commands in DEPLOY.md),
   keep copies off the server, and **try one restore**.
4. **Real data.** Only AMCS Semester 2 is entered. Every department and
   semester must be entered. A teacher or room shared between departments needs
   **exactly the same ID** everywhere; class IDs must be unique across the
   college.
5. **The free-period rule vs AMCS.** With 8 periods a day, AMCS classes have
   6–7 free periods. To meet "at most 2", either drop the 16:20 period (tested:
   works) or add ~5 taught periods per class (library, tutorial, mentoring)
   with a teacher for each. This is a decision for the department, not code.

### Should build before the whole college uses it
6. **Department administrators.** Today every admin can change every department.
7. **Student batch and elective links.** Today a student sees their whole
   class, including lab batches and electives they don't attend.
8. **College-wide common slots** (open electives, common hours across
   departments).
9. **Activity log.** Only "who published" is recorded today.
10. **Unpublished results live in memory.** A restart loses a generated but
    unpublished timetable; generate again.

### Can wait
- Email, notifications, "forgot password" by email (admins reset passwords
  today).
- College single sign-on, two-factor sign-in, calendar (.ics) export.
- `tt solve` on the command line ignores other departments; use the website.
- Very big setups: 20 classes took 3 minutes and 1.9 GB and gave weaker
  results. Keep one setup to about 4–8 classes.
- An outside security review: none has been done.

### Rules for running it
- Run **one** server process. The publish lock depends on it.
- Publish the departments that share the most teachers **first**. Each later
  department fits around the ones already live.

---

## 6. The road to live

1. **Code.** Push the local commits, merge #3 → #4 → #5, run `uv run pytest`.
2. **Server.** 4+ cores, 4 GB+ RAM. Install Docker. `cp .env.example .env`,
   set `TTGEN_ORG_NAME`. `docker compose up -d --build`. Create the
   administrator **before** the site is reachable from outside.
3. **Network.** HTTPS, only port 443 open, `TTGEN_SECURE_COOKIES=1`.
4. **Backups.** Nightly backup off the server; one practice restore.
5. **Pilot.** AMCS only.
   - Settle the free-period setup.
   - Generate, have the office compare it with reality, then publish.
   - Give a few teachers and one class logins for a week; collect problems.
6. **Expand.** Next department with matching teacher and room IDs: generate,
   check, publish. Repeat.
7. **Before going college-wide**, build items 6–8 of section 5.

**Verdict.** Safe for a supervised pilot with one or a few departments, once
HTTPS and backups are in place. Not yet for the whole college running
unattended.

---

## 7. Everyday commands

```bash
uv sync                                   # install
uv run pytest                             # all tests (expect 92 passed)
uv run tt --data data/amcs_sem2.yaml check     # find impossibilities
uv run tt --data data/amcs_sem2.yaml solve --time 60
uv run tt serve --port 8000               # the site (see DEPLOY.md for real use)
uv run tt user list | add | passwd        # accounts from the server shell
```
