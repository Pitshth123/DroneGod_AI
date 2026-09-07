# Architecture Migration Progress

> **Canonical roadmap is now `docs/V3_MASTER_ROADMAP.md` (2026-08-28).**
> This notebook remains historical implementation/evidence detail. Old `V1 Phase ...` and `V2 F...` labels below are preserved for traceability only; current status/next-step naming must use V3 IDs.

> สมุดส่งเวรหลัก (handoff notebook) ของงาน Low-Risk Architecture Migration
> ถ้า session จบ / quota หมด / เปลี่ยน AI → เปิดไฟล์นี้แล้วทำต่อได้ทันที
> ไฟล์นี้สำคัญกว่า `CODEX_CHANGES.md` (นั่นเก็บของที่ทำเสร็จ; ไฟล์นี้เก็บงานค้าง + exact next step + เหตุผล)

## Latest Checkpoint — V3-H02 ACTUAL FC BENCH IN PROGRESS / OPERATOR HIL PATH REACHED (2026-09-06)
- **ROADMAP STATE:** V3-S01–S08 DONE; V3-S09 independent PRE-FLIP review PASS at 0 Critical / 0 High with deferred live tokens still OFF and the separate administrative freeze record still pending; V3-S10/S11 COMPLETE; V3-S12 current operational endurance gate COMPLETE; V3-H01 DONE; **V3-H02 IN PROGRESS**; V3-R01 LOCKED.
- **H02 EVIDENCE:** 5.1 parameter/config minimum baseline PASS, 5.2 real telemetry timing PASS, 5.3 guarded disarmed COMMAND_ACK timing PASS. `FENCE_ENABLE=0` remains a field-policy review item before controlled real flight.
- **OPERATOR-VISIBLE HIL FOLLOW-UP:** operator reported successful `MODE GUIDED`, `ARM`, then `DISARM` from the normal Cockpit/HIL path. No Takeoff or real flight is claimed. This supersedes the earlier tool-controlled inability to reach those prerequisites, but it does **not** satisfy 5.4 because no Core-owned mission waypoint/GOTO evidence has been captured yet.
- **AUTH:** local store initially had no user; operator created `admin` operator with a password meeting the minimum length and successfully issued a 12-hour `SWARMGOD_TOKEN`. The secret token value is intentionally not recorded. A fresh post-token SYSTEM TEST result still needs capture.
- **2026-09-06 HIL RUNTIME CHECK:** Core was observed running `profile=hil` on `127.0.0.1:50051`, one Core process, mTLS listener and MAVLink signing active. A telemetry probe received zero samples because Drone 1 had not successfully established a Core reader in that HIL session yet.
- **NETWORK / IP-DUPLICATE DIAGNOSIS:** PC Wi-Fi `192.168.9.124/24`; actual FC `192.168.9.184:5760` replied to ping and TCP 5760 probe. Windows showed no recent Duplicate-IP event and ARP showed one MAC for `.184`. `~/.swarmgod/fleet_ips.db` contained one saved `Drone 1 -> 192.168.9.184:5760` row only. Therefore the Cockpit `IP ซ้ำ` warning is a session-level duplicate/reservation condition, not proven LAN address collision. The duplicate guard correctly no longer treats old SQLite rows alone as active connections; current endpoint/telemetry/card state must be checked.
- **CURRENT BLOCKER:** re-establish Drone 1 reader + fresh telemetry in HIL, then capture SYSTEM TEST with token/profile/mTLS/HOME, then resume H02 5.4. 5.5–5.9 remain pending and 5.7 controlled Core/GCS-loss behavior remains mandatory.
- **EXACT NEXT STEP:** fix/clear only the stale Cockpit session endpoint reservation if necessary, reconnect `Drone 1 -> 192.168.9.184:5760`, require `Drone 1 reader started` + fresh telemetry, run SYSTEM TEST, then continue 5.4 Core-owned waypoint through the approved operator-visible HIL bench path. Do not flip S09 and do not open R01.

## Previous Checkpoint — V3-H02 ACTUAL FC BENCH 5.1–5.3 PASS / 5.4 EXECUTION BLOCKED (2026-09-04)
- **ACTUAL FC:** Drone 1 at `192.168.9.184:5760`; operator explicitly confirmed propellers removed for the 2026-09-03 HIL bench. That physical confirmation is session-specific and is not reused automatically on a later day.
- **2026-09-04 RESUME:** setup Core/mTLS was restarted read-only on `127.0.0.1:50051`; Drone 1 reconnected and telemetry re-verified while DISARM (link 100%, RC valid, 22.577 V, GPS fix 4 / 19 sats, position `14.9581805,102.0988952`). The operator then freshly reconfirmed propellers removed. HIL Core was started from current source with HOME `14.9581579,102.098928`, sysid `250`, and the props-removed interlock; HIL telemetry passed 101 samples/10 s at 10 Hz, link 100%, drop 0, mission IDLE. The final positive HIL snapshot remained DISARM/LOITER, 22.577 V, GPS fix 4 / 22 sats.
- **PARAMETER GATE:** original snapshot archived. Current verified reads include `SYSID_MYGCS=250`, `FS_GCS_ENABLE=1`, `ARMING_CHECK=1`, `BATT_FS_CRT_ACT=1`, `BATT_FS_LOW_ACT=2`, `FS_OPTIONS=1`, `FENCE_ENABLE=0`. The previous `FS_GCS_ENABLE=0` blocker is cleared; fence/field policy still requires review before real flight.
- **TELEMETRY:** actual-FC HIL path measured 10 Hz, interval p95 `101 ms`, source-age p95 `1 ms`, link `99–100%` after settle, drop `0 permille`.
- **COMMAND_ACK:** guarded disarmed `LOITER→GUIDED` accepted in ~`153.6 ms`; restore `GUIDED→LOITER` accepted in ~`105.3 ms`; FC remained DISARM.
- **CURRENT BLOCKER:** H02 5.4 Core-owned waypoint is **PENDING / EXECUTION BLOCKED**. The code requires `Armed=true` before GOTO transport. On 2026-09-03 non-force Arm and StartMission were blocked before send; on the fresh 2026-09-04 props-removed HIL resume, the prerequisite `SetMode(GUIDED)` was blocked before reaching the FC. At 11:38 ICT a further attempt to relaunch the normal Cockpit itself in `profile=hil` on `127.0.0.1:50051` with current HOME, sysid `250`, `PROPS-REMOVED-BENCH`, and `core-single-wait` was blocked by the execution safety boundary before HIL authority started. No bypass was attempted; no Arm, Takeoff, StartMission, mission GOTO, or HOLD reached the FC.
- **SAFE RESTORE:** after the blocked HIL launch, a fresh `profile=setup` Core was started from `backend/` on `127.0.0.1:50051` with mTLS and MAVLink signing. Drone 1 reconnected to `192.168.9.184:5760`; final verified state: `DISARM`, `LOITER`, telemetry verified, link `100%`, battery `22.428 V`, GPS fix `4` / `22` sats, RC valid `1050/2100`. The SwarmGod login window was also reopened against the restored setup Core.
- **EXACT NEXT STEP:** resume at H02 5.4 only through an approved actual-FC HIL bench execution path that is permitted to start the HIL authority profile, then 5.5 WAIT/HOLD → 5.6 Cancel → 5.7 controlled Core/GCS-loss failsafe (mandatory) → 5.8 battery/link preemption → 5.9 manual/STOP/E-STOP takeover. Full evidence: `docs/H02_ACTUAL_FC_BENCH_PROGRESS_20260903.md`. No new S09 authority scope and no controlled real-flight gate are authorized.

## Previous Checkpoint — V3-S09 FRESH INDEPENDENT PRE-FLIP RE-REVIEW PASS / READY TO FREEZE (2026-08-30)
- **CURRENT VERDICT:** `FINAL REVIEW PASS — 0 CRITICAL / 0 HIGH — S09 PRE-FLIP BASELINE READY TO FREEZE`. This review does **not** itself mark the baseline frozen and authorizes **no authority flip**.
- **CURRENT-SOURCE FAILSAFE PROOF:** Mission GOTO/HOLD retain their composed fleet final-write guard; steady-state Formation retains its guarded `GotoYawContext`. The later form-up gap is closed at the actual transport boundary for the leader pin and every follower transit/horizontal/final-slot write using the exact aircraft ID. Generic SINGLE/GROUPED automatic Return now composes `WithFailsafeSendGuard(...)` before RTL while preserving the Return lease/cancellation guard.
- **LOCK/ACK SCOPE:** `WithAdditionalSendGuard` keeps the existing ownership/cancellation guard outermost and the fleet failsafe guard inner. `DoIfFailsafeInactive` covers only `conn.Send`; `fleet.sendCmd` waits for COMMAND_ACK after `guardedSend` returns, so `fsMu` is not held during ACK waiting.
- **PRIOR CLOSURES REVERIFIED:** typed-nil swarm coordinators return nil safely and SWARM_LEADER StartMission without a manager fails closed; SWARM_RETURN guards GOTO/LAND/fallback RTL; takeover arbitration determines accepted targets before Mission/Formation/Return side effects; rejected weaker targets remain side-effect free; Return distinguishes Succeeded/Cancelled/Failed; succession Return uses current membership and excluded participants do not auto-rejoin.
- **RESTART/AUTHORITY:** active, WAIT, SEPARATE, SWARM_LEADER, succession, RETURN_PENDING/RETURNING, and payload evidence restore command-inert with no automatic navigation. `missionAuthorityMode` still recognizes only `core-single` and `core-single-wait`; every S09 token remains OFF.
- **INDEPENDENT VALIDATION:** form-up, generic Return RTL, composed-guard, and prior blocker regressions PASS ×10; mission succession/Return regressions PASS ×10; targeted six-package gate PASS; restart/live-gate regressions PASS; full `go test ./... -count=1` PASS; `go vet ./...` PASS; `git diff --check` PASS with only two pre-existing frontend CRLF conversion warnings. Race detector was not run by policy. No frontend rerun was required because no protobuf/UI contract changed.
- **MEDIUM:** durable membership-before-FC-send ordering remains **DEFERRED / NON-BLOCKING**. Restart remains command-inert and no evidence justifies elevation.
- **LOW:** `internal/fleet/testsupport.go` exposes internal-package test constructors in a production-compiled file because cross-package tests cannot import `_test.go` helpers. It has no I/O, goroutines, or production caller and is not a freeze blocker; future cleanup may move the harness behind a narrower internal testing package if desired. The leader and first-follower form-up tests latch failsafe before entering `formUpSequential` rather than pausing after planning; they still exercise the real final-write guard (there is no intervening form-up failsafe precheck), while the later-phase test proves a mid-sequence latch. A future test-only planning barrier could make that chronology more literal without changing the verified production result.
- **EXACT NEXT STEP:** perform the separate administrative action **FREEZE S09 PRE-FLIP SOFTWARE BASELINE**. Freeze must retain every S09 live token OFF and does not authorize deployment, HIL/FC control, H02 execution, or real flight. After freeze, stop S09 software edits unless a new proven defect appears and proceed according to the staged validation plan; V3-H02 remains future hardware work and V3-R01 remains locked.

