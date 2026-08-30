# V3-S12 — SITL Endurance + Performance

**Status: V3-S12 TOOLING COMPLETE / CURRENT 30-MINUTE GATE PENDING (policy updated 2026-08-29).**

No 30-minute, 2-hour, 6-hour, 12-hour, or 24-hour result is claimed here yet.

**Current project-time decision:** only the **30-minute endurance run** is required
now. After that run passes and its evidence is reviewed, the project may continue
to the next necessary work. The 2h/6h/12h durations remain documented and runnable,
but are **DEFERRED EXTENDED ENDURANCE** to be executed later when the complete system
is ready and the user explicitly requests the longer validation. The optional 24h
run remains a soak test only. This policy does not weaken or change mission authority
or flight-safety semantics; it only changes when extended endurance evidence is run.

This stage did not start V3-H02 or V3-R01 and did not use real hardware.

## 1. Audit and reused evidence

The implementation began by inspecting the current roadmap, S10/S11 evidence,
current launch scripts, Core and frontend metrics, and the existing probes.

EXISTING / REUSABLE:

- `backend/cmd/missionverify`: live single-drone Start, duplicate Start,
  disconnect/reconnect, WAIT, completion, and Cancel flow.
- `backend/cmd/sitlverify`: configurable multi-SITL connection and telemetry
  checks. Its authoritative swarm/WAVE actions remain outside the live S12
  authority scope and are not reused as S12 mission authority.
- `backend/internal/bench` and `backend/cmd/benchprobe`: telemetry interval/age
  aggregation and read-only Core probe patterns.
- `backend/cmd/benchack`: command ACK timing pattern. S12 uses read-only RPC
  latency and separately gates mission mutation.
- `scripts/wsl_sitl.sh`, `scripts/wsl_sitl_multi.sh`, and
  `scripts/run_mission_sitl_core.bat`: existing SITL/Core launch paths.
- `TelemetryStore`, `TelemetryRenderGate`, and `HealthMonitor`: telemetry ingest,
  presentation coalescing, render counters, heartbeat/stall, and age seams.
- `CoreClient`, its deterministic close behavior, and the existing gRPC/pinger
  lifecycle tests.
- V2 F8 evidence recorded in the roadmap: missionverify 4/4 guarded live SITL
  cycles; duplicate Start/reconnect/WAIT/Cancel; 500 alternating terminal Engine
  cycles; bounded Core RSS/thread/handle samples.

PARTIAL before S12:

- Resource and telemetry evidence existed in separate tools but not in one
  duration-configurable report.
- Frontend metrics existed in runtime objects/log lines but had no repeatable
  endurance evidence schema.
- Lifecycle stress existed as deterministic tests, not as a long-run scenario.

MISSING before S12:

- One canonical duration/scenario runner, periodic process/file samples,
  baseline/final/trend aggregation, PASS/FAIL/ABORTED classification,
  collision-safe output naming, and documented long-run commands.

## 2. Canonical S12 architecture

The canonical entrypoint is `scripts/v3_s12_endurance.py`. It attaches to an
already-running Core and SITL fleet. It deliberately does not start or globally
kill Core, SITL, Python, or WSL processes. It owns and deterministically closes
only its gRPC channels, telemetry stream, and worker thread.

Important CLI parameters:

```text
--duration 30m|2h|6h|12h|24h
--scenario <name>
--sample-interval 10s
--action-interval 10s
--output <unique-path.json>
--core <host:port>
--core-pid <pid>
--drone-count <1..20>
--connect --base-port 5760
--log-dir <path> --db <path>
```

If `--output` is omitted, a microsecond timestamp and scenario name create a
non-overwriting path under `evidence/v3-s12`. An explicit existing output is
refused. Evidence is written to a temporary file, parsed, and atomically renamed.

## 3. Runnable scenario families

- `idle-connected`: connected Core/SITL telemetry with no mission action.
- `telemetry-5`: five-drone stream; fixes participant count at five.
- `telemetry-10`: ten-drone stream; fixes participant count at ten.
- `frontend-presentation`: feeds real stream messages through the existing
  immutable TelemetryStore, presentation render gate, and observe-only
  HealthMonitor. This is the deterministic safe presentation workload; it does
  not automate native GUI clicks or redesign widgets.
- `reconnect-churn`: closes/recreates only the UI/client transport and stream,
  checks that mission run/plan identity did not change, and never calls Start.
