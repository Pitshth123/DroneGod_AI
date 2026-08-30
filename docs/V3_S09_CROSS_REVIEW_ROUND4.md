# V3-S09 Independent Cross-Review — Round 4

> **Purpose:** adversarial review of the POST-ROUND-3 REPAIR DELTA.
> Round 3 returned `GREEN FOR PRE-FLIP ONLY`, then identified P1 flip-prerequisite findings.
> Those findings caused new source changes, so the previous frozen-source verdict is no longer sufficient.
>
> **Do not trust the implementer summary, roadmap, tests, or prior AI reviewers. Inspect the current source.**

## 1. Workspaces

Active project — REVIEW THIS:

`C:\Users\PC\Desktop\v2 swam\DroneGod_AI`

Legacy reference — READ ONLY:

`C:\Users\PC\Desktop\v2 swam\DroneGod`

Roadmap:

`docs/V3_MASTER_ROADMAP.md`

Historical Round-3 spec:

`docs/V3_S09_CROSS_REVIEW_ROUND3.md`

---

## 2. Hard boundary

- REVIEW ONLY.
- Do not modify Legacy.
- Do not authority-flip S09-A/B/C/D/E.
- Do not add a new live authority token.
- Do not deploy.
- No HIL / real FC / real flight.
- No V3-H02 / V3-R01.
- Do not retire Python executors.
- No `git reset`, `git clean`, broad revert, or deletion of dirty migration/evidence files.

The only live mission authority strings must still be exactly:

- `core-single`
- `core-single-wait`

A Round-4 GREEN verdict means **PRE-FLIP source only**. It does not authorize a flip.

---

# 3. Round-3 findings that were repaired

## R4-A — Emergency / swarm preemption architecture

Round 3 found that operator takeover could call `swarm.Stop()` while holding
`missionDispatchMu`, waiting up to 2 seconds before KILL/STOP ALL could send.
A naive async Stop would be unsafe because form-up and RETURN could still emit a
stale navigation write after the emergency.

Current repair introduces two-phase revocation:

- `swarm.Manager.RevokeFormationNavigation()`
- `swarm.Manager.RevokeReturnNavigation()`
- `navigationSendGuard`
- `fleet.Drone.GotoYawContext()`

Inspect at least:

- `backend/internal/swarm/manager.go`
- `backend/internal/fleet/drone.go`
- `backend/internal/api/server.go`
- `backend/internal/api/command_reservation.go`
- `backend/internal/api/mission.go`
- `backend/internal/swarm/split_ownership_test.go`
- `backend/internal/fleet/send_guard_test.go`
- `backend/internal/swarm/stop_test.go`

### Required properties to prove

1. `RevokeFormationNavigation()` returns after **write authority** is revoked, not after the old loop fully exits.
2. It cannot wait for the old 2-second loop teardown while `missionDispatchMu` is held.
3. An already-running final transport write may finish first, but once revoke returns no new stale formation write can begin.
4. Steady-state follower GOTO remains protected (`sendFollowerIfOwned`).
5. **Form-up leader pinning and follower transitions are also protected.** Trace every `Goto` / `GotoYaw` inside `formUpSequential` and prove they use a cancellable final-write guard.
6. A KILL/STOP ALL arriving during form-up cannot be overwritten afterward by a stale form-up GOTO.
7. `RevokeReturnNavigation()` closes the return sequence final-write gate before/with context cancellation.
8. Every RETURN/LAND navigation send that can occur after the sequence is registered uses the guarded context:
   - stage-altitude GOTO
   - horizontal return GOTO
   - fallback RTL `COMMAND_LONG`
   - LAND `COMMAND_LONG`
