# V3-S09 Independent Cross-Review — Round 3

> **HISTORICAL REVIEW SPEC:** Round 3 completed with `GREEN FOR PRE-FLIP ONLY`, then
> its findings triggered additional source changes. Do not use this file to review
> the current frozen source; use `docs/V3_S09_CROSS_REVIEW_ROUND4.md` instead.

> **Purpose:** adversarial review of the PRE-FLIP V3-S09 implementation as it existed at Round 3.
> This document is a review instruction, **not evidence that the implementation is correct**.
> The reviewer must distrust implementation comments, roadmap claims, prior AI summaries, and this document's descriptions until verified against actual source and Legacy behavior.

## 1. Workspaces

### Active project — REVIEW THIS

`C:\Users\PC\Desktop\v2 swam\DroneGod_AI`

### Legacy reference — READ ONLY

`C:\Users\PC\Desktop\v2 swam\DroneGod`

### Canonical roadmap

`docs/V3_MASTER_ROADMAP.md`

The roadmap is useful for scope/context only. **Do not use roadmap text as proof.**

---

## 2. Reviewer role

You are the **INDEPENDENT ADVERSARIAL REVIEWER**.

Your job is to try to prove the current implementation unsafe, behaviorally divergent, incomplete, or incorrectly tested.

Do not reward effort. Do not assume that because tests pass the behavior is correct.

For each claimed fix:

1. inspect the real active source;
2. inspect the real Legacy source where parity is claimed;
3. trace the actual execution path, not only helper/model code;
4. inspect the tests and verify that they exercise production logic instead of reproducing the algorithm inside the test;
5. look for stale callbacks, stale results, retry/reconnect/restart races, command ownership overlap, and terminal-state leakage;
6. classify any finding P0/P1/P2;
7. do **not** authorize an authority flip unless every P0/P1 required for that scope is genuinely closed.

Review only. Do not modify Legacy. Prefer not to modify the active project during review; if you must create temporary review artifacts, keep them outside flight source and report them.

---

## 3. Hard safety boundary

The following are forbidden in this review:

- NO new live mission authority token.
- NO authority flip for S09-A/B/C/D/E.
- NO deploy.
- NO real DB migration.
- NO HIL / actual FC / real flight.
- NO V3-H02.
- NO V3-R01.
- NO retirement/removal of the Python executor.
- NO modification of the Legacy project.
- NO `git reset`, `git clean`, broad revert, or deletion of untracked migration/evidence files.

Existing live mission authority must remain limited to the previously approved exact tokens:

- `core-single`
- `core-single-wait`

Everything added for S09-A/B/C/D/E must remain PRE-FLIP / OFF-by-default.

---

## 4. Global invariants to attack

Treat these as mandatory:

- Core authority only for the explicitly migrated scope.
- Python retains all non-migrated scopes.
- Never concurrent navigation authority over the same drone.
- SWARM_LEADER split ownership means Mission owns the leader only; formation manager owns followers only.
- Emergency/operator takeover must preempt stale mission/formation sends.
- KILL is strongest.
- STOP ALL must not be overwritten by stale navigation.
- No broad/global lock may be held over long FC ACK waits.
- `request_id` retry executes the underlying command once.
- Correlation metadata is observability only and must not affect authority/safety/dedup decisions.
- Mission GOTO/HOLD must remain behind `command.Service` / safety / final-write cancellation guards.
- Unsupported plans must remain Python-owned; do not silently widen Core authority.
- Terminal mission/action/wave state must suppress all stale progression.
- No callback/result from an old run/index/group/action may mutate a newer run.

---

# 5. Review targets

## S09-A — Multi-drone GROUPED

### A1. Navigation freshness — inspect very carefully

Review:

- `backend/internal/fleet/drone.go`
- `backend/internal/safety/envelope.go`
- `backend/internal/api/mission.go`
- `backend/internal/api/mission_grouped_dispatch.go`
- `backend/internal/mission/grouped_multi.go`
- associated tests

The current implementation is intended to track three different notions of age:

- generic MAVLink/link age (`lastMsg` / `TelemetryAgeSec`)
- `GLOBAL_POSITION_INT` age (`positionAt` / `PositionAgeSec`)
- `GPS_RAW_INT` fix age (`gpsAt` / `GpsAgeSec`)

Mission geometry should use navigation evidence, **not generic packet age**.

Attack these questions:

1. Can HEARTBEAT, RC, BATTERY, ATTITUDE, ACK, or any unrelated MAVLink message refresh mission position freshness?
2. Is `positionAt` updated only when latitude/longitude is actually replaced?
3. Is `gpsAt` updated only on a real GPS fix sample?
4. Does mission freshness conservatively use the older of position/fix evidence?
5. Can a stale high `GpsFix` value plus fresh position still incorrectly validate navigation?
6. Can `(0,0)`, missing GPS, negative age, NaN/Inf coordinates, or invalid position enter the centroid?
7. Does repeated polling of cached state ever move its observed timestamp forward?
8. Does WP0 seeding use the same validity contract as later observer ticks?
9. Does partial freshness correctly offset only fresh participants and raw-waypoint-fallback stale/unavailable participants?
10. Does arrival checking reject stale position evidence as well as centroid construction?