- `client-lifecycle`: repeatedly creates a real CoreClient, performs a snapshot
  RPC, and closes it; intended to expose cumulative native-client/thread growth.
- `mission-start-cancel`: alternates current allowed single-drone Core Start and
  Cancel. It requires an already-airborne D1 SITL, `SWARMGOD_PROFILE=sitl`, an
  existing live token (`core-single` or `core-single-wait`), and the explicit
  `--sitl-mission-ack` flag. It issues no Arm, Takeoff, Land, RTL, KILL, payload,
  WAVE, swarm, multi-drone, or corrective command.

The five/ten-drone and mission scenarios are runnable but were not executed in
this short validation. They require the matching SITL fleet; mission Start/Cancel
also requires a deliberately prepared airborne single-drone SITL.

## 4. Metrics collected

Periodic samples contain:

- recorder/Python RSS and, on Windows, private bytes;
- Core RSS when `--core-pid` is supplied;
- Python-managed thread count;
- recorder and Core OS thread totals (the recorder total includes observable
  native/background gRPC threads);
- Windows handle count or Linux `/proc/<pid>/fd` count;
- UI heartbeat status/gap and stall count;
- telemetry total/per-drone ingest count and interval rate;
- telemetry receive age and source timestamp age;
- presentation render/update rate, rendered count, skipped count/percentage;
- unary RPC latency and error count;
- reconnect, client lifecycle, mission transition, and mission Start/Cancel counts;
- recursive log size and SQLite/database file size start/end/delta;
- Core process exit/crash count when a PID is supplied.

Initial, steady-state, and final samples are explicit. Aggregates record sample
count, start, end, delta, minimum, maximum, and mean for resource and RPC metrics.

## 5. Metrics not currently exposed

- Go goroutine count: **NOT CURRENTLY EXPOSED**. Core has no safe metrics/pprof
  endpoint in the current architecture; S12 did not add a runtime endpoint.
- Native gRPC thread count separated from other OS threads: **NOT CURRENTLY
  EXPOSED**. Total recorder OS threads are captured instead.
- Real native GUI/WebEngine frame timing: **NOT CURRENTLY EXPOSED**. The safe
  read-model/render-gate/heartbeat path is measured; brittle desktop automation
  was not added.
- Linux private bytes: **NOT CURRENTLY EXPOSED** by the lightweight `/proc/status`
  sampler; RSS and thread/fd totals remain available.

## 6. Evidence schema

Schema identifier: `dronegod.v3.s12.endurance/v1`.

Every JSON report contains generated/start timestamps, Git commit/branch/dirty
identity where available, profile, locally observed authority token, scenario,
requested and actual duration, result and end reason, process IDs/ownership,
participant count, sample interval, time-series samples, aggregates, counters,
errors, reconnects, mission transitions, file growth, metric availability,
acceptance findings, and cleanup status.

`validate_evidence` rejects incomplete reports. SIGINT/SIGTERM sets the run to
ABORTED, closes owned resources, and still writes parseable evidence. A normal
run cannot become PASS unless the requested duration completed and telemetry was
nonzero.

## 7. Acceptance criteria

HARD FAIL:

- the supplied Core PID exits;
- an owned client/telemetry worker cannot be cleaned up;
- the telemetry stream ends unexpectedly;
- telemetry produces no packet for three consecutive sample intervals after
  having started, or any expected participant never produces telemetry;
- three consecutive scenario/health RPC operations fail;
- reconnect creates a different mission run/plan identity;
- recovery-required or terminal mission state regains active authority;
- requested duration ends early or expected telemetry remains zero.

INVESTIGATE (not automatically called a leak):

- at least five resource samples are nondecreasing, contain at least four real
  increases, and end above the baseline;
- UI stalls, telemetry age/rate deterioration, RPC latency/error changes,
  reconnect increases, or log/database acceleration seen in the evidence.

PASS:

- requested workload duration completes;
- telemetry is nonzero;
- no hard failure or observed safety invariant violation occurs;
- JSON evidence validates and owned client/stream cleanup succeeds.

Short-run monotonic RSS flags below are investigation prompts only. One short,
noisy sample series is not evidence of a memory leak.

## 8. Actual short validation (2026-08-29)

Environment: one ArduCopter SITL, Core at `127.0.0.1:50053`, explicit
`SWARMGOD_PROFILE=sitl`, `core-single-wait`; no real hardware.