9. Cancelling during an FC ACK wait does not wait for the ACK before a stronger emergency may establish authority; the final-write guard should cover only transport write, not ACK wait.
10. A stale RETURN sequence cannot emit another GOTO/RTL/LAND after revoke returns.
11. `Stop()` and `CancelReturn()` still preserve their historical synchronous contract for non-emergency callers.
12. `NavigationBusy()` stays true while old goroutines are still unwinding, so a new mission cannot start into cleanup.
13. `Start()` cannot create a new formation while `stopping` is still true.
14. Audit/lifecycle cleanup cannot race test/process teardown or double-close stop channels.
15. No lock inversion/deadlock was introduced among `missionDispatchMu`, `swarm.mu`, `returnMu`, navigation guards, fleet send locks, and ACK locks.
16. A second concurrent emergency is not serialized behind old formation loop teardown.

### API paths to trace

- KILL
- STOP ALL
- HOLD
- LAND
- RTL
- DISARM
- ChangeAlt / EqualizeAlt takeover paths
- CancelMission during SWARM_LEADER
- leader battery/link failsafe
- Swarm STOP command

Do not merely inspect `Revoke*`; trace from RPC entry to final FC write.

---

## R4-B — SEPARATE collision preflight can no longer be frontend-only

Round 3 found a future flip could bypass Python `check_route_conflicts`.
Current repair adds Core geometry and an API fresh-start gate.

Inspect:

- `backend/internal/mission/separate_conflict.go`
- `backend/internal/mission/separate_multi.go`
- `backend/internal/api/mission.go`
- `backend/internal/api/mission_grouped_dispatch.go` (`missionPositionSamples`)
- `backend/internal/mission/separate_conflict_test.go`
- `backend/internal/api/mission_separate_test.go`
- Legacy/active `frontend/swarmgod_gui/core/waypoint_logic.py::check_route_conflicts`

### Prove exact parity or report divergence

1. Legacy thresholds are exactly:
   - altitude gap `<= 2.0m` means same layer
   - route distance `< 6.0m` means conflict
2. Equirectangular XY conversion matches Legacy sufficiently/exactly for the deterministic tests.
3. Segment intersection returns distance 0.
4. Single-point routes match Legacy behavior.
5. Empty/malformed routes fail before authority.
6. Participant ↔ route bijection is still exact.
7. Per-drone altitude source matches the frozen altitude the executor would actually use.
8. Static planned-route conflicts are rejected by the Core authority predicate.
9. `StartMission` under the in-process SEPARATE authority opt-in requires a fresh valid current position for **every** participant.
10. Current position is prepended so current→WP1 crossings are checked.
11. Stale/no-GPS/(0,0)/NaN/Inf start evidence cannot satisfy the preflight.
12. The fresh-start source uses the same navigation-age contract as S09-A, not generic MAVLink packet age.
13. Missing telemetry fails closed rather than silently omitting the initial segment.
14. A future developer adding only a live SEPARATE token cannot bypass this Core preflight.
15. No live SEPARATE token exists now.

Try adversarial cases near the exact 2m / 6m boundaries.

---

## R4-C — Payload / Servo cleanup now uses release-all

Round 3 found the old Core model released only participants whose SET result had
been observed, so a lost/uncertain result could leave a physical payload engaged.
The repair changed the PRE-FLIP model to a conservative Legacy-like release-all
cleanup contract once action handling has entered HOLD/servo processing.

Inspect:

- `backend/internal/mission/payload_action.go`
- `backend/internal/mission/payload_action_test.go`
- Legacy `frontend/swarmgod_gui/app.py::_wp_run_action`

### Required properties

1. Legacy `finish()` really launches RELEASE for every participant on success and error.
2. Core registers every participant as a release obligation once action handling begins.
3. HOLD rejection cannot bypass cleanup.
4. Servo SET rejection cannot bypass cleanup.
5. Lost/uncertain SET result cannot remove that drone's release obligation.
6. Cancel/failsafe blocks every NEW SET immediately.
7. Cancel/failsafe never allows route progression.
8. Cleanup RELEASE remains allowed after cancel/failsafe.
9. Each participant RELEASE is accepted exactly once; duplicate callbacks do not double-release.
10. Cleanup failure is operator-visible.
11. Old action tokens cannot release/mutate a newer action.
12. Active action replacement is still rejected.
13. Verify whether Core waiting for modeled RELEASE results before best-effort progression is a safety-positive divergence from Legacy's fire-and-forget release launch; flag it if it can create a new deadlock/stall contract that matters to a future executor.

