# APP_PY RESPONSIBILITY MAP

> Phase 1 deliverable ของ Low-Risk Architecture Migration (ดู `LOW_RISK_ARCHITECTURE_MIGRATION_PLAN.md`)
> **Inventory เท่านั้น — ยังไม่ย้าย/rename/refactor code ใด ๆ** (Phase 1 = no behavior change)
> เป้าหมาย: ให้ AI/คน แก้ subsystem หนึ่งได้โดยไม่ต้องอ่าน `app.py` ทั้ง 9024 บรรทัด
> Source ณ วันจัดทำ: `frontend/swarmgod_gui/app.py` = **9024 บรรทัด**, class เดียว `GroundStation(QMainWindow)` (บรรทัด 115–8994)
> วิธีใช้: หาว่างานอยู่ subsystem ไหน → เปิดเฉพาะช่วงบรรทัด + `core/*` + tests ที่ระบุ

---

## 0. Classification legend

แต่ละ method จัดเป็น 1 ใน 4 ระดับ (สำคัญต่อการเลือกลำดับ extraction):

- **P (presentation-only)** — สร้าง/จัด widget, format, render label/marker, ไม่มี command side effect → **extract ก่อนได้ (Phase 2/4 ในแผน)**
- **B (business logic)** — ตัดสินใจ state/route/selection แต่ไม่ยิง flight command เอง
- **F (flight authority)** — เรียก `self.client.*` ที่ทำให้โดรนเคลื่อน/arm/mode เปลี่ยน → **ห้าม extract ช่วงต้น**
- **S (safety-critical)** — RTL / E-STOP / failsafe interrupt / geofence / collision / WAIT-cancel guard → **ห้ามแตะจนถึง Phase ปลาย**

> กฎ Phase 2: extract เฉพาะ **P** ก่อน; **F/S ห้ามย้าย** จนกว่าจะถึง Command Gateway (Phase 4) และ Mission Engine (Phase 6)

---

## 1. State Ownership

รูปแบบ: `field — owner subsystem — safety relevance — async relevance`
(writers/readers = "หลายที่" หมายถึงถูกอ่าน/เขียนจากหลาย method ข้าม subsystem = shared mutable state เสี่ยง)

### 1.1 Connection / Fleet registry
| field | owner | safety | async |
|---|---|---|---|
| `client` (CoreClient) | connection | — (transport) | ใช้จาก daemon threads หลายตัว |
| `telem_thread`, `event_thread` | telemetry stream | — | **named background threads** |
| `fleet_items` (id→FleetItem) | fleet UI | — | เขียนจาก telemetry callback (main thread via signal) |
| `_removed_ids`, `_reserved_ids` | fleet registry | — | อ่าน/เขียนใน telemetry + connect |
| `_endpoints`, `_connect_protocol`, `_connect_endpoint` | connection | — | main thread |
| `_ip_store`, `_group_store`, `group_of` | persistence (SQLite) | — | main thread |

### 1.2 Telemetry / latest state (shared, high-read)
| field | owner | safety | async |
|---|---|---|---|
| `_last_telem` (id→Telemetry) | telemetry | **อ่านโดย safety/preflight/RTL/waypoint** | เขียนใน `_on_telemetry` |
| `_last_seen` | telemetry/online | online detection | throttle |
| `_last_telem_log`, `_last_alt` | telemetry | — | throttle |

> `_last_telem` = **source of truth ฝั่ง UI** — Phase 3 (Telemetry Store shadow) จะ shadow ตัวนี้

### 1.3 Selection / Fleet UI
`_selected_id`, `_selected_ids`, `_drone_alt`, `_drone_spacing`, `_drone_names`, `_target_mode`, `_ui_mode`
— owner: selection/fleet — safety: `_target_ids()` กำหนดว่า command ไปลำไหน (**F ทางอ้อม**) — async: main thread

### 1.4 Map / view
`_map_ready`, `web`, `_sidebar_open`, `_sidebar_w`, `_map_enabled`, `_pixmap`, `_sections`, `tiles`, `map_url`, `_map_center`, `_map_followed_gps`, `_home_pos`
— owner: map/presentation — safety: `_home_pos` ใช้ตอน RTL preview — async: JS bridge callback

