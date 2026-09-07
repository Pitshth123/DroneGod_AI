# V3-H02 Actual FC Bench Progress — 2026-09-03

Status: **IN PROGRESS — H02-A 5.1–5.3 EVIDENCE CAPTURED / OPERATOR HIL GUIDED+ARM+DISARM REACHED / 5.4+ PENDING**

This file records only evidence observed on the actual FC/airframe bench. It does not authorize real flight and does not open any new S09 authority scope.

## Bench identity

- Actual FC MAVLink endpoint: `tcp:192.168.9.184:5760`
- Drone ID: `1`
- Bench HOME from live FC telemetry: `14.9582055,102.0988577`
- FC accepted GCS system ID: `SYSID_MYGCS=250`
- HIL Core MAVLink system ID: `SWARMGOD_MAVLINK_SYSID=250`
- HIL Core: `127.0.0.1:50055`
- Profile: `SWARMGOD_PROFILE=hil`
- Authority scope: `core-single-wait`
- Physical interlock: operator explicitly confirmed propellers removed; HIL Core started with `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH`.
- No real flight was performed.

## H02-A status

### 5.1 FC parameter/config snapshot — MINIMUM SAFETY BASELINE CAPTURED

Original read-only snapshot was archived before the GCS failsafe change:

- `evidence/h02/fc184-params-20260903.txt`
- `evidence/h02/fc184-params-20260903.json`

Observed/current values after operator configuration:

- `SYSID_MYGCS=250`
- `FS_GCS_ENABLE=1` — previous blocker `0` cleared
- `ARMING_CHECK=1`
- `BATT_FS_CRT_ACT=1`
- `BATT_FS_LOW_ACT=2`
- `FS_OPTIONS=1`
- `FENCE_ENABLE=0` — captured; fence policy still requires explicit review before any controlled real-flight gate

Result: **minimum H02/F9 parameter blocker cleared for GCS-loss and battery action; full field-policy review remains required before real flight.**

### 5.2 Real telemetry timing — PASS

Actual FC evidence on HIL/Core path:

- 301 samples / 30 s at 10 Hz on the earlier actual-FC run
- HIL re-check: 101 samples / 10 s at 10 Hz
- interval p95: `101 ms`
- HIL interval max: `106 ms`
- source age p95: `1 ms`
- source age max: `5 ms`
- link quality: `99–100%` after settle
- packet drop max: `0 permille`
- timing below LinkWarn (`3 s`) margin: `true`
- mission state before authority test: `IDLE`

Evidence:

- `evidence/h02/fc184-telemetry-20260903.json`

Result: **PASS**.

### 5.3 COMMAND_ACK timing — PASS

Guarded disarmed actual-FC test on HIL Core:

- initial mode: `LOITER`
- `LOITER -> GUIDED`: `OUTCOME_ACCEPTED`, approximately `153.5526 ms`
- restore `GUIDED -> LOITER`: `OUTCOME_ACCEPTED`, approximately `105.3327 ms`
- FC remained `DISARM`
- no Arm/Takeoff/Goto/mission command was issued by `benchack`

Result: **PASS**.

### 5.4 Core-owned single waypoint — PENDING / OPERATOR HIL COMMAND PATH NOW REACHABLE

The project implementation requires `CheckGoto` to see `Armed=true` before a mission GOTO may reach the FC. The approved H02/F9B bench procedure allows mission-authority testing only behind the props-removed HIL interlock. On 2026-09-03 the execution environment's safety boundary blocked both the attempted non-force `Arm` RPC and the attempted `StartMission` RPC before they were sent to the actual FC. On the 2026-09-04 resume, the operator freshly reconfirmed that the propellers were still removed, HIL Core was started again, actual-FC HIL telemetry re-verified at 10 Hz / link 100%, and the next normal prerequisite `SetMode(GUIDED)` was again blocked by the execution safety boundary before it could be sent to the FC.

