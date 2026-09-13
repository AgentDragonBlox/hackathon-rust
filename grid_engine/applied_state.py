"""
applied_state.py

Milestone 7 (physical feedback loop -- closing the gap flagged in
README.md sections 21/22: "settlement does not commit actions into the
replay network, update battery SOC through dispatch, or control
equipment"): tracks what part of the live grid's state must persist
ACROSS ticks beyond what api.py's `_rebuild_live_state()` already
reconstructs from `_baseline_net` + the current profile row + active
faults every time it runs.

Two kinds of state live here:

  - Battery energy (in MWh, matching pandapower's own storage units).
    A discharge settled on tick N must still be reflected in tick N+1's
    SOC -- energy doesn't come back just because the profile row changed
    to a new hour. This persists until an explicit reset (see reset()).

  - Which settled action_ids have already been applied, so the same
    settlement can never be double-applied to the live network. Rust's
    /agents/settle does not enforce settlement idempotency by itself
    (README section 8: "Calling settlement twice can create new trade
    IDs") -- this is where that protection actually lives for the one
    thing that matters physically: the live grid's `_net`.

Deliberately NOT persisted here: load curtailment. A curtailment action
(load_reduction / hvac_reduction / ev_delay / production_flex) is a
demand response to THAT hour's specific stress. api.py applies it
directly to the live `_net` for the current tick (see post_apply_actions),
but the next tick's `_rebuild_live_state()` starts from a fresh recorded
profile row -- reapplying a stale kW cut to a different hour's reading
would misrepresent the input data, not model a real device behavior.
Only battery energy is physically cumulative across time; curtailment's
effect ("this hour's served demand was lower") is captured by the
metrics module at the moment it happens (see experiments/metrics.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AppliedState:
    # pandapower storage element name (e.g. "hospital_bess") -> current
    # usable energy in MWh, matching net.storage's own max_e_mwh/min_e_mwh units.
    battery_energy_mwh: dict[str, float] = field(default_factory=dict)

    # ProposedAction.action_id values that have already been committed to
    # the live network -- reapplying one is a no-op, not a second dispatch.
    applied_action_ids: set[str] = field(default_factory=set)

    # Audit trail for tests/metrics/dashboard: one entry per action that
    # was actually applied (not the no-ops / rejects). Newest last.
    log: list[dict] = field(default_factory=list)

    def reset(self) -> None:
        """Full reset -- called on a replay restart or a scenario reset so
        repeated demo runs start from the same physical state every time
        (see api.py's control_replay('restart') and post_clear_all_faults())."""
        self.battery_energy_mwh.clear()
        self.applied_action_ids.clear()
        self.log.clear()