### 1.5 Head / Leader / Swarm
`_leader_id`, `_head_id`, `_head_pinned`, `_head_push_ts`, `_swarm_active`
— owner: swarm/head — safety: `_swarm_active` **ล็อกโหมด SEPARATE/WAVE (WIP)** — async: `_poll_swarm` timer + `_on_swarm_update`

### 1.6 Command summary / Flight run / Preflight
`_cmd_summary`, `_summ_batch`, `_flight_run`, `_flight_run_id`, `_flight_takeoff_sent`, `_flight_takeoff_targets`, `_preflight`, `_flight_mode`, `_nav_targets`, `_goto_armed`
— owner: preflight/execution-summary — safety: `_flight_run_id` = **run guard กัน stale callback** — async: RPC/telemetry callbacks

### 1.7 RTL (safety)
`_rtl_active`, `_rtl_pending`, `_rtl_phase`
— owner: RTL — safety: **S** — async: `_rtl_monitor_core` daemon thread + telemetry

### 1.8 Waypoint (route + execution)
`_waypoint_mode`, `_wp_separate`, `_waypoint_route`, `_wp_routes`, `_waypoint_executing`, `_wp_current_index`, `_wp_sep_index`, `_wp_target_ids`, `_wp_arrived`, `_wp_key`
— owner: waypoint — safety: execution = **F/S** — async: target-reached callback + timers

### 1.9 WAIT (WIP — ห้ามแตะ)
`_wp_waits`, `_wp_wait_generation`, `_wp_clock`, `_wp_takeoff_pending`, `_wp_takeoff_generation`, `_wp_airborne_alt_m`, `_wp_takeoff_timeout_s`, `_wp_alt_sep`, `_wp_min_dist`
— owner: waypoint WAIT — safety: **S** (`_wp_wait_generation` = invalidate guard) — async: `_wp_wait_timer` poll 500ms

### 1.10 WAVE (WIP-adjacent)
`_wave_enabled`, `_wave_executing`, `_wave_groups`, `_wave_group_index`, `_wave_phase`, `_wave_generation`, `_wave_group_started_at`, `_wave_timeout_s`, `_wave_route_template`, `_wave_auto`, `_wave_auto_next`, `_wave_payload_done`, `_wave_payload_failed`
— owner: WAVE — safety: **F/S** — async: `_wave_timer` + generation guard

### 1.11 Collision / RC / Servo / Landing / Tooltip
- Collision: `_collision_seen`, `_collision_crit`, `_collision_warn` — **S** — `_collision_timer`
- RC move: `_rc_dir`, `_rc_order`, `_rc_order_dir` — **F** — `_rc_timer`
- Servo: `_servo_btn_mode`, `_servo_primed`, `_servo_priming`, `_servo_state`, `_servo_pending` — F(payload) — daemon threads
- Landing: `_landing_seen`, `_land_guided_done` — S (auto-GUIDED after land) — telemetry
- Tooltip: `_tip_at`, `_tip_txt` — P — JS push
- Font: `_font_scale` — P

---

## 2. Async Inventory

### 2.1 Qt Signals (thread→main-thread marshaling) — บรรทัด 117–120
| signal | ปล่อยจาก | ใช้ทำอะไร |
|---|---|---|
| `cmd_result = pyqtSignal(str)` | worker threads | โชว์ผลคำสั่ง (`_on_cmd_result`) |
| `swarm_update = pyqtSignal(object)` | `_poll_swarm` worker | อัปเดต swarm state (`_on_swarm_update`) |
| `event_recv = pyqtSignal(object)` | EventThread | Core event (`_on_event`) |
| `ui_call = pyqtSignal(object)` | worker threads | รัน callable บน main thread (generic marshaler) |

### 2.2 QTimer instances (main thread) — 11 ตัว, สร้างใน `__init__` ~บรรทัด 306–353
`_clock`, `_swarm_timer`, `_rc_timer`, `_web_move_timer`, `_wave_timer`, `_wp_wait_timer`,
`_ping_timer`, `_head_timer`, `_collision_timer`, `_conn_timer` (+ `_ping` = PingService)
- **31× `QTimer.singleShot(...)`** กระจายทั่วไฟล์ (delay callbacks — จุดเสี่ยง stale callback, ป้องกันด้วย generation/run_id)

