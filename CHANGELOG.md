# Changelog

## Unreleased: several departments

- Each department has its own live timetable. Publishing, rolling back and
  taking down affect only that department.
- A department generated after others are live keeps the teachers and rooms
  it shares with them clear of the periods they already have there (H19).
  Departments are matched by clock time, so different bell timings still
  line up. A shared teacher's daily cap and longest run count every
  department.
- Publishing and making a version live are refused if they would clash with
  another department's live timetable. A class ID used by two departments is
  refused too.
- When no timetable fits around the other departments, the explanation names
  the teacher or room and the department.
- A teacher's portal shows one week across every department they teach in. A
  student sees their own department's timetable.
- The Build start page has a Department field. The AMCS sample is tagged
  `AMCS`, and a made-up `second_dept_demo` sample shares three teachers and a
  lab with it.
- Databases from 1.0 are upgraded in place. The old live timetable stays live,
  with no department.

## 1.0.0 — 2026-11-06

First release meant to be run for real.

**Sign-in and roles**
- One sign-in page with Student, Teacher and Admin tabs. A first-run screen
  creates the administrator.
- Temporary passwords that must be changed at first sign-in. Passwords are
  stored as scrypt hashes. Sessions use HttpOnly SameSite cookies stored as
  digests.
- Failed sign-ins are slowed per username and per address. Cross-site POSTs
  are refused, and security headers are sent on every response.

**Publishing**
- Publish a generated timetable as a numbered version. Make an older version
  live again, take the timetable down, or delete old versions.
- Every version keeps its CSV, its print-ready HTML and its setup, so
  reopening it in Build re-plans from it.

**Portal for teachers and students**
- Today's periods first (Now / Next), subject and faculty cards, the full week
  with labs merged into one block, and a one-page print layout.
- Administrators can preview any class or teacher.

**People**
- Add one login, add every teacher from the live timetable in one click, or
  import students from CSV.
- Reset a password, disable or delete a login, see who hasn't signed in yet.
  The last administrator cannot be removed.

**Shipping**
- `tt serve` for servers. `tt user add | passwd | list` for account recovery
  from the shell.
- Dockerfile and compose file, settings from the environment, and a
  `/healthz` endpoint.
- Deployment guide and user guide in `docs/`.

**Fixes**
- `tt web` no longer reports a port as busy just after a restart.

The solver and its rules (H1–H18, R1–R2) are unchanged from the
core-correctness work this branch starts from.
