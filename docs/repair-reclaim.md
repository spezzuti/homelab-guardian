# Destructive repairs — disk reclaim, with previews & preconditions (design)

**Status:** DESIGN — for review and sign-off **before** any code. Builds on
[repair.md](repair.md) (the propose → approve → execute → verify framework). Nothing
here is implemented yet.

The two shipped playbooks (`restart_systemd_unit`, `restart_container`) are the
*safest possible* repairs: non-destructive, ~idempotent, one-target, and the
prior state was already broken. The first **destructive** repair — reclaiming
disk space — is the highest-value next step (disk-full is the most common silent
homelab outage), but it deletes things. So it isn't really about adding a
playbook; it's about growing the safety layer so destructive actions are
*responsible*. Three new pieces, then the disk family that uses them.

## Why this, and why now

Disk-full silently takes down backups, databases, and containers. It's the
repair with the most real-world value. And building it forces the three safety
mechanisms every future heavy playbook needs — so we get the machinery once and
reuse it for `remount`, `renew_cert`, and beyond. Auto-approve and Telegram
buttons are bolt-ons by comparison; this is the load-bearing work.

## 1. Preview — a concrete, read-only dry run

Today `propose` shows the argv and a one-line blast-radius. For a restart that's
enough. For a delete it is not: a human must approve against **real numbers**.

Add an optional per-playbook `preview(config, pcfg, check, runner)` that runs
**only read-only** commands and returns concrete effects, e.g.:

- `docker_prune` → "would remove 3 dangling images + 2 stopped containers, ≈1.1 GB"
  (from `docker system df` / a `--dry-run`-style query)
- `journal_vacuum` → "journal is 740 MB; vacuum to 200 MB frees ≈540 MB"
- `prune_dir` → "1,203 files older than 14d in /srv/downloads/tmp, ≈4.2 GB"
  (`find … -mtime +N` counting only — never deleting)

Rules: the preview is read-only, bounded by a timeout, and computed at
**propose** time and stored on the proposal, so the same numbers the human saw at
approval are recorded in the audit trail. It is a point-in-time estimate
("≈"), not a guarantee — note that in the UI.

## 2. Preconditions — cross-collector interlocks (the differentiator)

This is the thing no dumb maintenance script has. A playbook declares
**preconditions checked against Guardian's own latest scan** — its other checks.
If a precondition fails, `propose` refuses with a plain reason.

Examples:
- `prune_dir` / any data-touching reclaim → **requires `backup_health` is `ok`
  and recent** (don't free space by deleting things when the safety net is
  stale). Guardian *already knows* backup health; the repair simply asks it.
- a reclaim on a filesystem → requires the **disk check it targets is the one
  that's actually failing** (don't prune a healthy disk).
- (future) `restart_network_service` → warn/refuse if Guardian is reaching this
  host remotely (it could cut its own connection).

Mechanically: a precondition is `(config, latest_checks) -> ok | reason`,
evaluated at propose from the latest snapshot. This is where "the safety-grounded
domain layer" finally *uses* its grounding — the whole collector fleet becomes
the safety context for every repair.

## 3. Risk tiers — destructive can never auto-approve

Each playbook declares `risk: low | moderate | destructive`.

- `low` / `moderate` (restarts) — eligible for the opt-in `auto_approve`.
- `destructive` (anything that deletes) — **can never be auto-approved**,
  regardless of config; `execute` refuses a destructive proposal that was
  auto-approved rather than human-approved. Optionally requires a stronger
  confirmation (a typed token, or a second approver) — open question below.

This formalizes the `auto_approvable` flag the original design reserved, and
makes "I turned on auto-approve" safe by construction: it can only ever fast-path
the low-risk actions.

## The disk-reclaim playbook family

Each is a bounded, whitelisted, single-command action that `applies_to` a failing
`disk_*` check, with a `preview` and the backup precondition where it deletes
data. Not one generic "prune" — a small set of specific, auditable actions:

| Playbook | Action (argv) | Risk | Precondition |
|---|---|---|---|
| `docker_prune` | `docker system prune -f` (never `--volumes` unless explicitly allowed — volumes are data) | destructive | disk failing |
| `journal_vacuum` | `journalctl --vacuum-size=<cap>` | moderate | disk failing |
| `apt_clean` | `apt-get clean` | low | disk failing |
| `prune_dir` | delete files older than N days under ONE allowlisted dir | destructive | disk failing **+ fresh backup** |

`prune_dir` is the sharp one: it deletes user files, so the target comes from an
explicit `allowed_paths` allowlist (`{path, older_than_days, pattern?}`) — never
from agent input, never an arbitrary path — and it carries the backup
precondition. The others touch only caches/logs/images.

A note on targeting: unlike restart (where the unit is *in* the failing check's
evidence), the disk check only says "/ is 95% full." So the **reclaim action is
chosen from the configured allowlist**, not derived from evidence — the human or
agent picks which blessed reclaim to run, and the preview shows what it'd free.

## How it extends the existing framework

The propose → approve → execute → verify spine is unchanged:
- **propose** additionally runs preconditions (refuse on fail) and computes the
  preview, storing it on the proposal.
- **approve / auto-approve** enforces the risk tier (destructive ⇒ human only).
- **execute** runs the same argv path, loop-guarded, then **verify** re-runs the
  disk collector — did free space actually improve? A reclaim that didn't move
  the needle is `failed`, surfaced, not silently "executed".
- The CLI / dashboard / MCP surfaces show the preview alongside the plan.