Pay special attention to `time.Duration(floatSeconds * time.Second)` conversions and whether any sample timestamp can move backwards/forwards incorrectly.

### A2. GROUPED parity

Verify actual Legacy behavior for:

- shared route/shared index
- formation centroid translation
- missing-position raw waypoint fallback
- front-of-travel ordering
- 150 ms stagger
- per-drone altitude
- per-drone offset arrival target
- all-participant barrier
- command rejection = best-effort stall, no blind retry/rollback/terminal fail
- generation/stale callback suppression

Confirm tests call production helpers used by `app.py`, not copied algorithms.

### A3. Rejection observability

Verify that a participant rejection is bound to:

- run ID
- waypoint index
- drone ID
- reason

Check that an old delayed rejection cannot attach to a new run/index.

Confirm operator-visible state and event/audit trail are sufficient to explain an indefinitely stalled barrier.

---

## S09-B — SEPARATE

Review:

- `backend/internal/mission/plan.go`
- `backend/internal/mission/separate_multi.go`
- API tests
- `frontend/tests/test_separate_characterization.py`
- Legacy `_wp_advance_one`, SEPARATE arrival path, and `check_route_conflicts`

Required structural property:

**exact participant ↔ route bijection**

Attack malformed plans:

- `[1,2]` with routes `[D1,D1]`
- `[1,2]` with routes `[D1,D3]`
- missing D2
- duplicate participant IDs
- zero/invalid DroneID cases
- empty route for one participant

Confirm `allSeparateDone()` can never silently skip a missing route.

Also verify:

- independent per-drone indices
- raw waypoint target, no formation offset
- per-drone altitude
- no duplicate claim/retry
- participant takeover/failsafe terminal behavior
- nonparticipant event does not cancel
- reconnect restores every route/index

### Critical future-flip question

Route-conflict preflight currently remains Python-owned.

Determine whether a future Core SEPARATE authority flip could call `StartMission` without first passing the Legacy `check_route_conflicts` preflight. If yes, mark this as a flip blocker even though live authority is currently OFF.

---

## S09-C — SWARM_LEADER

Review aggressively. This is the most architecture-sensitive S09 scope.

Inspect:

- `backend/internal/mission/swarm_leader.go`
- `backend/internal/api/mission.go`
- `backend/internal/api/server.go`
- `backend/internal/api/command_reservation.go`
- `backend/internal/swarm/manager.go`
- `backend/internal/api/mission_swarm_leader_test.go`
- `backend/internal/swarm/split_ownership_test.go`
- Legacy swarm/waypoint source

### C1. Exact ownership

Required:

- Mission GOTO = leader only.
- Formation GOTO = followers only.
- Formation must not command the leader once Mission owns it.
- Mission must never command followers.
- leader identity is non-zero and frozen for the run.
- no automatic “lowest participant” fallback.

Trace actual final write paths, not only model intents.

### C2. Formation ready/binding lifecycle

Verify:

- Mission cannot claim leader during form-up if form-up can command the leader.
- `FollowerAuthorityReady` means follower-only steady state is genuinely established.
- `ClaimMissionLeader` freezes failover safely.
- leader loss while bound cannot promote the old leader into follower ownership.
- Start failure releases a temporary claim.
- Cancel/failsafe/operator takeover clears/stops the appropriate ownership.
- **Natural mission completion releases the Mission leader binding without unnecessarily stopping Legacy formation.**
- a stale completion from an old run cannot release a newer run's leader binding.

### C3. Legacy GOTO rejection parity

Inspect Legacy `_goto_one` behavior.

Current intended behavior is:

- rejected leader GOTO = best-effort stall
- no blind retry
- no rollback
- no terminal mission failure
- rejection remains observable

Verify the generic single-drone dispatch path does not accidentally call `mission.Fail()` for SWARM_LEADER.

### C4. Failsafe parity

Verify separately:

- leader battery failsafe
- leader link failsafe
- follower battery failsafe
- follower link failsafe

Expected Legacy/Core formation behavior to verify from source:

- leader failsafe => leader route terminal/interrupted + formation halt fail-closed
- follower failsafe => follower excluded from formation navigation; leader route continues

Do not accept this expectation without checking Legacy/current formation source.

### C5. IMPORTANT unresolved design question — follower operator takeover

Current architecture may stop formation for an operator command on a follower while leaving the leader mission active.

Do **not** assume this is correct.