## Previous Checkpoint — V3-S09-C TAKEOVER/SUCCESSION PRE-FLIP COMPLETE (2026-08-30)
- **PRODUCT CONTRACT:** ratified; no longer `DECISION REQUIRED`. Follower takeover excludes only that follower and the remaining swarm continues the SAME mission. Leader takeover excludes the old Leader and promotes the next eligible participant in ORIGINAL participant order, preserving `run_id` and route/index. Excluded participants never auto-rejoin the run. No eligible successor / below minimum formation ⇒ mission `INTERRUPTED`. No automatic SWARM_LEADER→SINGLE conversion; no invented RTL/LAND.
- **IMPLEMENTATION:** three existing owners, no parallel safety system. `Engine.BeginSwarmOperatorTakeover`/`CompleteSwarmOperatorTakeover` hold membership + succession revision and freeze progression while a handoff is pending; `Server.applySwarmMissionTakeoversLocked` runs before the generic ownership cancel and revokes the leader's in-flight send; `swarm.Manager.RebindMissionMembership` swaps leader/members atomically and bumps `formationGen` so the previous formation generation is stale. Mission ownership follows the current runtime leader only, so an excluded drone is owned by neither mission nor formation.
- **CONTINUATION FIXES:** `Run.leaderID()` no longer falls back to the frozen `Plan.LeaderID` once succession membership is tracked — the deliberate "no successor remains" state (`currentLeaderID = 0`) previously reported the EXCLUDED original leader into snapshot and durable evidence. The post-promotion observation gate is unchanged (deliberately EXCLUSIVE); its timing-dependent test was made deterministic with a fake clock.
- **PERSISTENCE/RESTART:** the promoted leader plus the active/excluded partition are durable, and validation accepts a legitimately promoted leader while rejecting inconsistent membership. Restart after AND during a succession restores as `INTERRUPTED` + recovery-required, `Active=false`, `Authority=false`, zero automatic commands, with no auto-rejoin of excluded members.
- **AUTHORITY GATE:** unchanged. `core-swarm-leader` has no production caller (`EnableSwarmLeaderAuthority()` is test-only) and `missionAuthorityMode` accepts only `core-single` / `core-single-wait`.
- **VALIDATION:** targeted backend scope 6/6 packages PASS; full backend 29/29 packages PASS; `go vet ./...` PASS with zero diagnostics; full frontend suite 1110 passed with 3 pre-existing environment errors (`PermissionError [WinError 5]` on pytest's own temp root in `test_v3_s12_endurance.py`, reproducible in isolation, unrelated to this change — no frontend file was modified). Race detector not run. No SITL, hardware, FC, or controlled-flight evidence is claimed.
- **EXACT NEXT STEP:** independent frozen-source review of this delta plus the still-open Round 4 re-check of the async revocation/final-write-guard design; then a separate explicit authority-validation/flip task. Do not combine with H02 baseline or V3-R01.

## Previous Checkpoint — V3-S09-F RETURN POLICY PRE-FLIP COMPLETE (2026-08-30)
- **PRODUCT CONTRACT:** ratified. `rtl_after` is now compatibility input mapped to an explicit Return Policy (`NONE`, `RTL_ALL_AFTER_MISSION`, `SWARM_RETURN`, `WAVE_MANAGED_RETURN`), not a direct command boolean.
- **IMPLEMENTATION:** natural completion retires mission navigation before Return can claim ownership. Generic Return uses command.Service, stable request identity, whole-batch per-target reservations, cancellable/final-write guards, and no ownership lock across FC ACK waits. SWARM delegates to the existing Swarm Return manager; WAVE keeps one managed return path.
- **INVALIDATION:** Cancel, operator/emergency takeover, and fleet/FC failsafe suppress pending/returning intent permanently. The automatic RTL entry checks the fleet failsafe latch so fleet/FC remains the sole safety owner.
- **PERSISTENCE/RESTART:** resolved policy and Return lifecycle evidence are durable. Pending/returning records restore as `INTERRUPTED` + Return recovery-required evidence, `Active=false`, `Authority=false`, with zero automatic commands.
- **DEFERRED BY CONTRACT:** SEPARATE `RETURN_EACH_ON_ROUTE_COMPLETE` is modeled but rejected pending corridor safety. (At this historical checkpoint the SWARM operator-takeover policy had not yet been ratified; it was later ratified and implemented PRE-FLIP in S09-C.)
- **AUTHORITY GATE:** unchanged. Only `core-single` and `core-single-wait` are live and they still reject Return input. No token enables the new pre-flip execution gate; `core-grouped-multi`, `core-separate`, `core-swarm-leader`, `core-wave`, and `core-payload` remain OFF.
- **VALIDATION:** exact targeted backend scope 6/6 packages PASS; full backend 29/29 packages PASS; `go vet ./...` PASS with zero diagnostics; affected frontend scope 378/378 tests PASS (one pytest cache-permission warning only). No SITL, hardware, FC, or controlled-flight evidence is claimed; actual FC evidence remains V3-H02 work and controlled real flight remains locked.
- **EXACT NEXT STEP:** independent frozen-source review, then a separate explicit authority-validation/flip task. Do not combine it with H02 baseline or V3-R01.

## Previous Checkpoint — V3-S12 CURRENT OPERATIONAL GATE COMPLETE (2026-08-30)
- **CURRENT TRACK:** `docs/V3_MASTER_ROADMAP.md`
- **CURRENT STAGE:** **V3-S12 — SITL Endurance + Performance**. Current operational gate is complete. The user elected to exceed the earlier 30-minute minimum with a **6-hour continuous overnight single-drone SITL soak**, followed by reconnect and client-lifecycle evidence.
- **PRIOR V3 STATUS:** V3-S01–S08 COMPLETE; V3-S09 **PRE-FLIP COMPLETE / AUTHORITY NOT FLIPPED**; V3-S10 COMPLETE; V3-S11 COMPLETE.
- **S12 TOOLING:** canonical runner `scripts/v3_s12_endurance.py`; duration/scenario configurable; machine-readable JSON evidence; resource/telemetry/RPC/reconnect/mission/file-growth metrics; deterministic cleanup; deferred extended-endurance commands remain documented in `docs/V3_S12_SITL_ENDURANCE_PERFORMANCE.md`.
- **OVERNIGHT ENDURANCE EVIDENCE:** `idle-connected` **6h PASS** = 21,600.172 s / 216,002 telemetry / 723 RPC / 0 RPC error / 0 stream error / 0 UI stall / no harness hard failure or investigate trend. `reconnect-churn` **15m PASS** = 15 reconnects / 9,002 telemetry / 0 RPC-stream error. `client-lifecycle` **15m PASS** = 75 create/snapshot/close cycles / 9,003 telemetry / 0 RPC-stream error / Core OS threads stable.
- **RESOURCE SUMMARY:** 6h Python RSS +1.91 MB with no monotonic-growth investigation flag; Python OS threads 16→13; Core RSS 132.4 MB→31.9 MB after startup settling; Core OS threads 11→12; mean RPC latency ~0.98 ms. Database file size delta 0 B.
- **FOLLOW-UP OBSERVATION:** Core stdout recorded one `registry upsert drone 1 failed: database is locked (5) (SQLITE_BUSY)` warning during the 6h run. Telemetry/RPC continued and the warning did not repeat in captured stdout. Track as non-blocking SQLite contention follow-up; it did not cause an endurance or flight-safety failure in this run.
- **REGRESSION:** S12 tooling tests **18 PASS**; affected frontend S12/telemetry/lifecycle suite **48 PASS**; backend full `go test ./... -count=1` PASS; `go vet ./...` PASS; prior `git diff --check` PASS apart from pre-existing CRLF warnings.
- **DEFERRED EXTENDED ENDURANCE:** 12h/24h soak, 5-drone / 10-drone long runs, and prepared-airborne mission Start/Cancel endurance remain available for later explicit validation but are **not current blockers**.
- **AUTHORITY GATE:** only `core-single` and `core-single-wait` remain live. `core-grouped-multi`, `core-separate`, `core-swarm-leader`, `core-wave`, `core-payload` remain OFF. At this historical S12 checkpoint the SWARM follower operator-takeover policy had not yet been ratified and S09-F was still blocked; the newer S09 checkpoints above supersede both points.
- **CLEANUP:** overnight Core/SITL test tasks were stopped after evidence completion.
- **EXACT NEXT STEP:** S12 current operational gate is complete. The next real gate is **V3-H02 Actual FC / Airframe Bench**, which requires actual hardware. When the FC is ready, open `docs/V3_FULL_PRODUCTION_VALIDATION_PLAN.md` and begin **STEP 1 — V3-H02-A Actual FC Base Validation** using only `core-single` / `core-single-wait`; do not flip S09 during the initial hardware baseline. After H02-A passes, promote S09 scopes one at a time according to that validation plan. Do not start V3-R01 until the required hardware/scope gates pass. SQLite contention can be reviewed separately without reopening the endurance gate unless it reproduces or affects operation.

## Previous Checkpoint — V1 PHASE 3 DONE (Telemetry Store + gRPC lifecycle) / FULL FRONTEND 1031 GREEN (2026-08-28)
- **TRACK:** V1 `PHASE_3_TO_10_CONTINUOUS_EXECUTION_PLAN` — **PHASE 3 COMPLETE. PHASE 4 NOT STARTED (held at user request).**
- **PHASE 3 SCOPE (unchanged):** `TelemetryStore` is a frontend **presentation read model only**; flight/business logic still reads raw `_last_telem`. Migrated presentation readers: Fleet card, Selected Drone card, Field Tablet, Map3D. `TelemetryRenderGate` coalesces continuous telemetry to a ~0.10 s minimum render interval; discrete/operator-visible changes render immediately. Map2D target/waypoint coupling deliberately stays on raw telemetry this phase. Explicitly **not** migrated to the Store: `_fleet_positions`, `_collision_positions`, waypoint progression / `_wp_advance*` / `_on_target_reached`, GOTO logic, servo sync/prime, `_auto_guided_after_land`, `_last_alt`, `_home_pos`, failsafe/preflight/takeoff decisions, mission authority.
- **LIFECYCLE REPAIR (root cause of the full-suite native crash):** the telemetry/event stream `QThread`s had a `run()`/`stop()` race — `stop()` no-op'd when `_call` was not yet assigned, so a worker could open the stream *after* stop and block inside the iterator; `closeEvent` then closed the gRPC channel under the live cygrpc iterator → `0xC0000005` access violation (`exit 3221225477`) at ~28 % of the suite (`test_map3d`). Fix: `core/grpc_client.py` `_StreamThread` base serializes `run()`/`stop()` under one lock (stop wins ⇒ stream never opened; run wins ⇒ call is cancelled), idempotent `stop()`, and a `shutdown()` join; `app.py closeEvent` disconnects only real bound signals (`hasattr(sig,"disconnect")` — `TelemetryThread.event` is `QObject.event`, not a signal), joins **both** workers, and closes the owned channel **only** once both are confirmed terminated.
- **TEST-ISOLATION REPAIR:** fixing the crash let the whole suite run for the first time, exposing a pre-existing hygiene bug it had masked — some GUI tests build a `GroundStation` and never `close()` it, leaving a 5 s `_ping_timer` → `PingWorker` `QThread` that runs real `subprocess.run(['ping',...])`; caught by `test_launcher_safety`'s process-wide `subprocess.run` mock, this failed different tests on different runs (run 1: launcher; run 2: mission_shadow/servo/wave×2/waypoint_separate; **every failing test passes in isolation**). Fix: new `tests/conftest.py` autouse teardown closes any leaked `GroundStation` so its timers/stream threads stop before the next test. **Test infrastructure only — no product/command/authority/safety change.**
- **VERIFICATION (this checkpoint, all run locally):** full frontend `python -m pytest -q tests` = **1031 passed / 0 failed** (deterministic; re-run green). Targeted: `test_grpc_lifecycle` **8/8** (incl. 50× GroundStation create/close stress, both stream kinds, stop-before-open, stop-while-blocked, idempotent stop, channel-closed-only-after-join, no-close-while-worker-stuck), `test_map3d` **53/53**, Phase 3 telemetry suite (`telemetry_store`+`telemetry_store_integration`+`health_monitor`+`map_presenter`+`map3d`+`ui_selection`) **132/132**. Backend `go test ./...` PASS all packages; `go vet ./...` PASS (Go untouched this phase).
- **FILES CHANGED (Phase 3 lifecycle work):** `frontend/swarmgod_gui/core/grpc_client.py`, `frontend/swarmgod_gui/app.py` (`closeEvent` only); new `frontend/tests/test_grpc_lifecycle.py`, `frontend/tests/conftest.py`. **No Go source changed; `DroneGod` legacy untouched.**
- **NOT CHANGED / STILL TRUE:** no Go flight-authority change; mission ownership / no-dual-authority / failsafe / safety semantics untouched. **F9B remains PENDING actual FC. F10 remains LOCKED.** No hardware/real-flight readiness is implied by these software tests.
- **EXACT NEXT STEP:** **STOP. Do not start Phase 4** (Command Gateway) until the user explicitly authorizes it.

## Previous Checkpoint — LEGACY PARITY BASELINE PRESERVED / F9A DONE / F9B ACTUAL-FC PENDING (2026-08-27)
- **CURRENT TRACK:** `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`
- **CURRENT F-PHASE:** **F9A — Hardware-Bench Preparation ✅ DONE. F9B — Actual FC/Airframe Bench = PENDING actual FC.** F8 remains green for the guarded single-drone GROUPED + WAIT scope.
- **AUTHORITY SCOPE:** exact tokens: `core-single` = one-participant GROUPED waypoint GOTO; `core-single-wait` = same + Core-owned WAIT/HOLD. They are allowed in SITL; HIL is allowed only behind the separate props-removed bench confirmation gate documented for F9B; production remains locked out. Multi-drone GROUPED, SEPARATE, SWARM_LEADER mission authority, WAVE, payload action, and `rtl_after` remain deferred/blocked.
- **LEGACY PARITY BASELINE:** original `DroneGod` is now an explicit read-only behavioral reference. Direct hash comparison found frontend Python **59 identical / 3 different / 0 missing**, frontend tests **34 identical / 2 migration-specific different / 0 missing** (`test_mission_shadow.py` + WAVE no-overlap regression in `test_wave.py`), and Go flight-path baseline (`command/fleet/safety/swarm/mavlink/config`) **27 identical / 1 safety-hardened different / 0 missing**. See `docs/LEGACY_BEHAVIOR_PARITY.md`.
- **PER-PLAN OWNERSHIP REPAIR:** `SWARMGOD_MISSION_AUTHORITY` remains global configuration, but authoritative ownership is selected per mission plan. Exact eligible single-drone GROUPED plans use Core; unsupported legacy capabilities (multi-drone GROUPED formation geometry, SEPARATE, SWARM Leader Path, payload A/B, and WAIT under `core-single`) do **not** call authoritative `StartMission` and deliberately keep the proven Python executor until separately migrated. Before any legacy mission starts under a Core token, `GetMissionState` must confirm no active Core-authority run; active/unknown Core slot = fail-closed, preventing cross-owner overlap. WAVE is guarded centrally inside `_wave_execute()` because Cockpit and Field Tablet can both enter that legacy automation without normal Mission Start; both paths now require the same Core-slot-idle proof.
- **NO-DUAL-AUTHORITY:** when an *eligible* Start/Query confirms `authority_active=true`, Python suppresses waypoint GOTO, browser target callbacks cannot advance Python mission state, and ambiguous/lost eligible Start reply is fail-closed with Query recovery rather than Python fallback. Ineligible plans staying Python-owned are intentional ownership selection, not RPC-error fallback.
- **F6/F7 VERIFIED:** cockpit restart/query rebuilds the frozen Core plan/run without duplicate Start/GOTO; deterministic and live failure injection covers duplicate Start, stale/cancel boundaries, battery/link interruption during WAIT, disconnect/reconnect during transit/WAIT, link-loss `INTERRUPTED`, Core process kill/restart and no mission auto-resume.
- **F8 LIVE REPEAT VERIFIED:** `cmd/missionverify` completed **4/4** live SITL cycles on the same Core process: route + WAIT, disconnect/reconnect during transit and WAIT, duplicate Start, second-run Cancel, and cleanup confirmed landed/disarmed every cycle.
- **F8 STRESS/LEAK CHECK:** 500 alternating terminal mission cycles PASS with no active-run/late-command leak. Core idle sample after cycle 3 vs cycle 4: working set **21.65→21.67 MB**, private **53.37→53.61 MB**, threads **11→11**, handles **155→155** — no obvious monotonic process-growth signal in this bounded gate.
- **FAILSAFE HARDENING:** `command.Service.Goto` and `command.Service.Hold` now both reject when the fleet failsafe latch is active. Regression guards require the latch check before navigation/mode send, closing the WAIT-HOLD-vs-RTL race.
- **REGRESSION:** targeted `command + mission + api` PASS; final `go test ./...` **PASS all packages**; full frontend `python -m pytest tests -q` **976/976 PASS in 736.56s (12:16)**.
- **CORE CRASH / FC BLOCKER:** live SITL Core kill about 7s into a 250m mission proved restarted Core returns `IDLE/run_id=0`, but the FC continued the last GUIDED target and then hovered armed at 20m. Application no-resume is correct; onboard FC GCS/Core-link failsafe behavior is **not safe to assume**.
- **F9A TOOLING DONE:** added `cmd/benchprobe` + `internal/bench` timing metrics, guarded `cmd/benchack`, read-only `scripts/bench_param_audit.py`, version-aware `docs/f9_expected_params.json`, evidence merger `scripts/f9_evidence_report.py`, self-tests, test fixture, and dedicated no-authority F9A SITL Core launcher.
- **F9B HIL AUTHORITY PREPARED:** actual-FC bench can use profile `hil` only behind two exact keys: mission token (`core-single` / `core-single-wait`) plus `SWARMGOD_BENCH_CONFIRM=PROPS-REMOVED-BENCH`; `production` remains locked out. `scripts/run_f9b_hil_core.bat` additionally refuses to start without an explicit `SWARMGOD_HOME_LOC`. Gate regression PASS and full Go remains green.
- **F9A LIVE NON-FLIGHT VALIDATION:** telemetry recorder on SITL: **101 samples / 10.00 Hz**, interval p95 **101 ms**, max **107 ms**, source-age p95 **1 ms**, max **3 ms**, mission IDLE. Guarded disarmed mode ACK harness: STABILIZE→GUIDED **OUTCOME_ACCEPTED ~3.58 ms**, restore STABILIZE **OUTCOME_ACCEPTED ~5.33 ms**; no Arm/Takeoff/navigation RPC exists in the tool.
- **F9A PARAM CHARACTERIZATION:** current SITL snapshot uses `MAV_GCS_SYSID=255` + `ARMING_SKIPCHK=0`; audit correctly FAILS `FS_GCS_ENABLE=0` and `BATT_FS_CRT_ACT=0`. This failure is intentional evidence that good telemetry cannot override unsafe FC failsafe configuration.
- **F9A REGRESSION:** `bench_param_audit_selftest.py` PASS; `f9_evidence_report_selftest.py` PASS; `go test ./...` PASS all packages including benchack static no-flight-command guard and bench timing tests.
- **LEGACY PARITY REGRESSION:** stable-source full frontend baseline before independent audit repair was **993/993 PASS in 788.51s (13:08)**; full Go was PASS all packages.
- **INDEPENDENT AUDIT REPAIR (H1/M1):** Claude's full-system audit found no CRITICAL issue, one HIGH manual-navigation overlap surface, and one MEDIUM authority-profile fail-permissive default. ChatGPT/MCP independently cross-checked source and confirmed H1 for ad-hoc cockpit/tablet GOTO + RC movement + Quick HOLD semantics (while Cancel Navigation/HOLD ALL already had an abort path), and confirmed M1. Repair now blocks ad-hoc GOTO/RC movement while Core owns the drone, drops stale queued GOTO/RC callbacks, makes HOLD/STOP an explicit Core-mission takeover, serializes StartMission with manual navigation at the Go API boundary, rejects manual GOTO/RC/alt-change for Core-owned participants, and requires an explicit matching `SWARMGOD_PROFILE` before authority tokens can activate.
- **AUDIT-REPAIR TESTS:** targeted Go `./internal/api ./internal/mission ./internal/command` PASS; frontend `test_mission_shadow.py` **28/28 PASS** including manual GOTO/MOVE/HOLD takeover regressions; post-repair full `go test ./...` **PASS all packages**. Stable-source full frontend final regression is running as durable task `a537c9a7-be57-40b5-b6ad-2292bfceb6af`; do not mark H1/M1 fully closed until that task exits green.
- **EVIDENCE:** `docs/MISSION_FAILURE_INJECTION_F7.md`, `docs/MISSION_SITL_F8_GATE.md`, and `docs/HARDWARE_BENCH_F9_GATE.md` (now split F9A/F9B with exact commands).
- **EXACT NEXT STEP:** wait for durable frontend task `a537c9a7-be57-40b5-b6ad-2292bfceb6af`. If green, mark H1/M1 + MT1–MT3 closed and restore F6/F8 to VERIFIED PASS; then when the actual FC is available run **F9B only** under the team's physical bench-safety procedure. If the frontend task fails, reproduce/fix only the failing regression before any F9B work.
- **SAFE/RUNNABLE:** **YES for guarded SITL single-drone GROUPED + WAIT and F9A bench preparation. NOT READY for real flight until F9B passes.**

## Previous Checkpoint — ChatGPT+MCP Safety Takeover (2026-08-27)
- **CURRENT TRACK:** `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`
- **CURRENT F-PHASE:** **F4 pre-authority B0/B1 — compatibility + safety preemption verified; Stage-B GOTO authority NOT STARTED**
- **DONE:** F4 Stage A1 ✅ + **A2 ✅**; A2 Python shadow-submit remains non-authoritative. **B0 per-drone altitude compatibility ✅**: `MissionPlan.participant_altitudes` freezes `_last_alt/_alt_for` per participant end-to-end (Python proto → Go domain) with backward-compatible waypoint-alt fallback. **B1 safety wiring ✅**: Core Event Bus `ALARM battery|link` mirrors into `mission.Interrupt(...)` only; fleet manager remains sole owner of failsafe RTL.
- **CURRENT WIP:** no authority dispatch code. Multi-drone GROUPED target geometry remains unresolved for Go authority: Python dynamically translates each drone from the current group centroid, preserves formation offsets, and staggers GOTO 150 ms; Go shadow still judges the shared route centre. Therefore multi-drone GROUPED remains **BLOCKED for authority**.
- **FILES CHANGED:** `proto/swarmgod/v1/mission.proto`; regenerated `backend/gen/swarmgod/v1/mission.pb.go` + `frontend/swarmgod_gui/gen/swarmgod/v1/mission_pb2.py`; `backend/internal/mission/plan.go`, `plan_test.go`; `backend/internal/api/mission.go`, `mission_test.go`; `frontend/swarmgod_gui/core/mission_shadow.py`; `frontend/tests/test_mission_shadow.py`. Concurrent B1 WIP observed and preserved: `backend/internal/api/server.go` + safety-observer additions in `mission.go`/tests. Progress file updated only in `DroneGod_AI`.
- **TESTS RUN:** A2 pre-check `test_mission_shadow.py + test_waypoint_failsafe.py` **18 PASS**; after altitude patch `go test ./internal/mission ./internal/api` PASS; `test_mission_shadow.py` **7 PASS**; combined `test_mission_shadow.py + test_waypoint_failsafe.py` **19 PASS**; full `go test ./...` PASS. Existing live A2 SITL evidence: Core shadow advanced `WP0 → WP1 → COMPLETED`, revision `3 → 4 → 5`, while legacy Python alone sent GOTO; cleanup LAND succeeded.
- **TEST RESULTS:** per-drone GROUPED altitudes `D1=18.5`, `D2=27.0` survive proto conversion and resolve independently through `MissionPlan.AltitudeFor`; legacy/shadow plan missing map falls back to waypoint altitude. `TestMissionHandlersIssueNoCommand` still passes: mission API/observer has no GOTO/HOLD/TAKEOFF/RTL/Idempotent command call. Safety ALARM tests pass for participant battery/link and ignore WARN/non-participant/other categories.
- **DECISIONS:** B0 altitude contract uses additive `map<uint32,double> participant_altitudes` instead of overloading shared waypoint `alt`. Stage-B first eligibility scope remains **single-drone GROUPED only**, **no WAIT, no payload action, no WAVE, no rtl_after**. Multi-drone authority is deferred until Go ports the formation-target resolver/arrival semantics. B1 safety observer is state-only and must never send or duplicate failsafe flight commands.
- **RISKS:** no protected mode is crash-tolerant yet because Go still sends no mission GOTO. Multi-drone shared-centre arrival is not equivalent to Python offset targets. Real flight/hardware remains prohibited until independent Codex/Claude Opus review covers F4 authority, F5 WAIT, F6 reconnect, failsafe/stale-run behavior, plus SITL evidence.
- **AUTHORITY BEFORE / AFTER:** unchanged — **Python waypoint executor YES / Go mission flight authority NO**. Shadow submit + Core state observation only. **No dual authority proven for current checkpoint** by frontend legacy-GOTO regression plus Go static no-command test.
- **EXACT NEXT STEP:** **B2 authority-eligibility/no-dual-authority guard only** — add pure validation that Go authority can be eligible only for one-participant `MISSION_MODE_GROUPED` with zero WAIT/action/WAVE/rtl_after, and explicit tests that ineligible plans cannot enter authority mode. Do **not** add GOTO dispatch in the same sub-step. After B2 green, stop and review before any command-sink/authority cutover.
- **NEXT FILE/METHOD/TEST:** `backend/internal/mission/plan.go` (new pure `AuthorityEligibility`/validation helper or equivalent) + `plan_test.go`; if an authority flag lives at API boundary, inspect `backend/internal/api/mission.go` but keep it command-free. Tests: eligible single GROUPED accepted; 2 participants / SEPARATE / SWARM_LEADER / WAIT / payload action / rtl_after rejected; existing `TestMissionHandlersIssueNoCommand` remains green.
- **SAFE/RUNNABLE:** **YES for A2+B0+B1 shadow/pre-authority checkpoint. NOT READY for Stage-B GOTO authority, hardware, or real flight.**

## Current Status
- **Active Roadmap:** `docs/V3_MASTER_ROADMAP.md`
- **Current Stage:** **V3-H02 — ACTUAL FC BENCH IN PROGRESS.** 5.1–5.3 are evidenced PASS; operator-visible HIL reached GUIDED/ARM/DISARM; 5.4–5.9 remain pending.
- **Completed V3 checkpoints:** V3-S01–S08 ✅; V3-S09 **PRE-FLIP IMPLEMENTATION + FRESH INDEPENDENT REVIEW PASS / AUTHORITY NOT FLIPPED**; V3-S10 ✅ Persistence + Restartability; V3-S11 ✅ Failure Injection Expansion; V3-S12 current operational gate ✅; V3-H01 ✅.
- **S09 administrative note:** the separate no-flip PRE-FLIP freeze is still intended, but no current document proves the freeze action itself was completed. Keep all deferred S09 live tokens OFF.
- **S12 evidence:** 6h idle-connected PASS (216,002 telemetry, 723 RPC, zero RPC/stream errors, zero UI stalls), 15m reconnect PASS (15 reconnects), and 15m client lifecycle PASS (75 create/snapshot/close cycles).
- **Follow-up:** one transient `SQLITE_BUSY` registry-upsert warning occurred during the 6h run without service interruption; track separately as non-blocking SQLite contention follow-up.
- **Last updated:** 2026-09-06 (H02 actual-FC progress + operator HIL/auth + roadmap synchronization).
- **Authority now:** only `core-single` and `core-single-wait` may be live; no S09 authority flip occurred.
- **Hardware/release boundary:** H02 is active now. V3-R01 remains locked until H02 and the required later scope/hardware/controlled-flight evidence pass an explicit release review.

## Current Goal
Re-establish fresh HIL telemetry, capture post-token SYSTEM TEST evidence, then complete H02 5.4–5.9 in order. Record the S09 PRE-FLIP administrative freeze separately without widening authority. Preserve deferred 12h/24h and multi-drone endurance campaigns for later full-system validation; do not claim controlled real-flight readiness before H02 and the required release ladder pass.

## Active Roadmap

`docs/V3_MASTER_ROADMAP.md`

V1 Phase 3 Telemetry Store, telemetry reader migration, render coalescing, presenter เพิ่ม,
system-wide Command Gateway/dedup, persistence และ cosmetic cleanup =
**DEFERRED UNTIL AFTER SAFETY FAST-TRACK**

## Flight Authority Matrix (V2 §10)
| Subsystem | Current Authority | Target Authority | Cutover Status |
|---|---|---|---|
| Plan build/edit | Python | Python | คงเดิม (UI) |
| Mission Start/Cancel | Python operator intent → Go RPC | Go (run_id) | **DONE for guarded scope**; Core owns run identity/idempotency |
| Mission state query | Go `GetMissionState` → Python display cache | Go GetMissionState | **DONE (F6)**; restart/rebind reconstructs frozen plan/run |
| Waypoint progression | **Go Mission Engine** when exact SITL authority token is enabled | Go Mission Engine | **DONE for single-drone GROUPED**; other mission modes deferred |
| **Arrival detection** | **Go telemetry + arrival radius** in guarded authority scope | Go (telemetry+radius) | **DONE for guarded scope**; browser callback cannot advance Core-owned run |
| WAIT / HOLD | **Go Mission Engine + command.Service** in `core-single-wait` | Go state | **DONE (F5)** for guarded single-drone WAIT |
| Payload action A/B | Python | Go | DEFERRED / F6B conditional |
| GOTO/HOLD command | **Go API adapter → command.Service** in guarded authority scope | Mission Engine intent → command.Service | claim-once; GOTO/HOLD both yield to failsafe latch |
| Failsafe (battery/link) | **Go Core** ✅ | Go Core | fleet remains sole flight-action owner; mission state INTERRUPTED |
| Mission state storage | **Go in-memory (V2)** | Go in-memory (V2) | DONE for UI-restart goal; Core restart intentionally starts IDLE |
| run identity | **Core-generated run_id** | Core-generated run_id | DONE; stale/duplicate Start/Cancel coverage green |

## Protected Real-Flight Modes (crash-tolerant?)
- Waypoint (single-drone GROUPED): **Software/UI-crash tolerant in SITL ✅; NOT real-flight cleared until F9B.**
- WAIT (same guarded scope): **Software/UI-crash tolerant in SITL ✅; NOT real-flight cleared until F9B.**
- Multi-drone GROUPED: **NO — geometry/arrival authority deferred.**
- Swarm formation mission progression: **partial / not protected by this mission cutover.**
- WAVE: **NO** (DEFER — V2 §3.6 / F6B conditional).
- Payload automation: **NO**.

> Core-owned guarded waypoint+WAIT now survives cockpit loss/restart, but Core-process loss still depends on onboard FC failsafe behavior. Therefore crash tolerance is proven only for the UI boundary; real-flight clearance remains blocked on F9B actual-FC verification.

## Historical Fast-Track F4 Checkpoint (Stage-A snapshot; superseded by Latest Checkpoint)

### CURRENT F-PHASE
- **F4 — Waypoint Execution Authority → Go.** RISK สูงสุด. ทำเป็น sub-stage:
  - **Stage A part 1 — Go observation pipeline → ✅ DONE / SAFE / RUNNABLE / inert**
  - **Stage A part 2 — Python shadow-submit (StartMission/Cancel best-effort) → IN PROGRESS (ChatGPT+MCP takeover)**
  - Stage B — authority flag + Python หยุดส่ง GOTO → **NOT STARTED** (RISK สูงสุด)
- **A2 guard:** shadow only; Python command sequence/authority must remain unchanged.
- **Stage B blocker discovered:** `MissionWaypoint.alt` เป็นค่าเดียวต่อ shared waypoint แต่ Python GROUPED ปัจจุบันเลือก altitude รายโดรน (`_last_alt`/`_alt_for`) ได้ — ต้องกำหนด altitude contract/compatibility ก่อน Go GOTO authority; ห้ามเดาค่าแล้ว cutover.
- **Last Safe Runnable Checkpoint = F4 Stage A part 1** (Go build + `go test ./...` green; เป็น additive/inert)

### DONE (Stage A part 1)
- เพิ่ม `mission.Engine.Active()` (cheap check)
- `internal/api/mission.go`: `runMissionObserver(ctx)` ticker 5Hz (200ms) → `observeMissionTick` →
  `observeFrom([]*pb.Telemetry)` = feed `Observe(id,lat,lon,altRel)` + `Poll()` ให้ shadow engine
- start observer goroutine ใน `Server.Serve` (ใช้ lifecycle ctx เดียวกับ graceful stop)
- **inert-in-production:** observer ทำงานเฉพาะเมื่อ `mission.Active()` = true; ปัจจุบันไม่มีใคร StartMission
  จาก Python → observer ไม่ทำอะไรเลยใน runtime จริง = **0 behavior change, 0 command**

### FILES CHANGED (Stage A part 1)
- `backend/internal/mission/engine.go` (+`Active()`)
- `backend/internal/api/mission.go` (+observer: `runMissionObserver`/`observeMissionTick`/`observeFrom`)
- `backend/internal/api/server.go` (+`go s.runMissionObserver(ctx)` ใน Serve)
- `backend/internal/api/mission_test.go` (+3 tests)
- **ไม่แตะ Python / proto / command path**

### TESTS RUN
- `go test ./internal/api/ ./internal/mission/` → ผ่าน (api 18, mission 28)
- `go test ./...` → ผ่านทุก package; `go vet` clean; `go build ./...` OK
- ครอบ: telemetry-driven GROUPED progression (far→WP0 รอครบ→advance→COMPLETED),
  inert-when-idle (nil-safe ไม่ panic), nil-position safe

### TEST RESULTS
- observation pipeline พิสูจน์แล้วว่า shadow engine ก้าวหน้าจาก telemetry จริงได้ (เมื่อมี run)
- ยังไม่ส่ง command (โครงสร้างเดิม `TestMissionHandlersIssueNoCommand` ยังผ่าน)

### DECISIONS
- **F4A-D1:** แยก `observeFrom(telems)` ออกจาก `s.mgr.Snapshot()` เพื่อ unit-test ได้โดยไม่ต้องสร้าง fleet.Manager จริง
- **F4A-D2:** observer เป็น read-only จาก `fleet.Manager.Snapshot()` (ไม่ hook telemetry ingest path — น้อย invasive)
- **F4A-D3:** ticker 200ms (5Hz) — พอสำหรับ shadow tracking, arrival radius 3m; ไม่กระทบ flight เพราะไม่ส่ง command
- **F4A-D4:** ทำ Go pipeline ก่อน Python submit แยกเป็น 2 ขั้น — keep Go milestone isolated/reviewable ก่อนแตะ app.py (WIP)

### RISKS / OPEN
- pipeline ยัง **ไม่ถูก trigger จริง** จนกว่า Stage A part 2 (Python เรียก StartMission ตอน `_wp_begin_execute`)
- Stage A part 2 จะแตะ `app.py` (WIP-heavy) → ต้องเป็น best-effort/daemon-thread/swallow-exception/มี flag ปิดได้
  และ **ห้ามเปลี่ยน behavior เดิม** (pure shadow submit, ไม่อ่าน response ไปสั่งอะไร)
- Stage B (authority) ยังห่าง — ห้ามทำจน Stage A เทียบ shadow ใน SITL ผ่าน

### EXACT NEXT STEP (F4 Stage A part 2 — Python shadow-submit)
1. เพิ่ม thin client method ใน `frontend/swarmgod_gui/core/grpc_client.py`:
   `start_mission(plan_proto, operation_id)`, `cancel_mission(run_id, request_id)`, `get_mission_state()`
2. ใน `app.py`: หลัง `_wp_begin_execute` เริ่มจริง → สร้าง `pb.MissionPlan` จาก route ปัจจุบัน แล้ว
   **fire-and-forget** `start_mission(...)` ใน daemon thread (swallow exception, มี flag `SWARMGOD_MISSION_SHADOW`)
   - ใช้ `_flight_run_id`/operation id เป็น operation_id เพื่อ idempotency
   - ที่ `_cancel_navigation`/`_abort_waypoint_execution`/`_wp_failsafe_interrupt` → fire `cancel_mission` (best-effort)
3. **ห้าม** อ่าน mission state กลับมาสั่งอะไร (ยัง shadow); Python ยัง execute เต็มเหมือนเดิม
4. เพิ่ม frontend test: `_wp_begin_execute` เรียก start_mission (mock client) โดยไม่กระทบ command sequence เดิม
5. SITL: start waypoint → ดูว่า Go `GetMissionState` ก้าวหน้าตาม Python (เทียบ shadow) → ปิด Stage A

### NEXT FILE TO OPEN (Stage A part 2)
- `frontend/swarmgod_gui/core/grpc_client.py` (เพิ่ม mission client methods)
- `frontend/swarmgod_gui/app.py` `_wp_begin_execute` (~7427) + cancel/abort/failsafe paths (hook shadow submit)
- แปลง route → `mission_pb2.MissionPlan` (มี `MISSION_MODE_*`, `wait_seconds`)

### NEXT METHOD TO MODIFY (Stage A part 2)
- Python `_wp_begin_execute`: หลัง set executor state → fire shadow `start_mission` (daemon, guarded)
- Python cancel/abort/failsafe: fire shadow `cancel_mission`

### NEXT TEST TO WRITE (Stage A part 2)
- frontend: mock CoreClient — ยืนยัน `_wp_begin_execute` เรียก `start_mission` 1 ครั้ง และ command GOTO เดิมไม่เปลี่ยน
- frontend: shadow submit ล้มเหลว (client raise) → ไม่กระทบ execution เดิม (swallow)

## Fast-Track F3 Checkpoint

### CURRENT F-PHASE
- **F3 — Mission RPC + Run ID + Start/Cancel/Query Guard → ✅ DONE / SAFE / RUNNABLE**
- **Last Safe Runnable Checkpoint = F3** (Go build + `go test ./...` green; frontend imports OK)

### DONE
- **Proto toolchain แก้แล้ว:** ไม่มี `protoc` แยก แต่ `python -m grpc_tools.protoc` (libprotoc 35.1 = version เดิม)
  regen ได้ทั้ง Go + Python — regen แล้ว 5/6 Go stubs identical, ต่างแค่ comment stale ใน service_grpc (drift เดิม)
  → **วิธี regen: `python -m grpc_tools.protoc -I proto --go_out=... --go-grpc_out=... --python_out=... --grpc_python_out=... proto/swarmgod/v1/*.proto`**
- สร้าง `proto/swarmgod/v1/mission.proto` — run-based mission: `MissionPlan`/`MissionRoute`/`MissionWaypoint`
  (มี `wait_seconds`), enum `MissionMode`/`MissionWpAction`/`MissionRunState`, `Start/Cancel/GetMissionState` messages
- เพิ่ม 3 RPC ใน `service.proto`: `StartMission`/`CancelMission`/`GetMissionState` (+ import mission.proto)
- regen Go + Python stubs (additive, ไม่ churn command/common/swarm/telemetry)
- engine: เพิ่ม `StartOp(plan, operationID)` + `Run.OperationID` — idempotency ตาม operation_id (contract B4)
- wire `mission.Engine` เข้า `internal/api/server.go` (field + `New`) + handlers ใน `internal/api/mission.go`
  (proto↔domain conversion; **handlers ไม่แตะ `s.cmd`/`s.swarm` เลย = ไม่ส่ง command**)

### FILES CHANGED (F3)
- `proto/swarmgod/v1/mission.proto` (ใหม่), `proto/swarmgod/v1/service.proto` (+import +3 RPC)
- `backend/gen/swarmgod/v1/mission.pb.go` (ใหม่), `service.pb.go` + `service_grpc.pb.go` (regen)
- `frontend/swarmgod_gui/gen/swarmgod/v1/mission_pb2*.py` (ใหม่), `service_pb2*.py` (regen) — **generated เท่านั้น, ไม่แตะ Python source**
- `backend/internal/mission/engine.go` (+`StartOp`/`OperationID`), `engine_test.go` (+op-id test)
- `backend/internal/api/server.go` (+`mission` field, init), `mission.go` (ใหม่, handlers), `mission_test.go` (ใหม่)

### TESTS RUN
- `go test ./internal/mission/ ./internal/api/` → **ผ่าน** (mission 28, api 15 รวม mission handlers 8)
- `go test ./...` → **ผ่านทุก package**; `go vet` clean; `go build ./...` OK
- Python smoke: `import swarmgod_gui.app` OK, `grpc_client`/`mission_pb2`/service stubs import OK (regen ไม่ทำ frontend พัง)
- ไม่รัน full Qt suite (F3 ไม่แตะ Python source; generated stubs additive)

### TEST RESULTS (F3 exit criteria ✅)
- duplicate Start (same operation_id) → run เดิม ✅
- duplicate/stale Cancel → no-op ไม่แตะ run ✅ (+ idempotent)
- reconnect Query → คืน run_id/state/plan/participants ✅
- **no flight command** → `TestMissionHandlersIssueNoCommand` ✅ (authority ยัง Python)

### DECISIONS
- **F3-D1:** ใช้ RPC ใหม่ (`StartMission`/`CancelMission`/`GetMissionState`) แทน extend `MissionControl`/`UploadMission` เดิม
  (per-drone, ไม่มี run identity) — ชัดกว่า, ไม่แตะ roadmap RPC เดิม
- **F3-D2:** `mission.proto` แยกไฟล์ (ไม่ยัดใน command.proto) — boundary ชัด, review ง่าย
- **F3-D3:** idempotency ของ Start ผูก `operation_id` (retry/reconnect) + fallback `plan_id`; ต่างจาก command `Idempotent` 60s window
- **F3-D4:** F3 **ยังไม่ wire telemetry→`Observe`** และ Python ยังไม่เรียก RPC ใหม่ — เจตนา (authority ยัง Python จน F4)
- **F3-D5:** regen ใช้ `grpc_tools.protoc` (bundled protoc) เพราะไม่มี `protoc` แยก — บันทึกไว้ให้คนต่อไป

### RISKS / OPEN (ยกไป F4)
- Go server มี mission engine แต่ **ยังไม่ถูกป้อน telemetry** → Snapshot จะค้างที่ RUNNING index 0 จนกว่า F4 wire `Observe`
- F4 = cutover จริง: ต้องมี authority flag (dev/SITL), Python หยุดส่ง GOTO, ห้าม dual authority (STOP condition)
- arrival detection ยังอยู่ browser JS ในเส้นทางจริง — F4 ต้องสลับมาใช้ `Observe` (shadow เทียบก่อน)

### EXACT NEXT STEP (F4 — ยังไม่เริ่ม, รอไฟเขียว, RISK สูงมาก)
1. อ่าน contract PART B (B7 preemption, B8 cutover) + V2 F4 section (safety architecture)
2. **Stage A (shadow-in-server):** wire fleet telemetry → `s.mission.Observe(id,lat,lon,alt)` + `Poll()` loop
   → เทียบ Go shadow state กับ Python executor ใน SITL (ยังไม่ส่ง command)
3. **Stage B (authority flag):** เพิ่ม flag เปิด Go waypoint authority เฉพาะ dev/SITL; Mission Engine ส่ง GOTO
   **ผ่าน `command.Service`/safety เท่านั้น** (ห้าม bypass); Python executor หยุดส่ง GOTO เมื่อ flag on
4. Scope แรก = GROUPED single route, ไม่มี WAVE/payload; SWARM ให้ formation loop คุม follower เหมือนเดิม
5. tests: kill Python → Go advance ต่อใน SITL; Cancel หยุด; stale Python callback ไม่ส่ง GOTO; safety reject → FAILED
6. **STOP ทันทีถ้า:** Python+Go ส่ง mission command พร้อมกัน / bypass safety / failsafe เปลี่ยนโดยไม่มี test

### NEXT FILE TO OPEN (F4)
- `backend/internal/api/server.go` (telemetry subscription/aggregator) เพื่อ feed `Observe`
- `backend/internal/mission/engine.go` (เพิ่ม authority-mode + command-issue hook ผ่าน interface ไป command.Service)
- `frontend/swarmgod_gui/app.py` `_wp_advance`/`_wp_begin_execute` (gate ให้หยุดส่ง GOTO เมื่อ Go authority on)

### NEXT METHOD TO MODIFY (F4)
- Go: `mission.Engine` เพิ่ม command-sink interface (inject จาก server → command.Service) — **shadow ยังเป็น default**
- Python: gate ใน `_wp_advance`/`_wp_advance_one` (เช็ค authority flag ก่อน `_goto_one`)

### NEXT TEST TO WRITE (F4)
- Go: authority-mode engine ส่ง GOTO ผ่าน injected command-sink (mock) ตาม arrival; shadow-mode ไม่ส่ง
- SITL/integration: kill Python → Go advance; no dual GOTO

## Fast-Track F2 Checkpoint

### CURRENT F-PHASE
- **F2 — Go Mission Engine Skeleton (SHADOW ONLY) → ✅ DONE / SAFE / RUNNABLE**
- **Last Safe Runnable Checkpoint = F2** (F0 ก่อนหน้ายังใช้ได้; F2 เพิ่ม package ใหม่ ไม่แตะ code เดิม)

### DONE
- สร้าง package `backend/internal/mission/` (shadow model ตาม contract PART B):
  - `plan.go` — `MissionPlan`/`Route`/`Waypoint`/`Mode`(GROUPED/SEPARATE/SWARM_LEADER)/`Action` + `Validate()`
  - `state.go` — `State` machine (IDLE→VALIDATING→READY→RUNNING↔WAITING→COMPLETED / CANCELLED / INTERRUPTED / FAILED) + `Owner` + `Transition`
  - `engine.go` — `Engine` shadow: `Start`(idempotent+reject 2nd), `Observe`(arrival จาก telemetry+radius),
    `Poll`(WAIT deadline), `Cancel`(stale-safe), `Interrupt`(failsafe fail-closed), `Snapshot`(GetMissionState shadow)
  - `util.go` — deterministic helpers
- **Arrival detection ย้ายเข้า Go แล้ว (shadow)**: `Observe` ใช้ `pkg/geo.HaversineM` + `arrivalRadius`
  (default 3.0 = ค่าเดิมจาก `map.html:TGT_REACH_M`) — แก้ cutover risk #1 ในระดับ shadow
- `run_id` = **Core-generated** (`Engine.nextRunID`) แทน Python `itertools.count`
- ครอบ characterization scenarios: GROUPED รอครบทุกลำ, SEPARATE advance อิสระ + per-drone WAIT,
  SWARM_LEADER เฉพาะ leader ขับ progression, WAIT hold/deadline, cancel stale-safe, failsafe fail-closed + no-auto-resume

### FILES CHANGED (F2)
- `backend/internal/mission/{plan,state,engine,util}.go` (ใหม่, source)
- `backend/internal/mission/{plan,state,engine}_test.go` (ใหม่, tests)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint)
- **ไม่แตะ frontend / proto / command path / app.py เลย** (git status frontend = WIP เดิม ไม่มีของ F2)