### 2.3 Background threads
- **2 named stream threads**: `TelemetryThread(self.client.stub)` (บ.8561), `EventThread(self.client.stub)` (บ.8565) — long-lived gRPC streams
- **~38 daemon `threading.Thread(target=worker)`**: pattern เดียวกัน = ยิง RPC blocking นอก UI thread แล้ว emit signal กลับ. ไม่มี cancellation lifecycle ชัด (worker จบเอง). จุดที่ Phase 4 Command Gateway จะรวมศูนย์
- 1 warm-up: `warm_channel` daemon (บ.423)

### 2.4 subprocess
- `webengine_available()` (บ.97–108) — probe QtWebEngine ใน subprocess (startup only, ไม่เกี่ยวการบิน)

---

## 3. Command Authority Inventory

**ทุก method ที่ยิง flight/side-effect command ผ่าน `self.client.*`** (= จุดที่ Phase 4 Gateway ต้องครอบ, ห้าม extract ก่อน)

| command | method(s) / line | class |
|---|---|---|
| **Arm** | `_cmd_arm` 3429, `_card_arm` 5112 | F |
| **Disarm** | `_cmd_disarm` 3443, `_card_disarm` 5126 | F |
| **Takeoff** | `_cmd_takeoff` 3978, `_takeoff_one` 4514, `_web_takeoff` 8387, `_demo_takeoff_parallel` 8956 | F |
| **Land** | `_cmd_land` 3957, `_card_land` 5132 | F |
| **RTL** | `_cmd_rtl` 3962, `_card_rtl` 5129 | **S** |
| **Hold** | `_cmd_hold` 3965, `_wp_run_action` 7026, `_wp_wait_hold` 7652, `_cancel_navigation` 6443, `_build_left`(btn) 770 | F |
| **Goto** | `_goto_one` 6452, `_web_goto` 8439 | F |
| **Change alt** | `_cmd_goalt` 3987 | F |
| **Set mode** | `_cmd_mode` 3983, `_leader_mode` 5799, `_auto_guided_after_land` 8718, `_web_set_mode` 8412 | F/S |
| **Swarm start/config** | `_swarm_start` 4006/4010, `_do_formup` 4666/4672 | F |
| **Swarm stop** | `_swarm_stop` 4022, `_cancel_navigation` 6438 | F/S |
| **Swarm return** | `_swarm_return` 4724 | **S** |
| **Set leader** | `_apply_head` 4357, `_on_leader_combo` 4399, `_on_swarm_update` 6044 | B/F |
| **RC move** | `_rc_tick` 6001/6005 | F |
| **Servo set/release** | `_prime_servo`/`_run_servo`/`_wp_run_action` 3588/3617/3631/3742/3745/6997/7034 | F(payload) |
| **Geofence** | `_fence_send` 6193 | **S** |
| **stop_all (E-STOP/cancel)** | `_do_estop` 4909, `_rc_stop` 5983, `_cancel_navigation` 6442, `_web_hold_all` 8028, `_reset_sim_battery` 2087 | **S** |
| **param_set** | `_reset_sim_battery` 2623 (SITL only) | B |
| **connect/disconnect** | `_card_connect` 5582, `_card_disconnect` 5601/5663, `_card_apply_ip` 5666, `_mark_disconnected` 5694, `_connect_from_*` 8894/8924/8944 | — |
| **swarm_state (read)** | `_poll_swarm` 6014 | read |
| **stream (read)** | `_start_stream` 8561/8565 | read |

> สังเกต: entry points กระจายมาก (UI button, card, web/tablet, auto-flow) → Phase 4 ต้องรวมให้ผ่าน `command_gateway.py` จุดเดียว

---

## 4. Method groups by subsystem (ช่วงบรรทัด + classification)

> ใช้เป็น index: อยากแก้เรื่องไหน เปิดช่วงนั้น. ตัวเลข = บรรทัดเริ่ม method แรกของกลุ่ม