Multi-step repairs with rollback (stop → clean → start) are **out of scope** here
— every disk-reclaim action is a single command. We add multi-step later, when a
playbook actually needs it.

## Config sketch

```yaml
repair:
  enabled: false
  playbooks:
    docker_prune:
      enabled: false
      risk: destructive      # informational; the registry sets the real tier
      max_attempts_per_hour: 2
    prune_dir:
      enabled: false
      allowed_paths:
        - { path: /srv/downloads/tmp, older_than_days: 14 }
      require_fresh_backup_hours: 24   # the cross-collector precondition
      max_attempts_per_hour: 2
```

## Threat-model deltas (vs repair.md)

- **Wrong-path / data deletion.** → `prune_dir` only ever touches an allowlisted
  path with an age filter; no arbitrary paths; argv, not shell. Docker prune
  never removes volumes unless explicitly opted in.
- **Deleting the last copy.** → the backup precondition refuses data-touching
  reclaim when `backup_health` isn't ok-and-recent.
- **Preview drift.** → the preview is an estimate; the post-action verify reports
  the *actual* reclaimed space, closing the loop honestly.
- **Auto-approve footgun.** → destructive tier can't be auto-approved at all.

## Phased plan

- **a — doc + sign-off.** Done.
- **b — framework + cache/log reclaim. Done.** `preview` (read-only effects,
  carried on the proposal/audit), `preconditions` hook (cross-collector
  interlock, evaluated at propose), and `risk` tiers (destructive can never
  auto-approve — enforced at propose and defended at execute) are wired through
  build_plan/propose/execute and shown on all three surfaces (CLI/dashboard/MCP).
  Playbooks `docker_prune` (destructive), `journal_vacuum` (moderate), `apt_clean`
  (low) ship, each with a preview, all `enabled: false` by default.
- **c — `prune_dir`** (the user-data one) with the mandatory-but-narrow backup
  precondition (configured-but-stale → hard refuse; no backup configured → warn
  unless `require_fresh_backup: true`) and the optional `require_typed_confirmation`
  knob. Dogfood on a throwaway dir. *Not yet built.*

## Operationalizing on Marcus — the deconfliction track (the "rest")

Arming repairs on Marcus is gated on a real prerequisite the user flagged:
**Marcus has already built some of his own homelab skills.** Two actuators on the
same homelab with no coordination is exactly the failure mode Guardian exists to
prevent. Before turning repairs on, we run a "proper maintenance of Marcus" pass —
analogous to retiring the lobby-network watchdog, but broader:

1. **Audit** Marcus's skills (`~/.hermes/skills/…`) for any that *act on* the
   homelab — what can Marcus already DO, outside Guardian's gated/audited path?
2. **Divide labor** explicitly: Guardian owns deterministic detection + gated,
   audited, reversible repair; Marcus *consumes* Guardian, relays, and *drives
   Guardian's repair tools* (propose → relay → execute-after-approval) — he does
   not freelance his own maintenance actions on the domains Guardian covers.
3. **Reconcile / retire** overlapping skills so there is **one actuator path**
   (Guardian's) for those domains — no two things racing to "fix" the same issue.
4. **Marcus hygiene** generally — keep his skills/config healthy and coherent;
   candidate: Guardian could even watch Marcus's own service/skill health as
   collectors, closing the loop ("who watches the watcher").

This is a sequencing decision, not a blocker for *building* the playbooks: we can
scope + build the disk-reclaim machinery now, and run the Marcus deconfliction
pass when we actually arm repairs on the box.

## Security-review hardening (post-3d-c)

An independent security review of the repair surfaces drove these fixes (the core
guarantees — no shell, argv-only, allowlist-before-build, human-only approval, no
MCP approve tool, atomic state machine, destructive≠auto-approve — were confirmed
intact):

- **Execute re-validates (closes a propose→execute TOCTOU).** `execute` now
  rebuilds the plan from current config/state and refuses if the target left the
  allowlist, the check recovered, repairs were disabled, or the resolved argv
  drifted from what was approved — an approved-but-stale destructive proposal
  can no longer fire after the blessing is revoked.
- **Backup interlock checks freshness, not just status.** `require_fresh_backup_hours`
  refuses an ok-but-stale backup (the "deleting the last fresh copy" hazard), and
  destructive preconditions now **fail closed** if current state is unavailable.
- **Loop guard survives a crash.** A `running` row is recorded before the
  side-effecting action, so an interrupted execute still counts against the cap.
- **`older_than_days` is floored at 1** (a 0/negative value can no longer widen a
  delete to "everything").

Still open (lower severity, noted for later): bind reclaim applicability to the
*failing* filesystem (not any disk check); a `guardian doctor` preflight that the
sudoers grant is scoped (not passwordless-ALL); per-form/expiring CSRF tokens.

## Open questions for sign-off

1. **Stronger confirmation for destructive?** Is human approval (via CLI/dashboard,
   already built) enough, or do destructive actions need a typed confirmation
   (e.g. retype the proposal id / a phrase) or a second approver?
2. **`prune_dir` in the first cut, or cache/log reclaim only?** Ship `docker_prune`
   / `journal_vacuum` / `apt_clean` first (no user data), and hold `prune_dir`
   for a later, more-scrutinized pass?
3. **Backup-precondition default** — `require_fresh_backup_hours: 24` a sane
   default, and should it be *mandatory* (not just default) for any data-touching
   reclaim?
4. **Marcus deconfliction timing** — fold the audit into the next work block, or
   keep building playbooks and run it right before arming?
