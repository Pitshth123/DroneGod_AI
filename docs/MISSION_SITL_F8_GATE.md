# F8 Targeted SITL Safety Gate

Date: 2026-08-27
Scope: DroneGod_AI, single-drone GROUPED waypoint + Core-owned WAIT only.
Hardware/real flight is explicitly out of scope for this gate.

## Gate criteria and evidence

| Criterion | Evidence | Status |
|---|---|---|
| Normal Core-owned route repeats | `cmd/missionverify` completes 2-point route under Core authority | PASS 4/4 cycles |
| Route + WAIT repeats | Each cycle enters Core `WAITING`, persists through client disconnect, then completes | PASS 4/4 cycles |
| Duplicate Start | Same operation_id returns the same run, no duplicate mission command | PASS live cycles + automated tests |
| UI/Core disconnect during transit | gRPC client closed/reopened while Core continues mission | PASS live cycles |
| UI/Core disconnect during WAIT | reconnect sees same run and remaining WAIT state | PASS live cycles |
| Cancel repeatedly | second mission in each verifier cycle is cancelled and remains terminal | PASS live cycles |
| Cleanup | verifier waits for landed/disarmed rather than merely sending LAND | PASS cycles 1-2 |
| Link interruption | kill SITL link > LinkLostSec => Core WARN 3s, ALARM 10s, mission INTERRUPTED | PASS F7 live injection |
| Battery/link WAIT preemption | deadline cannot advance after Core interrupt | PASS deterministic F7 tests |
| Core process crash | restarted Core is IDLE / run_id=0, no silent mission resume | PASS F7 live process-kill |
| FC behavior after Core crash | SITL FC continued last GUIDED target and hovered armed at 20m | BLOCKER for F9 until FC failsafe policy is configured + bench verified |
| Run-state leak stress | 500 terminal mission cycles, alternating Cancel/Complete, no active run or late command claim | PASS |
| GOTO/HOLD failsafe race guard | `command.Service` rejects both GOTO and HOLD when fleet failsafe latch is active; static order regressions require guard before navigation/mode send | PASS |
| Go regression | `go test ./...` after HOLD failsafe hardening | PASS all packages |
| Frontend full regression | `python -m pytest tests -q` with 30m runner timeout | PASS — 976 passed in 736.56s (12:16) |

## Live repeated cycles

### Cycle 1
- CONNECT, GPS/EKF warm-up, TAKEOFF accepted.
- Duplicate Start returned run 1.
- gRPC disconnect during transit; reconnect reached `WAITING` on run 1.
- second disconnect during WAIT; reconnect preserved same run and WAIT.
- mission completed at revision 6.
- second mission Cancel remained terminal.
- cleanup confirmed landed/disarmed.
- Result: PASS.

### Cycle 2
- Existing fleet connection handled safely (`drone 1 already connected`).
- TAKEOFF accepted after landed state.
- Duplicate Start returned run 3 (monotonic Core run identity; prior cancelled run consumed run 2).
- transit and WAIT disconnect/reconnect scenarios repeated successfully.
- mission completed; cancel scenario remained terminal.
- cleanup confirmed landed/disarmed.
- Result: PASS.

### Cycle 3
- Existing connection reused safely; TAKEOFF accepted.
- Duplicate Start returned run 5.
- transit and WAIT disconnect/reconnect scenarios repeated successfully.
- mission completed; cancel scenario remained terminal.
- cleanup confirmed landed/disarmed.
- Result: PASS.

### Cycle 4
- Existing connection reused safely; TAKEOFF accepted.
- Duplicate Start returned run 7.
- transit and WAIT disconnect/reconnect scenarios repeated successfully.
- mission completed; cancel scenario remained terminal.
- cleanup confirmed landed/disarmed.
- Result: PASS.

### Core process growth sample
- Idle after cycle 3: working set 21.65 MB, private 53.37 MB, 11 threads, 155 handles.
- Idle after cycle 4: working set 21.67 MB, private 53.61 MB, 11 threads, 155 handles.
- Delta across another complete route+WAIT+disconnect+cancel+land cycle: +0.02 MB working set, +0.24 MB private, 0 threads, 0 handles. No obvious monotonic process-growth signal in this bounded repeated-SITL gate.

## Safety blocker carried to F9

Core mission restart semantics are fail-closed, but FC behavior is not yet safe to assume when the Core process disappears. In live SITL process-kill characterization the FC retained the last GUIDED navigation target, reached it without Core, and then hovered armed at mission altitude. Therefore:

- F8 may validate application-level mission authority/restart behavior.
- F9 hardware bench MUST verify the intended onboard GCS/link failsafe configuration on the actual flight controller.
- Hardware or real-flight readiness MUST NOT be claimed from the F8 PASS alone.

## F8 exit decision

**F8 DONE for the limited single-drone GROUPED+WAIT software/SITL scope.** Four repeated live SITL cycles passed with disconnect/reconnect, WAIT, duplicate Start, Cancel and landed/disarmed cleanup; 500 terminal Engine cycles showed no active-run leak; bounded Core process growth showed no obvious monotonic leak; full Go regression passed after GOTO/HOLD failsafe hardening; full frontend regression passed 976/976.

F9 remains **PREPARED / BLOCKED on actual FC failsafe configuration and hardware bench verification** regardless of F8 PASS. F10 controlled real flight remains prohibited until F9 passes.