Determine from Legacy behavior and safety invariants what should happen when operator HOLD/LAND/RTL/DISARM/STOP ALL/KILL targets a follower during an active SWARM_LEADER route:

- should only follower/formation authority stop while the leader mission continues?
- or should the entire SWARM_LEADER mission also terminate because the mission's formation contract is no longer valid?

If Legacy is ambiguous, mark this as an explicit product/safety decision blocker instead of inventing behavior.

### C6. IMPORTANT emergency latency question

Trace `runTakeoverBatch` → `stopSwarmForTargetsLocked` → `swarm.Stop()`.

`swarm.Stop()` may wait for the formation loop to exit (bounded timeout).

Determine whether this can delay KILL / STOP ALL / other emergency takeover unacceptably while `missionDispatchMu` is held.

Specifically verify:

- KILL cannot be serialized behind a long FC ACK.
- a stale follower GOTO cannot begin after takeover is accepted.
- if an old transport write is already in progress, define the exact ordering guarantee.
- no lock ordering can deadlock `missionDispatchMu` with `swarm.mu` / return locks.

If the current “wait for formation loop before emergency send” is necessary to prevent stale overwrite but adds meaningful emergency delay, surface it explicitly as a P0/P1 architecture tradeoff.

---

## S09-D — WAVE

Review:

- `backend/internal/mission/wave.go`
- `backend/internal/mission/wave_test.go`
- actual Legacy WAVE source

Verify:

- monotonic token never resets across runs
- token changes across same-phase occurrences in different groups
- old run callback cannot match a new run
- old group TAKEOFF cannot match new group TAKEOFF
- stale landed/next-group callback cannot advance
- cancel/interrupt/timeout are terminal
- timeout reset points match Legacy (TAKEOFF / ROUTE / RETURN+LAND timing)
- `auto_next` is captured at start
- minimum groups/order match Legacy

Remember: WAVE execution wiring is intentionally not Core-live yet. A good shadow state machine is **not** permission to flip authority.

---

## S09-E — Payload / Servo A/B

Review:

- `backend/internal/mission/payload_action.go`
- `backend/internal/mission/payload_action_test.go`
- Legacy `_wp_run_action`

Verify:

- monotonic action token across multiple actions
- duplicate Start cannot overwrite an active action
- stale HOLD/SET/RELEASE callback cannot mutate a newer action
- cancel/failsafe immediately blocks NEW SET
- cancellation never advances route
- cleanup RELEASE remains possible exactly once for channels/drones that may have been SET
- cleanup failure is observable

### IMPORTANT intentional divergence requiring independent decision

Legacy `finish()` launches RELEASE for **every participant**, including some failure cases.

The PRE-FLIP Core model currently narrows cleanup to drones whose SET may have been attempted.

Do not silently accept this.

Decide whether this is:

- a safer intentional contract change that should be explicitly approved/documented, or
- a Legacy parity bug that must be reverted to release-all semantics.

Consider whether release-all could reset a servo override that was not created by this action, versus whether release-only-attempted could leave an override from an uncertain/partially acknowledged SET.

Until resolved, do not mark S09-E authority-ready.

---

## S09-F — rtl_after

Expected status: **BLOCKED / undefined**.

Verify there is still no characterized live waypoint behavior that justifies inventing Core semantics.

Every Core authority predicate must continue rejecting `rtl_after=true`.

---

# 6. Frontend reconnect / no-dual-authority review

Inspect:

- `frontend/swarmgod_gui/core/mission_shadow.py`
- `frontend/swarmgod_gui/app.py::_apply_mission_core_state`
- reconnect tests

Verify recovery is presentation-only:

- GROUPED rebuild correct
- SEPARATE rebuilds every per-drone route + index
- SWARM_LEADER rebuilds leader identity + ownership presentation
- terminal snapshot clears local authority state
- recovery emits zero GOTO/HOLD/SERVO/SWARM START navigation commands
- browser `target_reached` cannot drive Python progression while Core owns mission
- stale Qt timers/callbacks cannot emit Python GOTO after authority handoff

---

# 7. Live authority gate proof

Inspect actual production token parsing in:

- `backend/internal/api/server.go`
- `frontend/swarmgod_gui/core/mission_shadow.py`
- authority gate tests

Do a repository-wide search for candidate tokens.

Required result for this review:

Only these exact tokens may enable Core authority:

- `core-single`
- `core-single-wait`

Explicitly verify that strings such as these do NOT enable anything:

- `core-grouped-multi`
- `core-separate`
- `core-swarm-leader`
- `core-wave`
- `core-payload`
- generic truthy strings

Also verify production profile remains locked and HIL keeps its physical-bench confirmation gate.

---

# 8. Required test execution

Run from `backend`:

```text
go test ./internal/mission ./internal/api ./internal/command ./internal/fleet ./internal/swarm -count=1
go test ./... -count=1
go vet ./...
```

Run relevant frontend tests, including at least:

```text
python -m pytest -q \
  tests/test_grouped_multi_characterization.py \
  tests/test_separate_characterization.py \
  tests/test_mission_shadow.py \
  tests/test_wave.py \
  tests/test_servo_and_ui.py \
  tests/test_command_gateway.py \
  tests/test_command_correlation.py \
  tests/test_command_wire_correlation.py
```

Then:

```text
git diff --check
```

Do not use cached Go results as sole evidence; keep `-count=1`.

If a test is weak, passing it is not evidence. Explain why it is weak and propose the adversarial replacement.

---

# 9. Changes made after the previous Codex quota stopped

These are **claims to verify, not facts to trust**:

1. Roadmap status changed back to `IN PROGRESS / INDEPENDENT REVIEW BLOCKED`.
2. Added navigation-specific freshness timestamps (`positionAt`, `gpsAt`) so generic MAVLink/HEARTBEAT freshness cannot refresh old mission geometry.
3. Mission sample validity now uses position/GPS sample ages rather than generic `TelemetryAgeSec`.
4. Added fleet/API tests around navigation-specific freshness and stale raw-waypoint fallback.
5. SWARM_LEADER rejected GOTO changed to Legacy best-effort stall rather than generic terminal `Fail`.
6. SWARM_LEADER natural route completion releases the temporary Mission leader binding without automatically stopping formation.
7. Added explicit no-live-token gate cases for SEPARATE/SWARM/WAVE/Payload candidate token strings.
8. Roadmap wording corrected where it previously contradicted the real S09 state.

Again: verify every item in source.

### Local verification before handoff — re-run independently

These commands were reported green on the current workspace after the repair pass,
but they are **implementer-side evidence only** and must be re-run by the reviewer:

- `go test ./internal/mission ./internal/api ./internal/fleet ./internal/swarm -count=1` — PASS
- `go test ./... -count=1` — PASS
- `go vet ./...` — PASS
- targeted frontend S09/authority/gateway regression listed in section 8 — **211 passed**
- `git diff --check` — exit 0; only pre-existing CRLF→LF warnings for
  `frontend/swarmgod_gui/widgets/preflight_dialog.py` and `frontend/tests/test_preflight.py`

No new live S09 authority token was intentionally added.

---

# 10. Required reviewer output

Return exactly these sections:

## A. VERDICT

One of:

- `GREEN FOR PRE-FLIP ONLY` — means implementation may proceed to a separately reviewed flip round, **not** that authority may be enabled immediately.
- `BLOCKED`

Never return “authority flip approved” in this review.

## B. FINDINGS

For every finding:

- Severity: P0 / P1 / P2
- Scope: A / B / C / D / E / global
- Exact file + function
- Actual behavior
- Expected Legacy/safety behavior
- Why existing tests do or do not catch it
- Minimal repair direction

## C. PARITY TABLE

For S09-A through E:

- Legacy behavior found
- Active implementation behavior
- Match / intentional divergence / unknown
- Evidence file/function/test

## D. OWNERSHIP TABLE

For each mode/state, state which component may write navigation to each drone:

- single GROUPED
- multi GROUPED
- SEPARATE
- SWARM_LEADER leader
- SWARM_LEADER follower
- WAVE current group
- operator takeover
- failsafe

Any cell with two simultaneous writers = blocker.

## E. STALE/RESTART MATRIX

Check:

- old run result → new run
- old waypoint result → new waypoint
- old WAVE group → new group same phase
- old WAVE run → new WAVE run
- old payload action → new action
- UI restart while Core alive
- Core restart
- operator takeover during pending send
- failsafe during pending send

## F. TEST RESULTS

List exact commands and pass/fail counts.

## G. LIVE-GATE PROOF

Show exact source evidence that no S09-A/B/C/D/E live token exists.

## H. CORRECTION PROMPT

If BLOCKED, finish with one copy-paste implementer prompt containing only the remaining blockers. Do not ask the implementer to enable authority.

---

# 11. Reviewer mindset

Assume the previous implementers and reviewers can all be wrong in the same direction.

A passing test may merely encode the same wrong assumption as the implementation.

Prefer contradictions found by:

- tracing final transport writes;
- comparing Legacy source directly;
- constructing same-phase stale callbacks;
- constructing old-run/new-run identity reuse;
- partial participant failures;
- takeover during the last possible write window;
- fresh heartbeat + stale position;
- follower/leader role change;
- reconnect with partially completed per-drone state;
- cancel/failsafe during servo dwell/release;
- emergency command while formation is stopping.

If a behavior is not characterizable from Legacy and not an obvious safety invariant, mark it **UNKNOWN / DECISION REQUIRED** instead of inventing a rule.
