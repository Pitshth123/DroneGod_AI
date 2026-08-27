"""Presentation-only helpers for fleet names, selection, counts, and groups.

The presenter receives small snapshots/adapters from ``GroundStation``.  It has
no Qt import, command client, target-selection authority, or flight state.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class GroupChipPresentation:
    text: str
    tooltip: str
    enabled: bool


class FleetPresenter:
    """Format and paint fleet presentation without owning fleet state."""

    @staticmethod
    def normalize_display_name(name, max_length=32):
        return " ".join(str(name or "").split())[:int(max_length)]

    @staticmethod
    def display_name(drone_id, *candidates):
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return "Drone %d" % int(drone_id)

    @staticmethod
    def fleet_count_text(count):
        return "fleet %d" % int(count)

    @staticmethod
    def selection_summary(selected_ids):
        ids = sorted(int(drone_id) for drone_id in selected_ids)
        if not ids:
            return None
        return "%d ลำ · %s" % (
            len(ids), ", ".join("D%d" % drone_id for drone_id in ids))

    @staticmethod
    def group_chip(group, count):
        group, count = int(group), int(count)
        return GroupChipPresentation(
            text=("%d·%d" % (group, count)) if count else str(group),
            tooltip=(
                "Group %d · %d ลำ\n"
                "คลิก: เลือกกลุ่ม · Ctrl+คลิก: เพิ่มกลุ่ม · คลิกขวา: กำหนดกลุ่ม"
            ) % (group, count),
            enabled=count > 0,
        )

    def render_fleet_count(self, label, count):
        if label is not None:
            label.setText(self.fleet_count_text(count))

    def render_selection(self, fleet_items, selected_ids, takeoff_panel=None):
        """Paint a read-only selection snapshot; never mutate selected_ids."""
        selected = frozenset(int(drone_id) for drone_id in selected_ids)
        for drone_id, item in fleet_items.items():
            item.set_selected(int(drone_id) in selected)
        if takeoff_panel is not None:
            takeoff_panel.set_selection_text(selected)
        return self.selection_summary(selected)

    def render_groups(self, fleet_items, group_of, group_chips=None):
        """Paint card badges and group chips from registry snapshots."""
        for drone_id, item in fleet_items.items():
            item.set_group(group_of.get(int(drone_id), 0))
        if group_chips is None:
            return
        for group, chip in group_chips.items():
            count = sum(
                1 for drone_id in fleet_items
                if group_of.get(int(drone_id)) == int(group)
            )
            view = self.group_chip(group, count)
            chip.setText(view.text)
            chip.setToolTip(view.tooltip)
            chip.setEnabled(view.enabled)