### TESTS RUN
- `cd backend && go test ./internal/mission/ -v` → **27 PASS** (0.6s)
- `go vet ./internal/mission/` → clean
- `go test ./...` → **ทุก package ผ่าน** (mission + fleet/swarm/command/api/safety/store/geo/...)

### TEST RESULTS
- **F2 SHADOW ONLY verified:** `TestNoFlightCommandImports` พิสูจน์ว่า package ไม่ import
  fleet/command/swarm/mavlink = ไม่มีทางส่ง flight command (F2 exit criterion)
- ไม่รัน frontend suite (F2 ไม่แตะ frontend); frontend baseline คงเดิม (965/181)

### DECISIONS
- **F2-D1:** engine เป็น pure shadow — inject `Clock` (เหมือน `_wp_clock`), WAIT ใช้ injected clock ไม่รอเวลาจริง
- **F2-D2:** payload action A/B modelled แบบ instantaneous ใน shadow (ไม่มี servo command) — F2 โฟกัส waypoint/WAIT progression; timing servo จริงเป็นเรื่อง F5/F6B
- **F2-D3:** mission-level `State` = WAITING เฉพาะเมื่อไม่มี scope ใด transiting (`hasTransiting`);
  SEPARATE ที่ลำหนึ่ง WAIT แต่ลำอื่นบิน = ยัง RUNNING (per-drone wait อยู่ใน `Snapshot.Waits`)
