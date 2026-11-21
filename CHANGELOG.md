# Changelog

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