Focused tooling tests:

```powershell
python -m pytest frontend\tests\test_v3_s12_endurance.py -q
# 18 passed
```

Idle connected telemetry:

```powershell
python scripts\v3_s12_endurance.py --scenario idle-connected --duration 45s `
  --sample-interval 5s --core 127.0.0.1:50053 --core-pid 11456 `
  --drone-count 1 --connect --output evidence\v3-s12\smoke-idle-45s-rerun.json `
  --log-dir backend\logs --db backend\logs\s12-smoke.db
```

Actual: PASS, 45.219 s, 452 telemetry messages, 11 samples, 12 successful RPCs,
zero RPC/stream errors, zero UI stalls, Python/Core OS thread delta 0/0, database
delta 0 B. Python RSS +172,032 B and Core RSS +1,810,432 B were monotonic across
this short series and therefore marked INVESTIGATE, not leak.

Presentation path:

```powershell
python scripts\v3_s12_endurance.py --scenario frontend-presentation --duration 25s `
  --sample-interval 5s --core 127.0.0.1:50053 --core-pid 11456 `
  --output evidence\v3-s12\smoke-presentation-25s.json
```

Actual: PASS, 25.219 s, 252 telemetry messages, 151 rendered, 101 presentation
updates coalesced, zero RPC/stream errors, zero UI stalls, Python/Core OS thread
delta 0/0.

Reconnect churn:

```powershell
python scripts\v3_s12_endurance.py --scenario reconnect-churn --duration 25s `
  --sample-interval 5s --action-interval 5s --core 127.0.0.1:50053 `
  --core-pid 11456 --output evidence\v3-s12\smoke-reconnect-25s.json
```

Actual: PASS, 25.187 s, 252 telemetry messages, five reconnects, 22 successful
RPCs, zero RPC/stream errors, no mission identity replacement, Python/Core OS
thread delta 0/0.

Repeated client create/close:

```powershell
python scripts\v3_s12_endurance.py --scenario client-lifecycle --duration 25s `
  --sample-interval 5s --action-interval 5s --lifecycle-batch 10 `
  --core 127.0.0.1:50053 --core-pid 11456 `
  --output evidence\v3-s12\smoke-lifecycle-25s.json
```

Actual: PASS, 25.360 s, 253 telemetry messages, 50 CoreClient create/snapshot/close
cycles, 57 successful RPCs, zero RPC/stream errors, zero UI stalls, Python/Core
OS thread delta 0/0.

An initial 0.172 s attempt correctly wrote FAIL evidence after a telemetry request
was imported from the wrong generated module. The tooling defect was fixed,
covered by a regression test, and all runs above used the fixed path.

After validation, the exact Core PID and WSL host PID started for this session were
stopped. A WSL process check found no `arducopter` or `sim_vehicle.py` left running.

Regression verification:

```powershell
python -m pytest frontend\tests\test_v3_s12_endurance.py `
  frontend\tests\test_telemetry_store.py `
  frontend\tests\test_telemetry_store_integration.py `
  frontend\tests\test_grpc_lifecycle.py `
  frontend\tests\test_pinger_lifecycle.py -q
# 48 passed (one non-test pytest-cache permission warning)

cd backend
go test ./internal/bench ./internal/api -run 'TestSummarizeTiming|TestMissionAuthorityMode' -count=1
# PASS
go test ./... -count=1
# PASS
go vet ./...
# PASS

cd ..
git diff --check
# PASS; warnings only for pre-existing CRLF files not changed by S12
```

The full slow frontend suite was not rerun; the affected telemetry, presentation,
gRPC lifecycle, pinger lifecycle, and new S12 tests were run together.

## 9. Long-run commands

Prerequisite: launch only the required SITL fleet and the existing SITL Core in
separate terminals. Supply the actual Core PID so crashes and Core RSS/threads are
observable. Example PowerShell discovery for port 50053:

```powershell
$corePid = (Get-NetTCPConnection -LocalPort 50053 -State Listen).OwningProcess
$env:SWARMGOD_PROFILE = "sitl"
$env:SWARMGOD_MISSION_AUTHORITY = "core-single-wait"
```

Exact next 30-minute run:

```powershell
python scripts\v3_s12_endurance.py --scenario idle-connected --duration 30m `
  --sample-interval 10s --core 127.0.0.1:50053 --core-pid $corePid `
  --drone-count 1 --connect --log-dir backend\logs `
  --db backend\logs\missionverify.db