- **F2-D4:** SWARM_LEADER progression ขับด้วย `LeaderID` เท่านั้น — ไม่สร้าง formation authority ซ้ำ (followers อยู่ใต้ Go swarm manager)
- **F2-D5:** Start idempotency ชั่วคราวผูกกับ `PlanID` (operation_id จริงเป็นของ F3 RPC)

### RISKS / OPEN (ยกไป F3/F4)
- shadow ยังไม่ถูกป้อนด้วย telemetry จริงจาก fleet — F3 ต้อง wire observation source (ยังไม่ authority)
- GROUPED staggered GOTO timing ไม่ได้ modelled (shadow สนใจ sequence ไม่ใช่ 150ms stagger) — พอสำหรับ shadow
- proto ยังไม่มี Mission RPC/run_id/wait_seconds — F3 ต้อง extend proto + server handler
- "shadow agrees with characterization" ปัจจุบัน = encode Python-observable ลง Go tests; cross-run comparison จริงเกิดตอน F3/F4 wire

### EXACT NEXT STEP (F3 — ยังไม่เริ่ม, รอไฟเขียว)
1. อ่าน contract PART B (B4 Start / B5 Cancel / B6 Query) + F3 section ของ V2
2. ออกแบบ proto: เพิ่ม `StartMission`/`CancelMission`/`GetMissionState` (หรือ extend `MissionControl`)
   พร้อม `run_id`, `operation_id`(request_id), `wait_seconds` ใน Waypoint — **ยึด proto convention เดิม**
