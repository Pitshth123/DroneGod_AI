# F7 Mission Failure Injection Evidence

Date: 2026-08-27
Scope: `DroneGod_AI`, SITL/dev only. No hardware/real-flight claim.

## Authority boundary under test

- Core authority is opt-in and SITL-only.
- `core-single` = single-drone GROUPED waypoint authority, no WAIT.
- `core-single-wait` = same boundary plus Core-owned WAIT/HOLD.
- Mission Engine remains command-package independent and emits claim-once intents.
- API adapter executes GOTO/HOLD only through `command.Service` / safety envelope.
- Python must not send mission GOTO or advance from browser target callbacks while Core reports `authority_active=true`.

## Failure matrix

| Scenario | Expected before test | Evidence / Actual | Status |
|---|---|---|---|
| Duplicate Mission Start | Same `operation_id` returns same run; no duplicate GOTO claim | Engine/API regression + live `missionverify` returned run 1 for duplicate Start | PASS |
| Stale Cancel / old run | Old/stale run id cannot affect current run | Existing stale-safe Cancel tests | PASS |
| Cancel before arrival | Mission becomes CANCELLED; stale telemetry cannot advance or emit next GOTO | `TestFailureInjectionCancelAtWaypointBoundarySuppressesNextCommand/cancel-before-arrival` | PASS |
| Cancel after arrival, before next dispatch | Mission becomes CANCELLED; next GOTO claim suppressed | `TestFailureInjectionCancelAtWaypointBoundarySuppressesNextCommand/cancel-after-arrival-before-next-dispatch` | PASS |
| Battery failsafe during WAIT | Mission INTERRUPTED; old WAIT deadline cannot advance; no later GOTO | `TestFailureInjectionBatteryAndLinkDuringWaitNeverResume/battery` | PASS |
| Link failsafe during WAIT | Mission INTERRUPTED; old WAIT deadline cannot advance; no later GOTO | `TestFailureInjectionBatteryAndLinkDuringWaitNeverResume/link` | PASS |
| UI/browser stale target callback under Core authority | Python waypoint index/GOTO unchanged | frontend mission-shadow regression | PASS |
| Lost Start reply | Query same accepted run; never fallback to Python GOTO | frontend lost-reply regression | PASS |
| Cockpit restart during active run | Query Core, rebuild frozen plan/run; no Start/GOTO | frontend F6 restart/rebind regression | PASS |
| UI↔Core disconnect during transit | Core continues; reconnect sees same run | Live SITL: client disconnected after run 1 Start; reconnect reached WAITING on the same run | PASS |
| UI↔Core disconnect during WAIT | Core WAIT persists; reconnect sees same run/plan and remaining/next state | Live SITL: reconnect saw run 1 WAITING with ~7.9s remaining; second disconnect/reconnect preserved run/state | PASS |
| Cancel in live SITL transit | CANCELLED latches despite later telemetry; no silent resume | Live SITL second mission cancelled; state remained CANCELLED after 5s of later telemetry | PASS |
| Core process restart | Fresh in-memory Engine is IDLE; no silent mission resume | Unit test + live SITL: Core killed ~7s after a 250m mission Start, restarted Core returned IDLE/run_id=0 | PASS |
| Core crash / FC onboard failsafe | FC behavior must match configured policy; mission must not silently resume after Core restart | Live SITL: after Core kill, FC continued the last GUIDED target to 14.9604180,102.0986183 then hovered armed at 20m; Core restart did not resume the mission | NO-AUTO-RESUME PASS; FC FAILSAFE CONFIG BLOCKER |
| UI freeze/event-loop stall | Core mission must remain deterministic because Core owns progression | Live client disconnect is a stronger mission-side fault than a frozen UI event loop: Core completed transit/WAIT without any UI process progress | PASS for mission authority |
| FC link loss during transit | Core fleet failsafe emits link ALARM; mission becomes INTERRUPTED and cannot resume | Live SITL process killed during run 1: WARN at 3s, ALARM `link lost 10s — failsafe RTL`, query returned `MISSION_STATE_INTERRUPTED` with same reason | PASS |

## Automated commands/results

- `go test ./internal/mission ./internal/api` — PASS after F7 deterministic additions.
- F6 frontend mission/shadow/failsafe checkpoint — 23/23 PASS.
- Full `go test ./...` checkpoint after F6 — PASS all packages.
- Full frontend regression attempt reached 81% with no reported failures before the 10-minute runner timeout; rerun with a longer timeout is required for the final checkpoint.

## Live SITL harness

- WSL ArduCopter dependency check: pymavlink/pexpect/em/future + `sim_vehicle.py` available.
- `mavproxy` is not installed, but project SITL launcher deliberately uses `--no-mavproxy`; not a blocker for this harness.
- Dedicated test Core: `127.0.0.1:50053`, profile `sitl`, authority `core-single-wait`, separate `logs/missionverify.db`.
- `cmd/missionverify` connects D1 on SITL TCP 5760, takes off, executes two-point Core mission with WAIT, deliberately closes/reopens gRPC connection during transit and WAIT, verifies duplicate Start identity, then starts and cancels a second mission and checks CANCELLED remains terminal.

## F7 exit rule

F7 mission-authority failure injection is DONE for the single-drone GROUPED + WAIT scope. A separate FC safety blocker remains: this SITL configuration continued the last GUIDED target after Core death instead of demonstrating an onboard RTL/LAND policy. F8 may continue in SITL, but F9 hardware and F10 real flight remain prohibited until the intended FC GCS/Core-link failsafe is explicitly configured and bench-verified.
