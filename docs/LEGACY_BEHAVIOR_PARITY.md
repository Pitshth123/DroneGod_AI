# Legacy DroneGod → DroneGod_AI Behavior Parity

Date: 2026-08-27
Status: ACTIVE BASELINE / old workspace read-only

## Purpose

The pre-migration `DroneGod` is the behavioral baseline. `DroneGod_AI` must preserve those proven operator capabilities while authority is migrated incrementally into Go. This does **not** mean copying the old monolith back over the new architecture. It means keeping the old behavior for every capability that has not yet completed its Go authority migration.

## Direct source parity evidence

Read-only SHA-256 comparison between the registered old `DroneGod` workspace and `DroneGod_AI` found:

- `frontend/swarmgod_gui/**/*.py`: **59 identical / 3 different / 0 missing**.
  - Different: `app.py`, `core/mission_shadow.py`, generated `mission_pb2.py`.
  - The `app.py` diff is additive mission authority/reconnect/cache guarding; old WAVE, SEPARATE, GROUPED geometry, SWARM leader, WAIT and payload implementation remains present.
- `frontend/tests/*.py`: **34 identical / 2 different / 0 missing**.
  - Different: `test_mission_shadow.py` for migration-specific authority/reconnect coverage and `test_wave.py` for the Core-slot no-overlap guard on legacy WAVE entrypoints.
- Go flight path baseline (`internal/command`, `fleet`, `safety`, `swarm`, `mavlink`, `config`): **27 identical / 1 different / 0 missing**.
  - Different: `internal/command/service.go`, where `Goto`/`Hold` now yield to the fleet failsafe latch before navigation/mode sends. This is safety hardening, not capability removal.
- Static `self.client.*` call-surface extraction from old vs AI `app.py`: **25 methods vs 25 methods; 0 missing / 0 unexpected additions**. The migration did not silently remove an operator command path from the cockpit.
- `app.py` method-surface comparison: old **412** unique methods vs AI **413**; **0 old methods missing**. The only added method is `_apply_mission_core_state` for Core query/reconnect presentation state.
- Proto comparison: **5 identical / 1 different / 0 missing**; only `mission.proto` differs for the additive run/authority contract. Existing command/telemetry/swarm/service contracts remain present.
- Widget layer: **17/17 identical / 0 different / 0 missing**.

## Legacy capability matrix

| Capability | Old behavior | DroneGod_AI policy before that capability is migrated |
|---|---|---|
| Single-drone GROUPED, no WAIT/action | Python GOTO progression | Go Core may own it under exact `core-single` / `core-single-wait` token |
| Single-drone GROUPED + WAIT | Python HOLD/deadline/progression | Go Core may own it only under exact `core-single-wait` token |
| Multi-drone GROUPED | Current-centroid translation preserves formation offsets; per-drone altitude; GOTO stagger 150 ms | **Keep legacy Python executor** until Go ports equivalent geometry/arrival/dispatch semantics |
| SEPARATE | Per-drone route/index; each drone advances independently; preflight route conflict gate | **Keep legacy Python executor** |
| SWARM Leader Path | Only Head gets mission GOTO; Go formation loop owns followers | **Keep legacy Python mission executor** until explicit leader-path cutover |
| WAVE | Groups fly shared route serially; prior group RTL/lands/disarms before next group | **Keep legacy Python executor** |
| Payload A/B at waypoint | Arrive → HOLD → servo A/B → wait 2 s → release → advance, with pre-execute confirmation | **Keep legacy Python executor** |
| Battery/link failsafe | Go fleet failsafe owns RTL; Python only invalidates mission progression | Preserved; Go mission state also becomes INTERRUPTED when applicable |
| Cancel/E-STOP stale callbacks | Generation/run guards suppress later progression | Preserved; Core-owned runs additionally use Core run_id/stale-safe cancellation |

## Ownership rule after parity repair

`SWARMGOD_MISSION_AUTHORITY` is global configuration, but ownership is selected **per plan**.

1. Determine whether the exact plan is eligible for the enabled Core scope before calling authoritative `StartMission`.
2. If the plan is ineligible, **do not call authoritative StartMission at all**. First query Core and require the mission slot to be confirmed idle; then use the unchanged legacy Python executor. This is intentional ownership, not a transport-error fallback.
3. If Core already reports an active authoritative run, or the Core mission slot cannot be queried, block the legacy mission fail-closed. A Python-owned mission must never overlap an unknown/active Core-owned mission.
4. WAVE uses the same legacy-slot guard inside `_wave_execute()` itself, so both Cockpit and Field Tablet entrypoints are covered. Core idle = legacy WAVE remains available; active/unknown Core slot = WAVE does not start.
5. If the plan is eligible and authoritative StartMission is attempted, a timeout/lost/ambiguous reply remains **fail-closed**. Query Core state; never fall back to Python GOTO because Core may already have accepted the run.
6. Python browser `target_reached` and WAIT callbacks have no progression authority while Core confirms `authority_active=true`.
6. WAVE is a legacy-only path that bypasses normal Mission Start by design, so `_wave_execute()` itself performs the same Core-slot-idle proof. This central guard covers both Cockpit EXECUTE and Field Tablet direct WAVE execution. Core active/unknown = WAVE blocked; Core confirmed idle = unchanged legacy WAVE executor.

This rule preserves old working functionality without reintroducing dual authority.

## Current Core eligibility

### `core-single`
- GROUPED only
- exactly one participant
- non-empty shared route
- no WAIT
- no payload action
- no WAVE
- no `rtl_after`

### `core-single-wait`
- GROUPED only
- exactly one participant
- non-empty shared route
- WAIT allowed
- no payload action
- no WAVE
- no `rtl_after`

Everything else remains legacy Python-owned until separately migrated and verified.

## Verification gates

- Migration ownership tests must prove unsupported old capabilities do not call authoritative StartMission when a Core token is enabled.
- Multi-drone GROUPED must still emit per-drone legacy targets rather than collapse all drones onto one waypoint coordinate.
- SEPARATE must still start independent GOTO routes and advance independently.
- SWARM Leader Path must still command only the Head.
- WAVE/payload/WAIT legacy regression suites remain green.
- Existing eligible Core-owned single-drone tests must continue proving no Python GOTO and fail-closed lost/ambiguous Start behavior.
- Full frontend and full Go suites remain the release regression gates.

## Hardware relationship

This parity baseline reduces F9B to verification of the architecture change against the actual FC. It does not replace F9B: actual command ACK timing, telemetry timing, onboard GCS/Core-loss failsafe, reconnect behavior and manual takeover still require the real FC bench.