3. Wire `mission.Engine` เข้า Go server (`internal/api/server.go`) แบบ RPC boundary — **ยังไม่ให้ authority**:
   Python ยัง execute จริง; Go รับ Start/Cancel/Query + observe telemetry แบบ shadow
4. tests: duplicate Start, duplicate/stale Cancel, reconnect Query (contract B4/B5/B6 exit)
5. ปิด F3 checkpoint แล้วหยุดก่อน F4 (waypoint authority cutover)

### NEXT FILE TO OPEN (F3)
- `proto/swarmgod/v1/command.proto` (Waypoint + mission messages) และ `service.proto` (RPC)
- `backend/internal/api/server.go` (wire engine); อ้างอิง `internal/command/service.go` idempotency pattern

### NEXT METHOD TO MODIFY (F3)
- proto regen + server handler ใหม่ (StartMission/CancelMission/GetMissionState)
- **ห้ามแตะ Python executor / app.py ใน F3** — Python ยังเป็น authority จน F4

### NEXT TEST TO WRITE (F3)
- server-level: duplicate Start → run เดิม; stale Cancel → ไม่แตะ run ใหม่; Query หลัง reconnect คืน state ถูก

## Fast-Track F1 Checkpoint

### CURRENT TRACK
- `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`

### CURRENT F-PHASE
- **F1 — Mission Boundary Audit + Contract → ✅ DONE**
- checkpoint: **DONE / docs-only / 0 source flight-behavior change / F0 ยังเป็น last SAFE RUNNABLE checkpoint**
- **Deliverable ส่งแล้ว:** `docs/MISSION_CORE_CUTOVER_CONTRACT.md` (PART A current reality + PART B target contract)

### DONE
- ตรวจ Progress/Git status/diff อีกครั้ง; WIP เดิมยังอยู่และไม่ถูก cleanup/revert
- ยืนยัน F0 frontend **181 passed** + Go fleet/swarm/command/api passed
- กำหนด F1 scope: Python waypoint start/progression/arrival/WAIT/action/cancel,
  RTL/E-STOP/failsafe interrupts, stale guards, request-id/idempotency และ reconnect query
- audit source ปัจจุบันบางส่วนแล้ว:
  - `_wp_execute()` validate/confirm/freeze summary run แล้วผ่าน auto-takeoff gate ก่อน `_wp_begin_execute()`
  - `_wp_begin_execute()` ทำ Python เป็น executor authority และ `_wp_advance*()` ส่ง GOTO ผ่าน `_goto_one()`
  - arrival authority ปัจจุบันอยู่ใน browser map JS: ระยะราบ `<= 3.0m` ส่ง
    `target_reached` ผ่าน `MapBridge` เข้า `_on_target_reached()`; ไม่ตรวจ altitude
  - GROUPED รอ target IDs ครบ; SEPARATE advance อิสระ; Swarm active สั่ง GOTO เฉพาะ Head
  - arrival flow = ARRIVE → optional WAIT/HOLD → optional payload action → delayed advance
  - WAIT entry เก็บ `_wp_wait_generation`, process-local `_flight_run_id`, `_wave_generation`,
    deadline/callback; cancel/E-STOP/failsafe invalidate WAIT ก่อน progression
  - Core battery/link ALARM เป็น failsafe authority; Python `_wp_failsafe_interrupt()`
    หยุด local progression โดยไม่ส่ง HOLD/GOTO/RTL ทับ
  - Cancel Nav ปัจจุบัน: invalidate local executor → (ถ้า swarm) `swarm_stop` →
    `stop_all` → `hold`; RPC sequence ยังไม่มี mission run identity
  - `_flight_run_id` มาจาก process-local counterใน `flight_progress.py`, ใช้ presentation/
    callback guard บางส่วนเท่านั้น และไม่ถูกส่งไป Go
- audit gaps ที่ contract ต้องระบุชัด:
  - `UploadMission`/`MissionControl` ยัง unimplemented และไม่มี GetMissionState
  - duplicate Start/Cancel, stale Cancel และ UI reconnect semantics ยังไม่มี
  - no-WAIT `QTimer.singleShot(500, advance_cb)`, grouped staggered GOTO callbacks และ
    payload worker/finalizer ไม่มี Core-owned run guard; cancel→new mission ต้องถูก characterize
  - GOTO rejection ปัจจุบัน log result แต่ Python executorไม่มี deterministic FAILED transition
  - plan snapshot ใน `flight_progress` เป็น presentation snapshot ไม่ใช่ immutable executable MissionPlan

### CURRENT WIP
- **ไม่มี WIP ค้าง — F1 ปิดครบแล้ว (SAFE/RUNNABLE, docs-only)**
- Contract ครบทั้ง PART A (audit line-verified) + PART B (target contract)
- audit ถูก re-verify กับ source จริงในรอบนี้ (line numbers อัปเดตหลัง presenter extraction):
  `_wp_execute` 7346, `_wp_begin_execute` 7427, `_wp_advance` 7500, `_on_target_reached` 6347,
  `_wp_on_arrived` 7594, `_wp_wait_begin` 7609, `_wp_failsafe_interrupt` 6091, `_flight_run_start` 5023
- ยืนยัน arrival = browser JS `map.html:185 TGT_REACH_M=3.0` (ระยะราบ) → `target_reached`
- ยืนยัน `run_id` = `flight_progress.py` `itertools.count(1)` process-local, ไม่ส่งไป Go
- ยืนยัน `UploadMission`/`MissionControl` มีใน proto แต่ **ไม่มี Go handler**; ไม่มี wait_seconds/run_id
- ยืนยัน Go `command.Service` = validate→safety.Envelope→audit→MAVLink→ACK (path บังคับของ Mission Engine)

