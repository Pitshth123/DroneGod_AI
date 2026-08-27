"""
Unit tests สำหรับ core/swarm_logic.py (สเปกข้อ 9 — Self-Verification)

รันแบบ headless ได้ (ไม่ต้องมี Qt / gRPC / core):
    cd frontend
    python -m pytest tests/test_swarm_logic.py -v
หรือรันตรง ๆ ด้วย unittest:
    python -m unittest tests.test_swarm_logic -v

ครอบคลุม:
  - Auto-Reassign Head (spec 1)
  - Take off All / Sequential + ความสูงต่อลำ (spec 2)
  - Swarm take off แนบ Form-up (spec 3)
  - Collision-Avoidance movement order (spec 7)
  - Command Summary (spec 6)
"""
import os
import sys
import unittest

# ให้ import แพ็กเกจ swarmgod_gui ได้ไม่ว่ารันจากที่ไหน
_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.dirname(_HERE)
if _FRONTEND not in sys.path:
    sys.path.insert(0, _FRONTEND)

from swarmgod_gui.core import swarm_logic as SL  # noqa: E402


# ─────────────────────────────────────────────────────────────
#  spec 1 — HEAD / AUTO-REASSIGN
# ─────────────────────────────────────────────────────────────
class TestHead(unittest.TestCase):
    def test_choose_head_default_lowest_id(self):
        self.assertEqual(SL.choose_head([3, 1, 2]), 1)

    def test_choose_head_respects_preferred_if_connected(self):
        self.assertEqual(SL.choose_head([1, 2, 3], preferred=2), 2)

    def test_choose_head_ignores_preferred_if_disconnected(self):
        # preferred=9 ไม่อยู่ในชุด → ตกไปตัวแรกตาม priority
        self.assertEqual(SL.choose_head([1, 2, 3], preferred=9), 1)

    def test_choose_head_empty(self):
        self.assertEqual(SL.choose_head([]), 0)

    def test_choose_head_priority_override(self):
        # priority บอกให้ 3 มาก่อน
        self.assertEqual(SL.choose_head([1, 2, 3], priority=[3, 2, 1]), 3)

    def test_head_still_connected_no_change(self):
        new, changed = SL.next_head_after_loss(1, [1, 2, 3])
        self.assertEqual(new, 1)
        self.assertFalse(changed)

    def test_head_lost_promotes_next(self):
        # หัว=1 หลุด เหลือ [2,3,4,5] → เลื่อนลำที่ 2 (id 2) ขึ้นเป็นหัว
        new, changed = SL.next_head_after_loss(1, [2, 3, 4, 5])
        self.assertEqual(new, 2)
        self.assertTrue(changed)

    def test_head_lost_promotes_next_when_head_was_middle(self):
        # หัว=3 (ตั้งเอง) หลุด เหลือ [1,2,4,5] → ตัวแรกที่ยังต่ออยู่ = 1
        new, changed = SL.next_head_after_loss(3, [1, 2, 4, 5])
        self.assertEqual(new, 1)
        self.assertTrue(changed)

    def test_all_lost(self):
        new, changed = SL.next_head_after_loss(1, [])
        self.assertEqual(new, 0)
        self.assertTrue(changed)

    def test_chained_failover(self):
        # จำลองหัวหลุดต่อเนื่อง: 1 หาย → 2, แล้ว 2 หาย → 3
        head = 1
        head, _ = SL.next_head_after_loss(head, [2, 3, 4, 5])
        self.assertEqual(head, 2)
        head, _ = SL.next_head_after_loss(head, [3, 4, 5])
        self.assertEqual(head, 3)