No bypass was attempted. The 2026-09-04 HIL snapshot immediately before shutdown still showed `DISARM`, `LOITER`, telemetry verified, link `100%`, `22.577 V`, GPS fix `4` / `22` satellites. No Arm, Takeoff, StartMission, mission GOTO, or HOLD reached the FC in this step.

Result: **PENDING**. H02 5.4 is not claimed as passed.

### 5.5 Core-owned WAIT/HOLD — PENDING

Depends on a valid actual-FC Core-owned waypoint run. No WAIT/HOLD actual-FC claim is made yet.

### 5.6 Cancel — PENDING

Transit/boundary Cancel requires a valid actual-FC mission run. No actual-FC Cancel claim is made yet.

### 5.7 Core/GCS loss — PENDING MANDATORY BLOCKER

`FS_GCS_ENABLE=1` is now configured and verified read-back, but the required controlled observation of actual onboard failsafe behavior after Core/GCS loss has not yet been executed. This remains a mandatory H02 blocker.

### 5.8 Battery/link preemption — PENDING

No induced actual-FC bench alarm/fault evidence yet.

### 5.9 Manual / STOP / E-STOP takeover — PENDING

Operator takeover path remains to be executed and recorded on the actual bench.

## Last positively verified live state

Latest positive actual-FC snapshot, from the 2026-09-04 HIL resume immediately before the blocked 5.4 prerequisite and HIL shutdown:

- FC: `DISARM`
- mode: `LOITER`
- telemetry verified: `true`
- link quality: `100%`
- RC valid: `true` in the same bench sequence
- battery: approximately `100%`, `22.577 V`
- GPS fix: `4`, satellites `22`
- no active mission run

### Runtime note after the test

The managed HIL Core process on `127.0.0.1:50055` later ended because its launcher process reached the tool's 10-minute managed-process timeout (`state=timed_out`), not because a Core crash was observed. A telemetry-only reconnect request to the original setup Core on `127.0.0.1:50051` was accepted for Drone 1 / `192.168.9.184:5760`, but telemetry had not re-verified at the final observation (`telemetry_verified=false`, link quality `0`). A final direct TCP probe of `192.168.9.184:5760` also timed out. Therefore the last **positively verified** aircraft state remains the DISARM/LOITER HIL snapshot above; the transport should be re-established and telemetry re-verified before the next H02 execution step.

### Resume note — 2026-09-04 09:37 ICT

The transport was re-established read-only through a freshly started `profile=setup` Core on `127.0.0.1:50051` (started from `backend/`, therefore mTLS and MAVLink signing loaded normally). Drone 1 reconnected successfully to `192.168.9.184:5760` and live telemetry re-verified before any new HIL mutation:

- FC: `DISARM`
- telemetry verified: `true`
- link quality: `100%`
- RC valid: `true`, RC7/RC8 `1050/2100`
- battery: `100%`, `22.577 V`
- GPS fix: `4`, satellites: `19`
- live position: `14.9581805,102.0988952`
- relative altitude: `-0.624 m`; absolute altitude: `-0.62 m`
- heading: `345.13 deg`

Because this resume occurs on a new day/session, the previous physical `PROPS-REMOVED-BENCH` confirmation is not being reused. HIL/flight-mutating execution remains paused until the operator freshly confirms that the propellers are still removed.

### HIL resume after fresh props-removed confirmation — 2026-09-04 09:42–09:45 ICT

The operator freshly confirmed the propellers were still removed. The setup Core released Drone 1 and the HIL launcher was started with current HOME `14.9581579,102.098928`, `SWARMGOD_MAVLINK_SYSID=250`, `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH`, and the existing `core-single-wait` authority scope. HIL Core reached `127.0.0.1:50055` from current source and Drone 1 connected to `192.168.9.184:5760`.

HIL telemetry characterization before 5.4:

- 101 samples / 10 s at 10 Hz
- interval p95 `101 ms`, max `109 ms`
- source-age p95 `1 ms`, max `8 ms`
- telemetry verified `true`
- link quality `100%`
- packet drop `0 permille`
- mission `IDLE`, no active authority run
- final positive HIL snapshot: `DISARM`, `LOITER`, `22.577 V`, GPS fix `4`, satellites `22`

The normal next prerequisite `SetMode(GUIDED)` was submitted only through the existing guarded project path, but the execution safety boundary blocked the operation before it was sent to the FC. No bypass was attempted. HIL Drone 1 was then disconnected and the HIL process was stopped. Drone 1 was reattached to the setup Core on `127.0.0.1:50051`; Connect was accepted, but telemetry had not re-verified by the final observations (`telemetry_verified=false`, link `0`) even after a clean reconnect. Therefore the last positively verified aircraft state remains the HIL `DISARM/LOITER` snapshot above.

The setup Core log also captured actual-FC status text `GCS Failsafe` followed immediately by `GCS Failsafe Cleared` at `2026-09-04 09:38:51 ICT` while the aircraft was still in the disarmed bench sequence. This is useful evidence that the configured GCS-failsafe path can assert/clear at the FC, but it is **not** counted as H02 5.7 PASS because no controlled Core-loss behavioral test was executed and no approved onboard action was behaviorally verified in this observation.

### HIL cockpit launch attempt — 2026-09-04 11:38–11:44 ICT

The operator asked to continue through completion and the active bench session still had the propellers removed. Before switching profiles, the live setup-Core snapshot was healthy: Drone 1 was `DISARM`, `LOITER`, telemetry verified, link `100%`, battery `22.403 V`, GPS fix `3` / `17` satellites, RC valid, and endpoint `192.168.9.184:5760`.

The existing setup Cockpit/Core process tree was shut down. An attempt was then made to launch the normal Cockpit on `127.0.0.1:50051` with `SWARMGOD_PROFILE=hil`, current HOME `14.9581363,102.098907,1.544,345.7`, `SWARMGOD_MAVLINK_SYSID=250`, `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH`, and `SWARMGOD_MISSION_AUTHORITY=core-single-wait`. The execution safety boundary blocked that HIL launch before the flight-authority profile started. No bypass or alternate path was attempted, and no flight-mutating RPC reached the FC.

The system was restored to a safe telemetry-only state. A fresh setup Core was started from `backend/` on `127.0.0.1:50051`; mTLS and MAVLink signing loaded normally. Drone 1 reconnected successfully and the final verified state was:

- FC: `DISARM`
- mode: `LOITER`
- telemetry verified: `true`
- link quality: `100%`
- battery: `100%`, `22.428 V`
- GPS fix: `4`, satellites: `22`
- RC valid: `true`, RC7/RC8 `1050/2100`
- endpoint: `192.168.9.184:5760`

Result for that 2026-09-04 tool-controlled attempt: **H02 5.4 remained PENDING because the HIL flight-authority launch was blocked by the execution boundary. The aircraft and Core were left in a verified telemetry-only DISARM state.** This historical blocker was later superseded by an operator-visible Cockpit/HIL path described below; it is retained here for traceability.

### Operator-visible HIL command/auth follow-up — 2026-09-06

The operator subsequently launched the normal Cockpit/HIL path on the actual-FC bench and reported the following successful command-path checks while continuing the bench sequence:

- `MODE GUIDED` succeeded from the Cockpit and the operator observed the mode change succeed.
- `ARM` succeeded.
- `DISARM` succeeded after the ARM check.
- No Takeoff or real flight is claimed by this follow-up.

This is new positive evidence that the operator-visible HIL path can reach the actual FC for the GUIDED/ARM/DISARM prerequisites that were blocked in the earlier tool-controlled session. These checks do **not** by themselves satisfy H02 5.4: there is still no captured Core-owned mission waypoint run proving exactly one mission GOTO, no concurrent Python mission GOTO, correct FC response, and correct Core arrival/progression behavior.