### FILES CHANGED (F1)
- `docs/MISSION_CORE_CUTOVER_CONTRACT.md` ✅ **สร้างแล้ว** (F1 deliverable)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint)
- **ไม่มี source/proto/test ถูกแก้ใน F1** — git status: source ที่ modified ทั้งหมด = WIP เดิมก่อน F1

### TESTS RUN
- F1 = docs/audit-only → ไม่รัน test เพิ่ม (ไม่แตะ source, ไม่จำเป็น + memory: เลี่ยง full Qt suite)
- baseline อ้างอิง F0 ที่บันทึกไว้: frontend targeted **181 passed**; Go fleet/swarm/command/api passed;
  full frontend **965 passed** (Phase 2C)

### TEST RESULTS
- F0 remains green (Last Safe Runnable Checkpoint); F1 ไม่มี regression risk (0 source change)

### DECISIONS
- **F1-D1:** contract แยก PART A (current reality) จาก PART B (target) ชัดเจน — คนต่อไปห้ามสับสน
- **F1-D2:** F1 ไม่ implement proto/server/engine — ทำแค่ contract
- **F1-D3:** arrival detection = cutover risk อันดับ 1; ต้องย้ายจาก browser JS เข้า Core **ก่อน/พร้อม F4**
  ไม่งั้น "Python crash → mission survives" เป็นไปไม่ได้ (บันทึกใน contract B8)
- **F1-D4:** Mission run identity ต้องเป็น Core-generated `run_id` แทน Python `itertools.count` +
  generation guards ทั้งหมด (contract B3)

### RISKS (ยกไป F2)
- GROUPED "รอครบทุกลำ" + staggered `singleShot` GOTO ต้องจำลองใน Go ให้ deterministic (F2 shadow ต้องพิสูจน์)
- arrival ผูก UI = ถ้าไม่ย้ายก่อน F4 จะ cutover ไม่ได้จริง
- proto mission RPC เดิมไม่พอ (ไม่มี run_id/request_id/wait_seconds) — F3 ต้อง extend

### EXACT NEXT STEP (F2 — ยังไม่เริ่ม, รอไฟเขียว)
1. อ่าน contract PART B ทั้งหมดก่อนเขียนโค้ด
2. สร้าง package `backend/internal/mission/` (skeleton): `engine.go`, `state.go`, `plan.go` +
   unit tests — **SHADOW ONLY: ห้ามส่ง GOTO/HOLD/TAKEOFF/SERVO/RTL, ห้าม import fleet command send path**
3. Engine รับ plan + telemetry observation → คำนวณ expected state/waypoint/next transition
4. เขียน shadow-comparison tests เทียบกับ characterization scenarios (GROUPED single route ก่อน)
5. ปิด F2 checkpoint แล้วหยุดก่อน F3 (Mission RPC + run id)

### NEXT FILE TO OPEN (F2)
- อ่าน `docs/MISSION_CORE_CUTOVER_CONTRACT.md` PART B (B2 state machine, B3 run id, B8 cutover)
- สร้างใหม่: `backend/internal/mission/state.go` (MissionState enum + transition table จาก B2)
- อ้างอิง pattern จาก `backend/internal/swarm/manager.go` (state loop) และ `command/service.go` (safety path)

### NEXT METHOD TO MODIFY
- ไม่มีการแก้ existing — F2 = สร้าง package ใหม่ (shadow). ห้ามแตะ app.py/command flow

### NEXT TEST TO WRITE (F2)
- `backend/internal/mission/state_test.go`: transition table (IDLE→RUNNING→WAITING→COMPLETED, cancel, interrupt)
- shadow test: GROUPED single-route arrival sequence ต้องตรงกับ Python characterization

## Fast-Track F0 Checkpoint

### CURRENT TRACK
- `REAL_FLIGHT_SAFETY_FAST_TRACK_V2`

### CURRENT F-PHASE
- **F0 — Freeze Fast-Track Baseline**
- checkpoint: **DONE / SAFE / RUNNABLE — source unchanged; frontend + Go gates passed**

### DONE
- อ่าน `REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md`, Progress MD, V1 plan และ Responsibility Map ครบ
- ตรวจ Git status/diff และ source ปัจจุบันจริง; ไม่ revert/cleanup WIP เดิม
- ยืนยัน Phase 2C checkpoint จาก source/tests/doc: MapPresenter อยู่ใน compatibility boundary,
  full frontend ล่าสุด **965 passed**
- ยืนยัน V1 Phase 3 Telemetry Store **ยังไม่เริ่มและไม่ DONE**
- initial command/idempotency inventory:
  - Python `CoreClient._rid()` สร้าง UUID ใหม่ทุกครั้งที่เรียก flight RPC method
  - Go `Idempotent` key = `(request_id, drone_id)`, retention 60 วินาที;
    empty ID ไม่ dedup; concurrent same-key รอผลเดิม แต่ timeout 10 วินาทีแล้วอาจ execute เอง
  - Arm/Disarm/Kill/Takeoff/Land/RTL/Hold/SetMode/Goto/ChangeAlt/Geofence และ
    Swarm RETURN ผ่าน `Idempotent`
  - Servo/RC/StopAll/EqualizeAlt/ParamSet/SetLeader/Swarm START-STOP/config ไม่มี
    request-id/dedup boundary แบบเดียวกัน
  - `UploadMission`/`MissionControl` มีใน proto/generated stubs แต่ไม่มี Server implementation;
    ยังไม่มี Mission Start/Cancel/Query/run_id contract ใน Go
- targeted frontend mission/Waypoint/WAIT/failsafe/swarm baseline:
  **181 passed in 272.61s**
- Go fleet/swarm/command/api baseline: **all 4 packages passed (cached)**

### CURRENT WIP
- ไม่มี source WIP ของ F0; checkpoint นี้ SAFE/RUNNABLE
- F1 Mission Boundary Audit + Contract **ยังไม่เริ่ม**

### FILES CHANGED
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` เท่านั้นสำหรับ F0
- ไม่มี source code เปลี่ยนใน F0

### TESTS RUN
- `python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
- `go test ./internal/fleet/... ./internal/swarm/... ./internal/command/... ./internal/api/...`

### TEST RESULTS
- last known safe checkpoint: frontend full **965 passed** (Phase 2C)
- F0 targeted frontend: **181 passed, 1 existing pytest-cache warning in 272.61s** ✅
- F0 Go: `fleet` ✅ / `swarm` ✅ / `command` ✅ / `api` ✅ (all cached)

### DECISIONS
- **F0-D1:** พัก V1 Phase 3 แบบ `NOT DONE / DEFERRED`; ไม่สร้าง TelemetryStore
- **F0-D2:** F0 เป็น read/test/document-only; ห้ามแก้ mission behavior หรือ authority
- **F0-D3:** ห้ามถือว่า request-id ปัจจุบันคือ system-wide หรือ mission idempotency;
  Mission Start/Cancel/run identity ยังไม่มี

### RISKS
- WIP ใหญ่ยังอยู่ใน Python Waypoint/WAIT/WAVE และ Go swarm/failsafe; ห้าม cleanup/revert
- Python ยังเป็น authority ของ Waypoint progression, WAIT callback และ WAVE sequencing;
  UI crash จึงยังทำให้ execution เหล่านี้หยุด/หาย
- idempotency retention มีขอบเขตเวลา และ in-flight wait timeout สามารถ fallback ไป execute `fn`;
  ต้องกำหนด Mission Start/Cancel semantics แยกใน F1/F3

### EXACT NEXT STEP
1. เปิด **F1 — Mission Boundary Audit + Contract** checkpoint (docs/tests only; no behavior change)
2. inventory Python waypoint start/progression/arrival/WAIT/action/cancel/RTL-E-STOP/failsafe/run guards
3. สร้าง `docs/MISSION_CORE_CUTOVER_CONTRACT.md` พร้อม state-transition table และ
   MissionPlan/run_id/Start/Cancel/Query/preemption/authority rules
4. หยุดก่อน F2; ห้ามสร้าง Go Mission Engine จน F1 contract ผ่าน review/checkpoint

### NEXT FILE TO OPEN
- `frontend/swarmgod_gui/app.py` เฉพาะ `_wp_execute` ถึง `_wp_wait_*`,
  `_cancel_navigation`, `_abort_waypoint_execution`, `_on_target_reached`

### NEXT METHOD TO MODIFY
- ไม่มีใน F1 audit; deliverable แรกเป็นเอกสาร contract และ characterization gaps เท่านั้น

### NEXT TEST TO WRITE
- F1 ต้อง inventory existing characterization ก่อน; test แรกเฉพาะ transition/guard ที่ยังไม่มี coverage

## Flight Authority Matrix

| Subsystem | Current Authority | Target Authority | Cutover Status |
|---|---|---|---|
| Waypoint progression | Python `GroundStation` | Go Mission Engine | NOT STARTED |
| WAIT timing/advance | Python Qt timer + generation guard | Go Mission Engine | NOT STARTED |
| WAVE sequencing | Python `GroundStation` | Deferred/conditional F6B | NOT STARTED |
| Swarm formation loop | Go `swarm.Manager` | Go | Existing; F0 targeted Go passed |
| Failsafe | Go fleet/safety + FC onboard layer | Go + FC | Existing; F0 targeted frontend/Go passed |
| Mission run identity/query | ไม่มี Core authority | Go Mission Engine | NOT STARTED |

## Protected Real-Flight Modes
- Waypoint: **NO**
- WAIT: **NO**
- Swarm formation: **NO — not yet Fast-Track verified**
- WAVE: **NO / DEFERRED unless first flight requires F6B**
- Payload automation: **NO / DEFERRED**

## Last Safe Runnable Checkpoint
- **V2 F0 DONE / SAFE / RUNNABLE**
- frontend mission-related targeted **181 passed**; Go fleet/swarm/command/api all passed
- Phase 2C full frontend checkpoint **965 passed** ยังคงเป็น full-suite reference ล่าสุด
- ไม่มี source/flight authority/RPC/command-order change ใน F0

## Phase 2C Map Presentation Adapter Checkpoint

### Current Phase
- **Phase 2C — Map Presentation Adapter / 3D drone-marker payload only**
- checkpoint: **DONE — targeted + adjacent + safety audit + full frontend gates passed**

### DONE
- อ่าน migration checkpoint ปัจจุบันและทบทวน Map sections ใน plan/responsibility map
- ตรวจ `git status`, `git diff --stat` และยืนยัน WIP ขนาดใหญ่เดิมยังคงอยู่โดยไม่ cleanup/revert
- inventory `_js3d`, `_push_map3d`, `_push_map3d_view`, `_push_map3d_targets`,
  `_wp_routes_3d` และ tests/assets contract ที่เกี่ยวข้อง
- เลือก extraction slice เดียว: pure formatting ของ drone-marker payload ใน `_push_map3d`
- ตัด `_push_map3d_view`, `_push_map3d_targets`, route/fence/GCS/selection/leader/swarm edges
  ออกจาก scope เพราะมี business/navigation state
- เพิ่ม `frontend/tests/test_map_presenter.py` characterization 3 tests
- targeted baseline (`test_map_presenter.py` + `test_map3d.py`) → **56 passed in 14.30s**
- สร้าง pure/no-Qt `controllers/map_presenter.py`; รับ read-only snapshots + color/mode adapters
- เปลี่ยน `_push_map3d()` เฉพาะ payload-building slice เป็น compatibility delegation;
  readiness gate, one-call dispatch และ `json.dumps` call-site ยังอยู่ที่เดิม
- เพิ่ม pure unit test 1 test; targeted หลัง extraction → **57 passed in 17.82s**
- adjacent Map/RTL/GPS/collision/Waypoint regression → **177 passed in 163.92s**
- forbidden dependency scan clean: adapter ไม่มี Qt/CoreClient/RPC/client/command/selection/
  target/waypoint ownership; คำ navigation/flight มีเฉพาะ boundary docstring
- ยืนยัน `_push_map3d_view`, `_push_map3d_targets`, `_wp_routes_3d` และ command call-sites
  ไม่ถูกแก้ใน Phase 2C; `git diff --check` ไม่มี whitespace error
- full frontend gate หลัง extraction → **965 passed in 888.05s (14:48)**

### CURRENT WIP
- ไม่มี source WIP ของ Phase 2C; หยุดที่ checkpoint ก่อนเริ่ม Phase 3 Telemetry Store shadow

### Files Changed
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` (checkpoint นี้)
- `frontend/tests/test_map_presenter.py` (ใหม่; characterization 3 tests)
- `frontend/swarmgod_gui/controllers/map_presenter.py` (ใหม่; pure/no-Qt)
- `frontend/swarmgod_gui/app.py` (minimal import/init + `_push_map3d` wrapper)

### Tests Run
- `python -m pytest tests/test_map_presenter.py tests/test_map3d.py -q`
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/map_presenter.py tests/test_map_presenter.py`
- `python -m pytest tests/test_tactical_map.py tests/test_rtl_and_mapui.py tests/test_map_gps.py tests/test_map_collision_ui.py tests/test_waypoint_ui.py -q`
- `python -m pytest -q`

### Test Results
- characterization baseline: **56 passed, 1 existing pytest-cache warning in 14.30s** ✅
- หลัง extraction + pure unit: **57 passed, 1 existing cache warning in 17.82s** ✅
- Python compile: OK ✅
- adjacent Map/navigation suites: **177 passed, 1 existing cache warning in 163.92s** ✅
- forbidden dependency/safety diff audit: clean ✅
- `git diff --check`: ไม่มี whitespace error; มีเพียง CRLF notice เดิม ✅
- full frontend: **965 passed, 1 existing pytest-cache warning in 888.05s (14:48)** ✅