### UI construction / chrome  — P
`_build_ui` 435, `_build_topbar` 491, `_build_gear_menu` 581, `_pill` 610, `_build_float_toast` 621, `_position_toast` 645, `_build_left` 659, `_build_group_bar` 788, `_build_commands` 1249, `_build_cfg_button` 1315, `_icon_btn`/`_abtn`/`_flight_btn`/`_arrow_*` 1342–1463, `_build_statusbar` 3048, `_build_mode_drop` 1496, `_refresh_logos` 1463, section builders `_sec_*` (1603–2850). **แทบทั้งหมด P** — target extraction Phase 2

### Map (2D/3D) — P (render) / B (target decision)
`_build_map` 857, `_build_map_placeholder` 954, `_on_map_loaded` 971, `_js` 978, `_build_map3d_*` 988–1236, `_toggle_map3d` 1082, `_push_map3d*` 1156–1236, `_jump_map_to` 1054, `_on_mouse_coord`/`_on_mouse_out` 942. **P** (ยกเว้น target push ที่อ้าง route/fence = B)

### Telemetry render — P/B
`_on_telemetry` 8571 (**hub — เขียน `_last_telem`, กระจายไป widget/map/servo/summary; เป็น B/orchestration**), `_update_coord` 8720 (P), `_auto_guided_after_land` 8688 (**S** — set mode after land), `_sync_servo_from_telemetry` 3833

### Selection / Fleet — B (+P)
`_wire_fleet_item` 4144, `_on_fleet_click` 4151, `_on_map_drone_selected` 4177, `_sync_fleet_button` 4183, `_refresh_selection_ui` 4208, `_selected_or_all` 4223, `_select_drone` 8728, `_target_ids` 3374, `_priority_ids` 4276, `_on_card_*` 4227–4235, `_on_drone_renamed` 4235, card lifecycle `_card_connect/disconnect/apply_ip/delete` 5541–5701 (F for connect). Group: `_refresh_group_ui` 4038, `_set_drone_group` 4074, `_select_group` 4113

### Head / Leader — B/F
`_head_change_block_reason` 4283, `_on_head_req` 4308, `_apply_head` 4331 (**F set_leader**), `_update_head_ui` 4359, `_head_watchdog` 4373, `_on_leader_combo` 4390, `_refresh_leader_combo` 8830

### Waypoint planning — B (+P render)
`_sec_waypoint` 1671, `_wp_update_mode_hint` 1803, `_wp_toggle` 6483, `_wp_set_separate` 6512 (**guard: reject เมื่อ swarm active — WIP**), `_wp_plot_target` 6544, route helpers `_wp_all_routes`/`_wp_route_for_key`/`_wp_unique_routes` 6557–6943, `_wp_set_action` 6570, context menu `_wp_action_*`/`_wp_points_menu` 6649–6708, `_on_waypoint_click` 6708, render `_wp_render_points_label`/`_wp_render_status` 6774–6808, `_wp_undo`/`_wp_clear` 6855–6879, conflict `_wp_check_conflicts`/`_wp_block_on_conflicts` 6903–6913. **Pure model อยู่ `core/waypoint_logic.py`**

### Waypoint WAIT (WIP — ห้ามแตะ) — S
`_wp_set_wait` 6588, `_wp_clear_wait` 6620, `_wp_prompt_wait` 6623, `_wp_on_arrived` 7607, `_wp_wait_begin` 7622, `_wp_wait_hold` 7646 (**F hold**), `_wp_wait_valid` 7655, `_wp_wait_invalidate` 7667, `_wp_wait_remaining` 7678, `_wp_wait_tick` 7686, `_wp_wait_render` 7710, `_wp_refresh_mode_availability` 1825, `_wp_swarm_defensive_wave_stop` 1850

### Waypoint execution — F/S (ห้ามแตะช่วงต้น)
`_wp_execute` 7359, `_wp_begin_execute` 7440, `_wp_advance*`/`_wp_finish*` 7482–7607, `_wp_run_action` 6973 (**F servo/hold**), `_wp_require_takeoff` 7064, `_wp_is_airborne`/`_wp_grounded_ids` 7046, `_abort_waypoint_execution` 4817 (**S**), `_on_target_reached` 6360, `_cancel_navigation` 6402 (**S**)