```

Exact 2-hour run:

```powershell
python scripts\v3_s12_endurance.py --scenario frontend-presentation --duration 2h `
  --sample-interval 30s --core 127.0.0.1:50053 --core-pid $corePid `
  --drone-count 1 --log-dir backend\logs --db backend\logs\missionverify.db
```

Exact 6-hour run (requires five SITL instances on ports 5760, 5770, ...):

```powershell
python scripts\v3_s12_endurance.py --scenario telemetry-5 --duration 6h `
  --sample-interval 30s --core 127.0.0.1:50053 --core-pid $corePid `
  --connect --base-port 5760 --log-dir backend\logs --db backend\logs\missionverify.db
```

Exact 12-hour run (requires ten SITL instances on ports 5760, 5770, ...):

```powershell
python scripts\v3_s12_endurance.py --scenario telemetry-10 --duration 12h `
  --sample-interval 60s --core 127.0.0.1:50053 --core-pid $corePid `
  --connect --base-port 5760 --log-dir backend\logs --db backend\logs\missionverify.db
```

Optional exact 24-hour client lifecycle run:

```powershell
python scripts\v3_s12_endurance.py --scenario client-lifecycle --duration 24h `
  --sample-interval 60s --action-interval 30s --lifecycle-batch 5 `
  --core 127.0.0.1:50053 --core-pid $corePid --drone-count 1 `
  --log-dir backend\logs --db backend\logs\missionverify.db
```

Reconnect churn and prepared-airborne SITL mission Start/Cancel must also be run
before full S12 closure; they are separate scenario evidence, not substitutes for
the multi-drone ladder.

## 10. Cleanup and interruption

- Normal completion and Ctrl+C close the owned telemetry call, join its worker,
  close every CoreClient/channel, and write final evidence.
- Ctrl+C/SIGTERM records ABORTED, never PASS.
- The harness has no global process-kill path and does not stop external Core or
  SITL processes. The operator that launched those processes owns their shutdown.
- Mission scenario cleanup only cancels the run it started. It does not invent a
  LAND/RTL/HOLD/KILL command.

## 11. Bugs found and fixed

- Runtime/product performance or safety bug: **NONE** in the executed short runs.
- S12 tooling bug: the first stream request referenced `service_pb2` instead of
  `telemetry_pb2`. Fixed locally and covered by
  `test_telemetry_session_uses_request_from_telemetry_contract`.
- No broad optimization, runtime endpoint, mission semantic change, or authority
  expansion was made.

## 12. Current gate and deferred endurance evidence

**Current operational gate: PASS (2026-08-30).** The user elected to run a stronger
single-drone overnight soak than the previously required 30-minute gate. Actual
completed evidence:

- `idle-connected` — **6h PASS**, 21,600.172 s, 216,002 telemetry messages, 723 RPCs,
  zero RPC errors, zero stream errors, zero UI stalls, no hard failures, no resource
  trend flagged for investigation by the harness.
- `reconnect-churn` — **15m PASS**, 15 reconnects, 9,002 telemetry messages, zero
  RPC/stream errors and no mission identity replacement.
- `client-lifecycle` — **15m PASS**, 75 create/snapshot/close cycles, 9,003 telemetry
  messages, zero RPC/stream errors and stable Core OS thread count.

A single Core log warning occurred during the six-hour run:
`registry upsert drone 1 failed: database is locked (5) (SQLITE_BUSY)`. The endurance
harness still completed successfully, telemetry/RPC continued normally, the database
file size did not change, and no repeat warning was observed in the captured Core
stdout. Treat this as a follow-up SQLite contention item, not as evidence of a flight
safety failure or endurance crash.

**Deferred for later validation:** 12h/24h soak, five/ten-drone long-duration runs,
and prepared-airborne mission Start/Cancel endurance remain useful but are not a
current blocker. Run them only when explicitly scheduled later. Do not convert
unexecuted durations into PASS evidence.

## 13. Authority gate

Only `core-single` and `core-single-wait` remain live. The harness explicitly
refuses `core-grouped-multi`, `core-separate`, `core-swarm-leader`, `core-wave`,
and `core-payload`. S09 authority is not flipped. SWARM_LEADER follower takeover
remains DECISION REQUIRED and `rtl_after` remains BLOCKED/undefined.