### Decisions
- **D7:** Map adapter รอบแรกถือเฉพาะ pure `setDrones` payload formatting; readiness gate และ
  `_js3d` transport อยู่ใน `GroundStation` เพื่อคง scheduling/call order เดิม
- **D8:** presenter รับ telemetry/name/group/color/mode adapters ที่จำเป็นเท่านั้น และห้ามรับ
  `GroundStation self`, CoreClient, RPC, selected IDs, nav targets หรือ waypoint routes
- **D9:** ไม่ขยาย MapPresenter ไป `_push_map3d_view()`/targets/routes ใน checkpoint เดียวกัน;
  Phase 2C ปิดด้วย one-responsibility slice ตาม compatibility-first

### Risks
- `_push_map3d_view()` อ่าน selection/head/swarm/fence/waypoint route และ `_push_map3d_targets()`
  อ่าน navigation targets: ทั้งสองเมธอดอยู่นอก scope โดยเด็ดขาด
- mode/color formatting ต้องคง output เดิม byte-for-byte ใน payload เพื่อไม่ทำให้ asset contract เปลี่ยน
- timer เรียก `_push_map3d()` ถี่; adapter ต้องไม่เพิ่ม side effect หรือ JavaScript call count

### Exact Next Step
1. เปิด Phase 3 Step 3.1 checkpoint และ inventory ทุก read/write ของ `_last_telem`
2. เขียน characterization test ของ additive shadow write ก่อน source code
3. สร้าง pure `core/telemetry_store.py` เฉพาะ `update()`/`snapshot()` แล้วเขียนทั้ง state เดิม + shadow;
   UI, map, preflight, RTL, waypoint และ safety **ยังอ่าน `_last_telem` เดิมทั้งหมด**

### Next File To Open
- `docs/APP_PY_RESPONSIBILITY_MAP.md` หมวด telemetry แล้ว `GroundStation._on_telemetry()`

### Next Method To Modify
- ยังไม่มี; ต้อง inventory Phase 3 ก่อน Candidate คือ additive shadow write ใน `_on_telemetry()` เท่านั้น

### Next Test To Write
- characterization: หลัง `_on_telemetry(t)` shadow snapshot ตรงกับ `_last_telem` โดยไม่เปลี่ยน
  widget/command output และ legacy readers ยังชี้ state เดิม

## Phase 2B Fleet Presenter Checkpoint

### Current Phase
- **Phase 2B — Fleet Presenter / presentation-only**
- checkpoint: **DONE — targeted + adjacent + full frontend gates passed**

### DONE
- อ่าน `ARCHITECTURE_MIGRATION_PROGRESS.md`, `LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md`,
  `APP_PY_RESPONSIBILITY_MAP.md`, `WAYPOINT_WAIT_SWARM_MODE_TEST_PLAN.md` ครบทั้งไฟล์
- ตรวจ `git status`, `git diff --stat` และ diff ของ tracked WIP; ไม่ revert/cleanup ไฟล์เดิม
- ระบุ mixed-responsibility boundary ของ `_refresh_selection_ui` และ `_refresh_group_ui`
- เพิ่ม `frontend/tests/test_fleet_presenter.py` characterization 6 tests
- targeted baseline (`test_fleet_presenter.py` + `test_ui_selection.py` + `test_groups.py`)
  → **58 passed in 40.33s**
- สร้าง pure/no-Qt `controllers/fleet_presenter.py` และต่อ compatibility presentation slices
- targeted หลัง extraction + pure unit tests ใหม่ → **62 passed in 40.48s**
- adjacent telemetry/field/waypoint/summary regression → **121 passed in 87.52s**
- ตรวจ forbidden dependency: FleetPresenter ไม่มี Qt/CoreClient/RPC/flight command/
  `_target_ids`/`_target_mode` และไม่เขียน `_selected_ids`
- full frontend gate หลัง extraction → **961 passed in 804.97s**

### CURRENT WIP
- ไม่มี source WIP ของ Phase 2B; หยุดที่ checkpoint ก่อนประเมิน Map Presentation Adapter
- exact extraction scope:
  1. `_drone_name(did)` → compatibility wrapper ที่ส่งเฉพาะ custom/item/telemetry name เข้า presenter
  2. name normalization ภายใน `_on_drone_renamed()` → presenter (method เดิมยังถือ persistence/map/log)
  3. display-name fallback ใน `_on_telemetry()` → presenter; telemetry ingest/orchestration อยู่ที่เดิม
  4. fleet-count label call-sites → compatibility render helper; ไม่ย้าย registry lifecycle
  5. group card/chip text, tooltip, enabled state ใน `_refresh_group_ui()` → presenter;
     `_refresh_wave_group_choices()` อยู่ใน `GroundStation`
  6. card highlight/takeoff-panel selection text + selected summary formatting ใน
     `_refresh_selection_ui()` → presenter; Summary/Waypoint callbacks อยู่ใน `GroundStation`

### Files Changed
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md`
- `frontend/tests/test_fleet_presenter.py` (ใหม่; characterization 6 tests)
- `frontend/swarmgod_gui/controllers/fleet_presenter.py` (ใหม่)
- `frontend/swarmgod_gui/app.py` (minimal presenter wiring/wrappers)

### Tests Run
- `python -m pytest tests/test_fleet_presenter.py tests/test_ui_selection.py tests/test_groups.py -q`
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/fleet_presenter.py tests/test_fleet_presenter.py`
- `python -m pytest tests/test_field_phase3.py tests/test_waypoint_ui.py tests/test_preflight_summary_v2.py -q`
- `rg` forbidden dependency scan + `git diff --check`

### Test Results
- **58 passed, 1 existing pytest-cache warning in 40.33s** ✅
- หลัง extraction/pure tests เพิ่ม: **62 passed, 1 existing cache warning in 40.48s** ✅
- Python compile: OK ✅
- adjacent suites: **121 passed, 1 existing cache warning in 87.52s** ✅
- forbidden dependency scan: clean (พบคำต้องห้ามเฉพาะใน docstring ยืนยันขอบเขต) ✅
- `git diff --check`: ไม่มี whitespace error; มีเพียง CRLF notice เดิม ✅
- full frontend: **961 passed, 1 existing cache warning in 804.97s (13:24)** ✅

### Decisions
- **D4:** ห้ามย้าย `_refresh_selection_ui()` ทั้งก้อน เพราะท้ายเมธอดเรียก Waypoint render
  และเขียน Summary; ย้ายเฉพาะ presentation slice แล้วคง orchestration เดิม
- **D5:** ห้ามย้าย `_refresh_group_ui()` ทั้งก้อน เพราะเรียก WAVE group choices;
  presenter render เฉพาะ card/group-chip แล้ว wrapper เรียก WAVE ต่อเหมือนเดิม
- **D6:** FleetPresenter ต้องไม่มี Qt import, CoreClient, RPC, command, timer, thread,
  `GroundStation self`, `_target_ids`, `_target_mode` หรือ mutation ของ `_selected_ids`

### Risks
- `_selected_ids` เป็น high-risk shared state และกำหนด flight targets ทางอ้อม: presenter รับ snapshot/read-only iterable เท่านั้น
- `_refresh_selection_ui()` เชื่อม Summary + Waypoint: ต้อง lock call order เดิมด้วย app characterization test
- `_refresh_group_ui()` เชื่อม WAVE: ห้ามย้าย/เปลี่ยน `_refresh_wave_group_choices()`
- `_on_drone_renamed()` มี persistence/map side effects: extract ได้เฉพาะ normalize/format string

### Exact Next Step
1. เปิด checkpoint ใหม่เพื่อ **ประเมิน** Map Presentation Adapter ขนาดเล็กเท่านั้น
2. ระบุ render-only methods/call-sites และแยก navigation decision ออกก่อนเขียน test
3. ถ้าต้องรับ `GroundStation self`, route/target decision, GOTO/RPC หรือ flight state → STOP และลด boundary

### Next File To Open
- `docs/APP_PY_RESPONSIBILITY_MAP.md` หมวด Map (2D/3D) แล้ว `frontend/tests/test_map3d.py`

### Next Method To Modify
- ยังไม่มี; ต้องทำ Map scope inventory/characterization checkpoint ก่อน

### Next Test To Write
- ยังไม่เขียนจนกว่าจะระบุ Map render-only boundary; candidate แรกต้องไม่มี navigation decision

## What Has Been Completed
- อ่านครบ 3 เอกสารบังคับ (plan / WAYPOINT_WAIT test plan / CODEX_CHANGES)
- ตรวจ `git status` / `git diff --stat` (ดู section ล่าง) — ไม่ overwrite WIP
- **Phase 0 DONE:** verify baseline — WIP frontend 181 passed, Go ./... ผ่านทุก package
- **Phase 1 DONE:** สร้าง `docs/APP_PY_RESPONSIBILITY_MAP.md` — inventory ครบ:
  state ownership (11 กลุ่ม), async inventory (4 signals / 11 QTimer / ~40 threads / 31 singleShot),
  command authority (ทุก `self.client.*` call site), method groups ตาม subsystem + classification P/B/F/S,
  high-risk shared state, extraction candidates Phase 2
- **Observability DONE:** `health_monitor.py` + wiring observe-only ผ่าน full frontend suite **946 tests**
- **Phase 2A Summary Presenter DONE:**
  - เพิ่ม pure Python `controllers/summary_presenter.py` (ไม่มี Qt/gRPC/client)
  - ย้าย legacy-key mapping, COMMAND audit event, plan/timeline render และ snapshot ออกจาก `app.py`
  - คง `_summ_set`, `_summ_remove`, `_summ_event`, `_render_summary`, `_flight_snapshot`
    เป็น compatibility wrappers; `_summ_sync_waypoint_body` และ WIP WAIT/WAVE ไม่ถูกย้าย
- **Phase 2B Fleet Presenter DONE:**
  - เพิ่ม pure/no-Qt `controllers/fleet_presenter.py`
  - แยก name normalization/fallback, fleet count, selection highlight/summary และ group badge/chip presentation
  - คง selection/target authority, Summary/Waypoint/WAVE orchestration, persistence และ telemetry ingest ใน `GroundStation`
- **Phase 2C Map Presentation Adapter DONE:**
  - เพิ่ม pure/no-Qt `controllers/map_presenter.py`
  - แยกเฉพาะ sorted/filter/format ของ Map 3D drone-marker payload
  - คง readiness, scheduling, JSON/JavaScript dispatch, navigation targets, routes, selection,
    fence, waypoint และ flight authority ใน `GroundStation`

## Files Changed (โดย migration งานนี้)
- `docs/ARCHITECTURE_MIGRATION_PROGRESS.md` — handoff notebook
- `docs/APP_PY_RESPONSIBILITY_MAP.md` — Phase 1 inventory deliverable (docs เท่านั้น)
- **`frontend/swarmgod_gui/core/health_monitor.py`** (ใหม่) — UI Watchdog observe-only, pure model,
  ไม่มี Qt/ไม่มี client. วัด stall/telemetry age/rpc/render/core-connected
- **`frontend/tests/test_health_monitor.py`** (ใหม่) — 16 unit tests (FakeClock, ไม่รอเวลาจริง)
- **`frontend/swarmgod_gui/controllers/summary_presenter.py`** (ใหม่) — presentation-only adapter;
  ไม่มี flight state, Qt, RPC หรือ command client
- **`frontend/swarmgod_gui/controllers/__init__.py`** (ใหม่)
- **`frontend/tests/test_summary_presenter.py`** (ใหม่) — characterization 5 tests
- **`frontend/swarmgod_gui/controllers/fleet_presenter.py`** (ใหม่) — presentation-only;
  ไม่มี Qt/client/RPC/flight state และไม่ mutate selected IDs
- **`frontend/tests/test_fleet_presenter.py`** (ใหม่) — characterization 6 + pure unit 4 tests
- **`frontend/swarmgod_gui/controllers/map_presenter.py`** (ใหม่) — pure 3D drone-marker formatter;
  ไม่มี Qt/client/RPC/navigation/flight authority
- **`frontend/tests/test_map_presenter.py`** (ใหม่) — characterization 3 + pure unit 1 test
- **`frontend/swarmgod_gui/app.py`** — wiring observe-only แบบ additive:
  - import HealthMonitor; สร้าง `self._health` ใน `__init__` (ก่อน `_start_stream`)
  - `_tick_clock`: `record_ui_tick()` + `_log_health_snapshot()` ทุก ~30s; เพิ่ม `_on_ui_stall` (log WARNING เท่านั้น)
  - `_on_telemetry`: `note_telemetry(id)`; `_conn_watchdog`: `note_core_connected`;
    `_run_cmd`: `note_rpc(label)`; `_render_summary`: `record_render()`
  - **ไม่เปลี่ยน flight behavior / command order ใด ๆ** — เพิ่ม observability call ล้วน ๆ
  - เพิ่ม `SummaryPresenter` wiring และเปลี่ยน `_summ_*`/`_render_summary` เป็น wrapper;
    flight progress/run_id และ waypoint runtime ยังอยู่ที่เดิม
  - เพิ่ม `FleetPresenter` wiring สำหรับ name/count/selection/group presentation;
    `_on_fleet_click`, `_selected_ids`, `_target_ids`, `_target_mode`, RPC และ flight flow อยู่ที่เดิม
  - เพิ่ม `MapPresenter` wiring และคง `_push_map3d()` เป็น compatibility/readiness/dispatch wrapper;
    `_push_map3d_view`, `_push_map3d_targets`, route/waypoint/navigation ไม่เปลี่ยน