# ─────────────────────────────────────────────────────────────
#  spec 2 — TAKE OFF PLANNING
# ─────────────────────────────────────────────────────────────
class TestTakeoff(unittest.TestCase):
    def test_all_mode_same_order(self):
        steps = SL.plan_takeoff("all", [1, 2, 3, 4, 5], head_id=1)
        self.assertTrue(all(s.order == 0 for s in steps))
        self.assertEqual(len(steps), 5)

    def test_all_mode_default_alt(self):
        steps = SL.plan_takeoff("all", [1, 2], default_alt=20)
        self.assertTrue(all(s.alt == 20 for s in steps))

    def test_sequential_head_first(self):
        steps = SL.plan_takeoff("sequential", [1, 2, 3, 4, 5], head_id=3)
        # หัว (3) ต้องได้ order 0
        self.assertEqual(steps[0].drone_id, 3)
        self.assertEqual(steps[0].order, 0)
        # order ต้องไล่ 0,1,2,3,4
        self.assertEqual([s.order for s in steps], [0, 1, 2, 3, 4])

    def test_sequential_followers_after_head(self):
        steps = SL.plan_takeoff("sequential", [5, 4, 3, 2, 1], head_id=1)
        ids = [s.drone_id for s in steps]
        self.assertEqual(ids[0], 1)          # head first
        self.assertEqual(ids[1:], [2, 3, 4, 5])  # เรียง id ต่อ

    def test_per_drone_altitude(self):
        alts = {1: 30, 2: 15}
        steps = SL.plan_takeoff("sequential", [1, 2, 3], head_id=1,
                                default_alt=20, alts=alts)
        by_id = {s.drone_id: s.alt for s in steps}
        self.assertEqual(by_id[1], 30)
        self.assertEqual(by_id[2], 15)
        self.assertEqual(by_id[3], 20)       # ไม่ระบุ → default 20

    def test_invalid_alt_falls_back_to_default(self):
        steps = SL.plan_takeoff("all", [1], alts={1: 0}, default_alt=20)
        self.assertEqual(steps[0].alt, 20)

    def test_empty_drones(self):
        self.assertEqual(SL.plan_takeoff("all", []), [])

    def test_takeoffstep_unpack(self):
        step = SL.plan_takeoff("all", [7], default_alt=25)[0]
        did, alt = step
        self.assertEqual(did, 7)
        self.assertEqual(alt, 25)


# ─────────────────────────────────────────────────────────────
#  spec 3 — SWARM TAKE OFF = FORM-UP + TAKEOFF
# ─────────────────────────────────────────────────────────────
class TestSwarmTakeoff(unittest.TestCase):
    def test_first_op_is_form_up(self):
        ops = SL.build_swarm_takeoff_ops([1, 2, 3], head_id=1,
                                         spacing=12, formation=1)
        self.assertEqual(ops[0].kind, "form_up")
        self.assertEqual(ops[0].payload["spacing"], 12)
        self.assertEqual(ops[0].payload["formation"], 1)

    def test_takeoff_ops_follow(self):
        ops = SL.build_swarm_takeoff_ops([1, 2, 3], head_id=1, mode="sequential")
        kinds = [o.kind for o in ops]
        self.assertEqual(kinds[0], "form_up")
        self.assertEqual(kinds[1:], ["takeoff", "takeoff", "takeoff"])
        # หัวขึ้นก่อน
        self.assertEqual(ops[1].payload["drone_id"], 1)