### WAVE — F/S
`_sec` inside waypoint; `_wave_toggle` 1977 (**guard reject when swarm active — WIP**), `_wave_execute` 7182, `_wave_start_next_group` 7242, `_wave_begin_group_route` 7282, `_wave_route_finished` 7311, `_wave_complete` 7330, `_wave_cancel`/`_wave_abort` 2033/2039 (**S**), `_wave_tick` 2077, helpers `_wave_*` 1886–1968

### Swarm control — F/S
`_swarm_start` 3989, `_swarm_stop` 4016, `_swarm_return` 4025, `_swarm_takeoff` 4572, `_await_airborne_then_formup` 4614, `_do_formup` 4663, `_flight_swarm_active` 4685, `_poll_swarm` 6011, `_on_swarm_update` 6020. **Pure logic: `core/swarm_logic.py`**

### Takeoff orchestration — F/S
`_refresh_takeoff_panel` 4436, `_on_panel_takeoff` 4449, `_execute_takeoff_steps` 4466, `_takeoff_one` 4509 (**F**), `_flight_takeoff_response` 4532, `_flight_watch_takeoff` 4549

### RTL — S (ห้ามแตะ)
`_cmd_rtl` 3958, `_card_rtl` 5128, `_staggered_rtl` 4696, `_rtl_core_rejected/_uncertain` 4740/4746, `_rtl_monitor_core` 4752, `_rtl_finish` 4780, `_set_rtl_phase`/`_clear_all_rtl_phase` 4792/4805, `_abort_rtl` 4809, `_rtl_preview_text`/`_update_rtl_preview` 2676/2690

### E-STOP / Emergency — S
`_emergency_menu` 4851, `_on_estop_drone` 4872, `_estop_all` 4886, `_do_estop` 4900 (**F stop_all**)

### Collision — S
`_fleet_positions` 5812, `_collision_positions` 5827, `_collision_watchdog` 5839, `_update_collision_badge` 5905

### RC / manual movement — F
`_multi_move` 5928, `_ordered_fleet` 5932, `_rc_press` 5946, `_rc_release` 5966, `_rc_stop` 5971 (**S**), `_rc_halt` 5975, `_rc_tick` 5990 (**F rc_move**)

### Servo / payload — F
`_servo_*` 3482–3861, `_cmd_servo` 3686, `_run_servo` 3726, `_prime_servo` 3561, `_verify_servo_released` 3794

### Preflight — B/P
`_sec_preflight` 2276, `_preflight_snapshot` 2330, `_preflight_telem` 2391, `_preflight_gate` 2502 (**gate ก่อน takeoff — S-adjacent**), `_preflight_soft_warn` 2556, `_refresh_preflight_ui` 2450, `_open_preflight_test/checklist` 2401/2427, `_sync_preflight_warning` 3233

### Execution summary / flight-progress — P/B (candidate!)
`_summ_set`/`_summ_remove`/`_summ_event` 4927–4947, `_summ_sync_waypoint*` 4957–4968, `_render_summary` 5025, `_flight_snapshot` 5030, `_flight_run_start` 5034, `_flight_is_current` 5059, `_flight_step_active/done/failed` 5062–5072, `_flight_run_cancel` 5077, `_clear_summary` 5082. **Pure model: `core/flight_progress.py` + widget `command_summary.py`**

### Field Tablet — B (LAN bridge)
`_field_push` 7733, `_field_*` 7776–8560, web dispatch `_on_web_command` 7904, `_dispatch_web` 7924, `_web_*` 8007–8442 (**หลายตัวเป็น F: `_web_takeoff`/`_web_goto`/`_web_set_mode`/`_web_hold_all`/`_web_swarm_start`**)

### Geofence — S
`_sec_geofence` 2727, `_fence_toggle`/`_fence_set`/`_fence_apply`/`_fence_clear`/`_fence_send` 6148–6190 (**F set_geofence**), `_cache_area`/`_prefetch_bounds` 6220–6226

### Tactical map — P/B
`_sec_tactical` 2780, `_refresh_tac_btns` 2850, `_set_tac_tool` 2854, `_tac_*` 2880–3030, `_set_draw_tool` 3034

### Timers / watchdogs — B
`_tick_clock` 8959, `_conn_watchdog` 3122, `_head_watchdog` 4373, `_collision_watchdog` 5839, `_rc_tick` 5990, `_wave_tick` 2077, `_wp_wait_tick` 7686, `_poll_swarm` 6011, `_auto_ping_selected` 8810

