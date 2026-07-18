# Improvements TODO - 2.0.0 (post-code-fix actions)

This file tracks actions that **cannot** be shipped as dataset-safe code changes on the
`optimizations/2.0.0` branch and must be done separately. It is the companion to the code fixes
implemented from `.claude/documents/improvements/*.md`.

**None of the code changes on this branch require a DB reset.** Everything below is deferred work:
data remediation (mutating existing rows), Metabase/dashboard changes, environment config changes, and
cross-team coordination. Each item says who owns it and what must happen.

Legend: `[ ]` pending, `[x]` done, `(DATA)` mutates existing rows, `(CONFIG)` env/PROD config,
`(DASHBOARD)` Metabase, `(COORD)` needs another team.

---

## Issue 1 and 2 - session_type mapping

**Code fix: DONE on optimizations/2.0.0.** Added `chatbot/utils/session_type_utils.resolve_session_type`
and routed `async_consumer`, `async_chaupal_consumer`, and `free_flow_consumer` through it, so the write
path can no longer store an arbitrary/invalid client `flow_name` as `session_type`. Remaining below.

- [ ] (COORD) Agree the single canonical `session_type` value for the chaupal / guest-discussion flow
  with the Metabase / Programs owners. The code fix derives/validates `session_type`, but the exact
  string it should write (`shikshalokam_chaupal` vs `guest_discussion`) must match what the dashboards
  expect. Decide once, align code and dashboards.
- [ ] (DATA) One-time script to normalise the historical `ChatSession.session_type` column from the old
  value to the agreed canonical value. Out of scope for code branch (mutates existing rows). Run only
  after the COORD decision above.
- [ ] (DASHBOARD) Confirm the Metabase queries filter on the agreed canonical value (the previous team
  already patched to include `guest_discussion`).

## Issue 3 - English translation missing

**Code fix: DONE on optimizations/2.0.0.** Fixed the broken `is_english_text` detector (Defect B) and
stopped the silent failure-passthrough (Defect A): translation failures now log at ERROR, are counted, and
set `other_params['translation_failed'] = True` on the story so it is queryable and reprocessable instead
of being silently marked COMPLETED with vernacular text in English fields. Remaining below.

- [ ] (DATA) Re-translate the ~100 reports whose English fields silently hold vernacular text. Safe to run
  now that the code fix stops storing failure as success (reprocessing can no longer recorrupt).
  Selector for NEW failures: `Story.objects.filter(other_params__translation_failed=True)`. Older
  pre-fix corrupted rows have no marker and need a separate heuristic sweep (detect non-Latin script in
  English fields using the corrected detection logic).

## Issue 4 - translation cron / language coverage

- [ ] (DATA) Backfill translations for historically skipped Telugu / Odia (and any residual Kannada)
  reports. The cron will pick these up on its normal forward runs once the detector fix ships; a
  deliberate backfill of old rows is the data task.

## Issue 5 - shikshalokam_chaupal still live

- [ ] (COORD) Confirm with Product whether the `shikshalokam_chaupal` flow is truly retired. The code fix
  (retire vs gate the route) depends on this decision and must be sequenced with a frontend release if
  any legitimate client still uses the old endpoint.
- [ ] (DATA) Reclassify the 3 existing `shikshalokam_chaupal` rows if the flow is confirmed retired.

## Issue 6 - dashboard count / created_at

- [ ] (DASHBOARD) Point the guest-discussion and MI-Story date filters at `chat_sessions.created_at`
  (joined to the story), per the previous team's plan. Document the intended metric explicitly.
- [ ] (DATA) Optionally backfill `Story.client_created_at` on historical stories from their session
  origin timestamp (the code fix only sets it for NEW stories).

## Issue 7 - sessions vs story discrepancy

- [ ] (DATA) Back-classify the existing 103 + 52 sessions-without-story rows with a reason. Becomes
  largely unnecessary once the code fix records `no_story_reason` on new/processed sessions.

## Issue 8 - org translate vs transliterate

- [ ] (CONFIG) Keep / verify the PROD revert of the guest-discussion bot's identity steps to
  transliteration (already done by the previous team). This is environment config, not code.
- [ ] (DATA) Re-transliterate already-translated org/person names in existing stories/profiles. Depends
  on Issue 3 fixes to be reliable.

## Issue 9 - Ramnagar cross-state mapping

- [ ] (DATA) Move the existing 4 mis-mapped rows to the universal unmapped table (the previous team's
  immediate action). The code fix prevents new occurrences; existing wrong values need an explicit
  re-run / reset which is the data task.
- [ ] (REVIEW) Confirm whether `stakeholder_fgd` / `student_fgd` `district_classification.py` share the
  same ambiguity blind spot and need the equivalent fix.