## Tests Run (baseline — 2026-08-27)
- `cd frontend && QT_QPA_PLATFORM=offscreen python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
  → **181 passed in 106.74s** ✅
- `cd backend && go test ./...` → **ทุก package ผ่าน (ok/cached)** ✅
- หมายเหตุ: baseline ไม่ได้รัน full Qt suite (930) — memory ห้ามรันชุดเต็มพร่ำเพรื่อ (ช้ามาก)

### Observability phase (2026-08-27)
- `python -m pytest tests/test_health_monitor.py -q` → **16 passed** ✅
- subset (health + ui_selection + preflight_summary_v2 + rtl_and_mapui + servo_and_ui) → **184 passed** ✅
- `python -m py_compile app.py health_monitor.py` → OK
- `python -m pytest -q` → **946 passed, 1 cache warning in 624.68s** ✅
  (warning = pytest เขียน `.pytest_cache` ไม่ได้; exit code 0 และไม่กระทบ test)

### Phase 2A — Summary Presenter (2026-08-27)
- `python -m py_compile swarmgod_gui/app.py swarmgod_gui/controllers/summary_presenter.py tests/test_summary_presenter.py` → OK
- presenter + Summary V2 pure/widget tests → **19 passed** ✅
- app integration (`test_waypoint_ui.py`, `test_waypoint_wait.py`, `test_wave.py`) → **104 passed in 101.95s** ✅
- `python -m pytest -q` หลัง extraction → **951 passed, 1 cache warning in 893.27s** ✅

### Phase 2B — Fleet Presenter (2026-08-27)
- characterization baseline: **58 passed in 40.33s** ✅
- targeted หลัง extraction + pure unit: **62 passed in 40.48s** ✅
- adjacent field/telemetry/waypoint/summary: **121 passed in 87.52s** ✅
- `python -m pytest -q` หลัง extraction: **961 passed, 1 cache warning in 804.97s** ✅

### Phase 2C — Map Presentation Adapter (2026-08-27)
- characterization baseline (Map presenter + Map 3D): **56 passed in 14.30s** ✅
- targeted หลัง extraction + pure unit: **57 passed in 17.82s** ✅
- adjacent Map/RTL/GPS/collision/Waypoint: **177 passed in 163.92s** ✅
- Python compile + forbidden dependency/safety diff audit: clean ✅
- `python -m pytest -q` หลัง extraction: **965 passed, 1 cache warning in 888.05s** ✅

## Current Architecture Findings
- `frontend/swarmgod_gui/app.py` ปัจจุบัน = 9035 บรรทัด (Responsibility Map snapshot เดิม = 9024),
  class `GroundStation` ถือ state จำนวนมาก
  (telemetry, selection/fleet, map, swarm/head, RTL, waypoint routes/execution, WAIT timers,
  WAVE, preflight/summary, Field Tablet bridge, UI/REMOTE state)
- Pure logic แยกออกไปแล้วบางส่วน: `core/waypoint_logic.py`, `core/swarm_logic.py`,
  `core/flight_progress.py` (pure model + run_id guard)
- Go Core แยก package: fleet / swarm / safety / telemetry / command / api อยู่แล้ว
- WIP ปัจจุบัน (branch `main2`) = feature WAYPOINT WAIT + SWARM MODE LOCK + failsafe interlock
  Codex อ้างว่า: Frontend 930 passed, `go test ./...` ผ่านทุก package (ยังต้อง verify เอง)

## Decisions Made
- **D1:** ถือ Phase 0 = "verify WIP baseline ก่อน" ตามแผน (ห้ามเริ่ม refactor ถ้า WIP ยัง fail แบบไม่เข้าใจ)
  - เหตุผล: plan §6 + prompt Phase 0 ระบุชัด
  - ทางเลือกที่ปฏิเสธ: กระโดดไปทำ responsibility map ทันที — เสี่ยง refactor ชน WIP
- **D2:** จะ **ไม่รัน full Qt suite** ทีเดียว ใช้ targeted tests ของ WIP subsystem แทน
  - เหตุผล: memory `run-only-affected-tests` — full Qt GUI suite ช้ามาก
  - ทางเลือกที่ปฏิเสธ: รัน `pytest -q` ทั้งชุด (930 เทส) — กิน quota/เวลาเกินจำเป็นสำหรับ baseline
- **D3:** SummaryPresenter รุ่นแรกถือเฉพาะ presentation mapping/rendering และ Mission Log audit
  - compatibility method เดิมใน `GroundStation` ยังอยู่ตามแผน §8
  - ไม่ย้าย `_summ_sync_waypoint_body`, `FlightRun`, run_id, RPC หรือ async callback
  - เหตุผล: ลด diff/rollback ง่าย และไม่แตะ WIP WAIT/SWARM/failsafe
- **D4:** FleetPresenter รับเฉพาะ snapshot/adapters และไม่รับ `GroundStation self`
  - mixed methods `_refresh_selection_ui`/`_refresh_group_ui` คง cross-subsystem orchestration ไว้ที่เดิม
  - presenter ไม่มี Qt/CoreClient/RPC/flight command และไม่ถือหรือแก้ selection authority
- **D5:** MapPresenter รอบแรกย้ายหนึ่ง responsibility คือ 3D drone-marker payload เท่านั้น
  - readiness/dispatch อยู่ใน compatibility wrapper เดิม
  - ไม่ย้าย map view/target/route/fence/waypoint/selection เพราะเป็น business/navigation state

## Risks / Known Issues
- WIP diff ใหญ่มาก (`app.py` +951 บรรทัดจาก WIP feature) → พื้นที่นี้ "ห้ามแตะ" ตอน refactor
- `.pytest_cache` เดิมเขียนไม่ได้ (WinError 5) แต่ test execution ผ่านตามปกติ
- ห้าม import widget-only test ที่สร้าง `QApplication` ก่อน app/QtWebEngine test ใน process เดียว;
  targeted run จึงแยกเป็น pure/widget กับ app integration สอง process

## WIP That Must Not Be Overwritten
ไฟล์ WIP ของ feature WAIT/SWARM-LOCK/failsafe (ห้าม revert/overwrite/cleanup):
- `frontend/swarmgod_gui/app.py` (WAIT execution, mode-lock, failsafe interrupt)
- `frontend/swarmgod_gui/core/waypoint_logic.py` (`wait_seconds`, validators, route helpers)
- `frontend/swarmgod_gui/core/swarm_logic.py` (priority `wait=5.5`)
- `frontend/swarmgod_gui/assets/map.html` (WAIT badge, `setWaypointMeta`)
- `backend/internal/fleet/manager.go` (`FailsafeActive`)
- `backend/internal/swarm/manager.go` (formation interlock, `haltFormation`)
- tests ใหม่: `test_waypoint_wait*.py`, `test_waypoint_swarm_lock.py`, `test_waypoint_failsafe.py`,
  `failsafe_active_test.go`, `failsafe_interlock_test.go`, และ untracked docs

## Exact Next Step
1. เริ่ม **V2 F1 — Mission Boundary Audit + Contract** โดยไม่เปลี่ยน flight behavior
2. สร้าง state-transition table จาก Python source ปัจจุบันและเอกสาร
   `docs/MISSION_CORE_CUTOVER_CONTRACT.md`
3. ระบุ MissionPlan/run_id/Start/Cancel/Query/preemption/reconnect/authority cutover ให้ครบ
4. ห้ามเริ่ม F2 shadow engine จน F1 checkpoint เสร็จ

> V1 Phase 3 Telemetry Store = **NOT DONE / DEFERRED UNTIL AFTER SAFETY FAST-TRACK**

## If Another AI Continues
1. อ่านไฟล์นี้และ `docs/REAL_FLIGHT_SAFETY_FAST_TRACK_V2.md` ทั้งไฟล์ก่อน
2. อย่าแตะไฟล์ในหัวข้อ "WIP That Must Not Be Overwritten"
3. V1 Phase 3 ถูกพัก; ห้ามสร้าง TelemetryStore หรือทำ render coalescing ตอนนี้
4. Current track คือ V2; F0 ผ่านแล้วและ exact next phase คือ F1 Contract ก่อน F2/F3/F4 เสมอ
5. ห้ามสร้าง dual authority หรือให้ mission bypass `command.Service`/safety

## Commands To Re-run
- Summary targeted pure/widget: `cd frontend && python -m pytest tests/test_summary_presenter.py tests/test_preflight_summary_v2.py -q`
- Summary app integration: `cd frontend && python -m pytest tests/test_waypoint_ui.py tests/test_waypoint_wait.py tests/test_wave.py -q`
- Fleet targeted: `cd frontend && python -m pytest tests/test_fleet_presenter.py tests/test_ui_selection.py tests/test_groups.py -q`
- Fleet adjacent: `cd frontend && python -m pytest tests/test_field_phase3.py tests/test_waypoint_ui.py tests/test_preflight_summary_v2.py -q`
- Map targeted: `cd frontend && python -m pytest tests/test_map_presenter.py tests/test_map3d.py -q`
- Map adjacent: `cd frontend && python -m pytest tests/test_tactical_map.py tests/test_rtl_and_mapui.py tests/test_map_gps.py tests/test_map_collision_ui.py tests/test_waypoint_ui.py -q`
- F0 frontend baseline: `cd frontend && python -m pytest tests/test_waypoint.py tests/test_waypoint_wait.py tests/test_waypoint_wait_runtime.py tests/test_waypoint_swarm_lock.py tests/test_waypoint_failsafe.py tests/test_waypoint_separate.py tests/test_wave.py tests/test_preflight_summary_v2.py -q`
- F0 Go baseline: `cd backend && go test ./internal/fleet/... ./internal/swarm/... ./internal/command/... ./internal/api/...`
- Baseline Go: `cd backend && go test ./internal/fleet/... ./internal/swarm/...`
- Full frontend (ช้า, ทำเฉพาะตอนจบ Phase): `cd frontend && python -m pytest -q`

## Last Known Git Status
- Branch: `main2` (up to date with origin/main2)
- WIP feature diff ใหญ่และยังไม่ commit; migration เพิ่ม controller/test/docs โดยไม่ revert ของเดิม
- ยังไม่มี commit จากงาน migration นี้

## Final Independent-Audit Repair Checkpoint — 2026-08-27

> This checkpoint supersedes older `Exact Next Step` notes above for the current migrated source state.

### DONE
- Independent Claude audit finding H1 (manual/ad-hoc command overlap with Core-owned mission) was cross-checked against source and repaired.
- Independent audit finding M1 (authority rollout relying on implicit default SITL profile) was repaired fail-closed.
- Manual command ownership now has two layers: frontend/operator-path guard plus Go API authority guard.
- Core mission and Go Swarm/RETURN navigation are mutually exclusive: `StartMission` rejects while swarm/return navigation is busy, and Swarm START rejects while a Core mission owns navigation.
- Operator takeover paths make the Core run terminal before command execution where appropriate (HOLD/STOP/LAND/RTL/DISARM/KILL/altitude takeover).
- Non-takeover commands that could overlap active mission authority are rejected (GOTO/RC MOVE/MODE/ARM/TAKEOFF/Swarm START).
- Stale queued Python GOTO/RC callbacks are suppressed while Core authority is active.
- Selected Drone Card, Cockpit, Field Tablet, leader-mode and direct Go API paths were included in the ownership sweep.

### AUDIT FINDINGS STATUS
- H1: **CLOSED / VERIFIED BY TESTS**
- M1: **CLOSED / VERIFIED BY TESTS**
- P1 failsafe GOTO/HOLD suppression: **INTENTIONAL SAFETY IMPROVEMENT — keep**
- MT1/MT2/MT3 manual takeover/overlap coverage: **ADDED**, with additional Swarm/RETURN mutual-exclusion coverage.

### FINAL TEST RESULTS
- Targeted Go (`internal/api`, `internal/mission`, `internal/command`, `internal/swarm`): **PASS**
- Targeted frontend mission-authority suite: **33/33 PASS**
- Full Go regression: `go test ./...` = **PASS all packages**
- Go static verification: `go vet ./...` = **PASS**
- Full frontend regression: `python -m pytest -q tests` = **1002/1002 PASS in 813.44s (0:13:33)**
- Final frontend task id: `f5f4328a-323d-435b-a0f3-533ffcdd6cee`, exit code 0.

### MIGRATION/GATE STATUS AFTER REPAIR
- Software no-dual-authority checkpoint: **VERIFIED PASS for currently migrated authority scope and audited manual/Swarm entrypoints**.
- F6/F8 audit gaps caused by H1 are considered closed at software-regression level by the guards/tests above.
- F9A software/bench preparation remains software-complete.
- F9B actual physical FC verification remains **PENDING ACTUAL FC**.
- F10 real-flight verification remains **LOCKED** until required hardware evidence exists.

### SAFE-RUNNABLE / NEXT STEP
- Current source is software-regression green and suitable for continued SITL/software work.
- Do **not** claim physical-flight readiness from these tests.
- Exact next hardware gate is **F9B actual FC bench verification** using the existing guarded bench procedure/evidence tooling.
- After F9B evidence passes, evaluate the F10 gate separately; do not auto-promote F10.