### Settings / persistence — B
`_collect_settings` 5227, `_apply_settings` 5286, `_save/_export/_load_settings*` 5407–5487, `_autoload_settings` 5466, `_remember_endpoint`/`_forget_endpoint` 5150/5198, `_load_saved_ips` 5209, dup-IP `_find_duplicate_ip`/`_reject_duplicate_ip` 5487/5530

### Logging / misc — P/B
`_log` 8964, `_show_banner`/`_hide_banner` 3252/3275, `_show_toast`/`_hide_toast` 3308/3339, `_field_notify` 3298, `_confirm` 3284, `_guard` 3392, `_safe` 5775, `_run_cmd` 3405, `_on_cmd_result` 3348, `closeEvent` 8968, `keyPressEvent` 1559, `eventFilter` 1550, `resizeEvent` 3343

---

## 5. High-risk shared mutable state (จับตาเป็นพิเศษ)

| state | ทำไมเสี่ยง |
|---|---|
| `_last_telem` | อ่านข้าม subsystem เกือบทั้งหมด (safety/preflight/RTL/waypoint/map/servo). Phase 3 shadow ตัวนี้ |
| `_selected_ids` / `_target_mode` | กำหนดปลายทางของ **ทุก** flight command ผ่าน `_target_ids()` |
| `_swarm_active` | ล็อกโหมด + guard SEPARATE/WAVE (WIP). เขียนจาก `_on_swarm_update` (async) |
| `_flight_run_id`, `_wave_generation`, `_wp_wait_generation`, `_wp_takeoff_generation` | **run/generation guards** กัน stale callback — Phase 5 (Run ID) จะทำให้เป็นทางการ; ห้ามทำพัง |
| `_rtl_active` / `_rtl_pending` / `_rtl_phase` | safety state, เขียนจาก daemon `_rtl_monitor_core` + telemetry |
| `_nav_targets` | เป้าหมายที่สั่งไว้ — ใช้ตัดสิน target-reached |

---

## 6. Extraction candidates สำหรับ Phase 2 (เรียงจาก risk ต่ำสุด)

> **Presentation-only, ไม่มี `self.client.*`, ไม่แตะ WIP** — เลือกตัวแรกเมื่อได้ไฟเขียวเริ่ม Phase 2

1. **Summary/Execution-Flow presenter** — logic ส่วน `_summ_*` + `_render_summary` อ่าน `flight_progress.py` (pure model มีแล้ว) แล้ว format ลง `widgets/command_summary.py`. Format/label ล้วน ๆ. tests: `test_preflight_summary_v2.py`
   *(หมายเหตุ: `_summ_sync_waypoint` แตะ WAIT summary ซึ่งเป็น WIP — แยกเฉพาะส่วน format ที่ไม่ชนได้)*
2. **Fleet presenter** — `_refresh_selection_ui`, `_refresh_group_ui`, `_drone_name`, badge/label formatting (ไม่รวม `_on_fleet_click` ที่เปลี่ยน selection state). tests: `test_ui_selection.py`
3. **Map render adapter** — `render_drone/waypoint/route/target` helpers (`_push_map3d*`, marker/polyline JS) แยกจาก "ตัดสินใจว่าจะบินไปไหน". tests: `test_map3d.py`, `test_tactical_map.py`
4. **Pill / toast / banner formatting** — `_pill`, `_show_toast`, `_show_banner` (pure Qt widget helpers)

**ห้ามเริ่มจาก:** RTL, E-STOP, WAVE execution, Waypoint runtime, Takeoff, WAIT, failsafe, servo, RC — (F/S)

---

## 7. Discrepancies / notes (MD vs source)
- ยังไม่พบ discrepancy ระหว่าง plan docs กับ source ณ ตอนทำ inventory
- `controllers/` dir **ยังไม่มี** — Phase 2 จะสร้าง `frontend/swarmgod_gui/controllers/`
- pure-logic modules ที่มีแล้ว: `core/waypoint_logic.py`, `core/swarm_logic.py`, `core/flight_progress.py`, `core/preflight.py` — extraction ควรต่อยอดจากของเดิม ไม่สร้างซ้ำ