# ─────────────────────────────────────────────────────────────
#  spec 7 — COLLISION-AVOIDANCE MOVEMENT ORDER
# ─────────────────────────────────────────────────────────────
class TestMovementOrder(unittest.TestCase):
    def setUp(self):
        # 5 ลำเรียงหน้ากระดาน ห่างกันลำละ ~3-4m (x=east). Leader=1 ซ้ายสุด
        # (y เท่ากันหมด)
        self.line = {
            1: (0.0, 0.0),
            2: (3.0, 0.0),
            3: (6.0, 0.0),
            4: (9.0, 0.0),
            5: (12.0, 0.0),
        }

    def test_move_right_rightmost_first(self):
        # ไปขวา → ขวาสุด(5) ก่อน ไล่มาซ้าย(1) : 5,4,3,2,1
        order = SL.movement_order(self.line, "RIGHT")
        self.assertEqual(order, [5, 4, 3, 2, 1])

    def test_move_left_leftmost_first(self):
        # ไปซ้าย → ซ้ายสุด(1) ก่อน ไล่ไปขวา(5) : 1,2,3,4,5
        order = SL.movement_order(self.line, "LEFT")
        self.assertEqual(order, [1, 2, 3, 4, 5])

    def test_leader_left_moving_right_does_not_lead(self):
        # เคสปัญหาในสเปก: Leader ซ้ายสุด สั่งไปขวา — leader (1) ต้องขยับ "ท้ายสุด"
        order = SL.movement_order(self.line, "RIGHT")
        self.assertEqual(order[-1], 1)   # leader ขยับหลังสุด → ไม่ชนลำขวา

    def test_move_forward_frontmost_first(self):
        col = {1: (0.0, 0.0), 2: (0.0, 5.0), 3: (0.0, 10.0)}
        order = SL.movement_order(col, "FWD")
        self.assertEqual(order, [3, 2, 1])   # เหนือสุด (y มากสุด) ก่อน

    def test_move_back_rearmost_first(self):
        col = {1: (0.0, 0.0), 2: (0.0, 5.0), 3: (0.0, 10.0)}
        order = SL.movement_order(col, "BWD")
        self.assertEqual(order, [1, 2, 3])   # ใต้สุด (y น้อยสุด) ก่อน

    def test_alias_directions(self):
        self.assertEqual(SL.movement_order(self.line, "east"),
                         SL.movement_order(self.line, "RIGHT"))
        self.assertEqual(SL.movement_order(self.line, "FORWARD"),
                         SL.movement_order(self.line, "FWD"))

    def test_vertical_direction_stable_id_order(self):
        # UP/DOWN ไม่จัดคิวแนวราบ → เรียงตาม id
        self.assertEqual(SL.movement_order(self.line, "UP"), [1, 2, 3, 4, 5])

    def test_unknown_direction_stable(self):
        self.assertEqual(SL.movement_order(self.line, "sideways"), [1, 2, 3, 4, 5])

    def test_diagonal_positions_right(self):
        # ตำแหน่งไม่เรียงเป๊ะ — ยังต้องเรียงตาม east มาก→น้อย
        pos = {1: (1.0, 5.0), 2: (8.0, 2.0), 3: (4.0, 9.0)}
        self.assertEqual(SL.movement_order(pos, "RIGHT"), [2, 3, 1])

    def test_latlon_coordinates(self):
        # ใช้ lat/lon ดิบ (x=lon, y=lat) — ลำดับต้องถูกเพราะ monotonic
        pos = {1: (100.000, 14.0), 2: (100.003, 14.0), 3: (100.006, 14.0)}
        self.assertEqual(SL.movement_order(pos, "RIGHT"), [3, 2, 1])


# ─────────────────────────────────────────────────────────────
#  spec 6 — COMMAND SUMMARY
# ─────────────────────────────────────────────────────────────
class TestCommandSummary(unittest.TestCase):
    def test_set_and_rows(self):
        cs = SL.CommandSummary()
        cs.set("head", "HEAD", "Drone 1")
        cs.set("mode", "TAKEOFF", "Sequential")
        self.assertEqual(cs.rows(), [("HEAD", "Drone 1"), ("TAKEOFF", "Sequential")])

    def test_set_same_key_updates_in_place(self):
        cs = SL.CommandSummary()
        cs.set("head", "HEAD", "Drone 1")
        cs.set("head", "HEAD", "Drone 2")
        self.assertEqual(len(cs), 1)
        self.assertEqual(cs.rows(), [("HEAD", "Drone 2")])

    def test_order_preserved_on_update(self):
        cs = SL.CommandSummary()
        cs.set("a", "A", "1")
        cs.set("b", "B", "2")
        cs.set("a", "A", "9")   # อัปเดต a ต้องไม่ย้ายไปท้าย
        self.assertEqual([r[0] for r in cs.rows()], ["A", "B"])

    def test_remove_and_clear(self):
        cs = SL.CommandSummary()
        cs.set("a", "A", "1")
        cs.set("b", "B", "2")
        cs.remove("a")
        self.assertEqual(cs.rows(), [("B", "2")])
        cs.clear()
        self.assertEqual(cs.rows(), [])
        self.assertEqual(len(cs), 0)

    def test_as_text(self):
        cs = SL.CommandSummary()
        cs.set("head", "HEAD", "Drone 1")
        cs.set("alt", "ALT", "20 m")
        self.assertEqual(cs.as_text(), "HEAD: Drone 1\nALT: 20 m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
