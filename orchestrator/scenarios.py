"""
Maps the six demo scenario buttons to a default feeder and fault type.
Kept separate from main.py so the mapping is easy to tweak without
touching route/loop logic.
"""

from __future__ import annotations

SCENARIOS: dict[str, dict[str, str]] = {
    "solar_drop": {"feeder_id": "F2", "fault_type": "solar_drop"},
    "demand_spike": {"feeder_id": "F2", "fault_type": "demand_spike"},
    "feeder_overload": {"feeder_id": "F2", "fault_type": "feeder_overload"},
    "battery_failure": {"feeder_id": "F1", "fault_type": "battery_failure"},
    "grid_outage": {"feeder_id": "F1", "fault_type": "grid_outage"},
    "line_fault": {"feeder_id": "F2", "fault_type": "line_fault"},
}