No real Servo command execution is enabled by this change.

---

# 4. Intentionally unresolved — do NOT accidentally ratify it

## R4-D — SWARM_LEADER follower operator-takeover policy

This remains a **decision-required flip blocker**.

The current architecture proves:

- Mission owns leader only.
- Formation owns followers.

But product/safety policy has not decided what should happen when the operator takes
over **one follower** during a SWARM_LEADER route:

Option A: terminate the leader mission / break the whole mission contract.

Option B: exclude only that follower and let the leader + remaining formation continue.

The old behavior of stopping the entire formation while allowing the leader mission
to continue must NOT be treated as ratified merely because it exists mechanically.

Verify:

1. `mission_swarm_leader_test.go` no longer encodes "leader continues" as an approved safety assertion.
2. Roadmap explicitly marks this as unresolved.
3. No new code change silently chooses A or B.
4. No SWARM_LEADER live token exists.

Round 4 must NOT resolve this product decision by reviewer preference. Report it as an
explicit remaining flip blocker unless there is separate user/product authorization.

---

# 5. Regression targets from Round 3

Re-check that the post-review repairs did not regress:

- navigation freshness uses position/GPS sample age, never HEARTBEAT age;
- GROUPED formation geometry/order/barrier/rejection semantics;
- SEPARATE route bijection and independent indices;
- SWARM_LEADER explicit non-zero leader and follower failsafe parity;
- WAVE monotonic token isolation across run/group/phase;
- Action monotonic token isolation;
- reconnect is presentation-only and sends zero navigation commands;
- only `core-single` / `core-single-wait` are live authority strings;
- Python remains authoritative for S09-A/B/C/D/E live use.

Also note the existing P2 from Round 3:

The navigation freshness gate now affects live single-drone Core arrival. Before any
new S09 flip, a SITL regression should confirm `core-single` and `core-single-wait`
still progress normally with real stream rates. This is not authorization for HIL or
real flight.

---

# 6. Required verification

Run from `backend`:

```text
go test ./internal/mission ./internal/api ./internal/command ./internal/fleet ./internal/swarm -count=1
go test ./... -count=1
go vet ./...
```

Run the targeted frontend suite covering at least:

- grouped characterization
- separate characterization
- mission shadow / reconnect
- wave
- servo/UI
- command gateway
- command correlation / wire correlation
- authority gating

Then:

```text
git diff --check
```

Do not accept cached Go results; keep `-count=1`.

---

# 7. Required final output

Return exactly these sections:

## A. VERDICT

One of:

- `GREEN FOR PRE-FLIP ONLY`
- `BLOCKED`

Never authorize authority flip in this review.

## B. ROUND-4 FINDINGS

For every finding:

- severity P0/P1/P2
- scope
- file/function
- actual behavior
- expected behavior
- why current tests did/did not catch it
- concrete repair direction

## C. ROUND-3 FINDING CLOSURE TABLE

Rows:

- emergency latency / formation stale writes
- return/land stale writes
- SEPARATE Core collision preflight
- payload release-all cleanup
- follower takeover decision
- single-drone freshness SITL P2

Status each as `CLOSED`, `OPEN`, or `DECISION REQUIRED`.

## D. OWNERSHIP + FINAL-WRITE TABLE

Show writer and cancellation/final-write guard for:

- single Core mission
- grouped multi scaffold
- separate scaffold
- SWARM leader
- SWARM steady follower
- SWARM form-up leader/follower
- swarm RETURN/LAND
- operator takeover
- fleet failsafe

Any same-drone overlapping writer = P0.

## E. LIVE-GATE PROOF

Prove by source that only `core-single` and `core-single-wait` can become live.

## F. TEST RESULTS

Exact commands and results.

## G. REMAINING FLIP BLOCKERS

Must include the unresolved follower-takeover policy unless separately ratified.

If verdict is BLOCKED, finish with one implementer correction prompt.