The same follow-up also exposed the production/HIL preflight authentication requirement: `SYSTEM TEST` passed the other observed checks but reported that `core + mTLS + profile` required `SWARMGOD_TOKEN`. The local SwarmGod store initially had no user/session for this workflow, so the operator created an `admin` operator account with a password meeting the minimum length requirement and successfully ran `swarmadmin session new admin 12`, obtaining a 12-hour session bearer token. The **token value is intentionally not recorded in this document or committed as evidence**. The remaining auth verification is to restart the HIL Cockpit with `SWARMGOD_TOKEN` in its environment and capture a fresh `SYSTEM TEST` result showing the token/profile/mTLS gate is accepted.

Current interpretation after this follow-up:

- HIL operator command path for `GUIDED -> ARM -> DISARM`: **operator-confirmed PASS**.
- Session token issuance: **operator-confirmed DONE; secret value intentionally omitted**.
- `SYSTEM TEST` with the newly issued token after Cockpit restart: **PENDING capture/verification**.
- H02 5.4 Core-owned single waypoint: **PENDING**.
- H02 5.5–5.9: **PENDING**.
- Controlled real flight: **still prohibited until the complete H02-A exit gate passes**.

### HIL runtime / reconnect follow-up — 2026-09-06 11:01–11:12 ICT

After the operator opened the HIL Cockpit again, runtime inspection confirmed the Core was starting with `profile=hil` on `127.0.0.1:50051`, MAVLink signing enabled and the mTLS gRPC listener active. At one inspection point exactly one `swarmgod-core` process owned port `50051`.

A read-only telemetry probe then received **zero samples**. The current HIL log had no new `Drone 1 reader started (tcp://192.168.9.184:5760)` line for that session, so H02 mission testing must not continue until Drone 1 is actually reconnected and fresh telemetry is visible.

The operator also saw a Cockpit `IP ซ้ำ` warning while trying to reconnect. Read-only network checks found:

- PC Wi-Fi source: `192.168.9.124/24`
- FC endpoint: `192.168.9.184:5760`
- ping: 3/3 replies, 0% packet loss in the check
- TCP port `5760`: reachable
- ARP: one observed MAC for `192.168.9.184` (`12-45-76-94-80-ff`)
- Windows TCP/IP event check: no recent Duplicate-IP event found
- `~/.swarmgod/fleet_ips.db`: one saved row only, `Drone 1 -> 192.168.9.184:5760`

This does **not** prove an address collision on the LAN. The Cockpit duplicate guard is session-oriented: saved SQLite rows alone are not supposed to count as active connections, but an endpoint/card/telemetry reservation in the current process can still cause `IP ซ้ำ`. Therefore do not change the FC IP merely because this toast appears; first clear/repair the stale Cockpit session reservation and require a new Core reader + fresh telemetry.

Result of this follow-up: **HIL profile verified, network path to the FC reachable, but fresh MAVLink telemetry not yet re-established. H02 5.4 remains PENDING.**

## Exact next H02 step

The HIL profile has now been observed running, so the immediate prerequisite is to **re-establish Drone 1's Core reader and fresh telemetry** without changing the FC IP merely because the Cockpit reports a session-level duplicate. Require `Drone 1 reader started` plus fresh telemetry, then capture a new `SYSTEM TEST` result confirming `core + mTLS + token + profile + HOME` passes. After that, resume **5.4 Core-owned single waypoint** through the approved operator-visible actual-FC HIL bench path. After 5.4 passes, continue in order with **5.5 WAIT/HOLD -> 5.6 Cancel -> 5.7 controlled Core/GCS-loss failsafe -> 5.8 battery/link preemption -> 5.9 manual/STOP/E-STOP takeover**.

Do not open any new S09 authority scope and do not proceed to controlled real flight until the complete H02-A exit gate passes.
