"""
Mock Grid Engine — stands in for Person 1's service.

Implements the same interface Person 1's real service will expose:
  - get_state() -> GridState
  - validate(actions: list[ProposedAction]) -> list[ValidationResult]
  - inject_fault(feeder_id, fault_type) -> None

This lets you build and demo the entire orchestrator + dashboard tonight
without Person 1's code existing at all. Swap it out later by pointing
GRID_SOURCE=live at their real HTTP service (see grid_client.py).
"""

from __future__ import annotations

from datetime import datetime

from schemas.contracts import (
    AssetState,
    GridState,
    NetworkState,
    Prediction,
    ProposedAction,
    ValidationResult,
)

# Starting topology: enough variety to exercise every dashboard panel.
_INITIAL_ASSETS = [
    AssetState(asset_id="hosp_battery", asset_type="battery", current_load_kw=10,
               current_gen_kw=0, soc_percent=85, min_reserve_percent=40, online=True),
    AssetState(asset_id="hospital", asset_type="hospital", current_load_kw=140,
               current_gen_kw=0, online=True),
    AssetState(asset_id="academic1", asset_type="academic", current_load_kw=90,
               current_gen_kw=0, online=True),
    AssetState(asset_id="factory1", asset_type="factory", current_load_kw=160,
               current_gen_kw=0, online=True),
    AssetState(asset_id="solar1", asset_type="solar", current_load_kw=0,
               current_gen_kw=120, online=True),
    AssetState(asset_id="ev1", asset_type="ev", current_load_kw=40,
               current_gen_kw=0, online=True),
    AssetState(asset_id="utility", asset_type="utility", current_load_kw=0,
               current_gen_kw=0, online=True),
]

_FEEDER_MEMBERSHIP = {
    "F1": ["hospital", "hosp_battery", "utility"],
    "F2": ["academic1", "factory1", "solar1", "ev1"],
}

_MAX_FEEDER_KW = 400.0  # loading_percent = net_kw / this * 100


class MockGridEngine:
    def __init__(self) -> None:
        self.tick_count = 0
        self.assets: dict[str, AssetState] = {a.asset_id: a.model_copy() for a in _INITIAL_ASSETS}
        self.active_scenario: str | None = None
        self.scenario_tick_start: int | None = None
        self.active_faults: list[str] = []
        self.islands: list[list[str]] = []

    # ---- scenario injection -------------------------------------------------

    def inject_fault(self, feeder_id: str, fault_type: str) -> None:
        self.active_scenario = fault_type
        self.scenario_tick_start = self.tick_count

        if fault_type == "solar_drop":
            self.assets["solar1"].current_gen_kw = 20
        elif fault_type == "demand_spike":
            self.assets["academic1"].current_load_kw += 60
            self.assets["factory1"].current_load_kw += 40
        elif fault_type == "feeder_overload":
            self.assets["factory1"].current_load_kw += 150
        elif fault_type == "battery_failure":
            self.assets["hosp_battery"].online = False
        elif fault_type == "grid_outage":
            self.assets["utility"].online = False
            self.active_faults.append("F1")
            self.islands = [
                _FEEDER_MEMBERSHIP["F1"],
                _FEEDER_MEMBERSHIP["F2"],
            ]
        elif fault_type == "line_fault":
            self.active_faults.append(feeder_id)

    def reset(self) -> None:
        self.__init__()  # noqa: PLW0108 -- deliberate full reset for demo control

    # ---- state reporting ------------------------------------------------

    def _feeder_loading(self, feeder_id: str) -> float:
        members = _FEEDER_MEMBERSHIP[feeder_id]
        net_kw = sum(
            self.assets[a].current_load_kw - self.assets[a].current_gen_kw
            for a in members
            if self.assets[a].online
        )
        return round(max(net_kw, 0) / _MAX_FEEDER_KW * 100, 1)

    def get_state(self) -> GridState:
        self.tick_count += 1

        feeders = []
        predictions = []
        for feeder_id in _FEEDER_MEMBERSHIP:
            loading = self._feeder_loading(feeder_id)
            if feeder_id in self.active_faults:
                status = "faulted"
            elif self.islands:
                status = "islanded"
            elif loading >= 100:
                status = "overloaded"
            elif loading >= 80:
                status = "warning"
            else:
                status = "normal"

            feeders.append(
                NetworkState(
                    feeder_id=feeder_id,
                    loading_percent=loading,
                    connected_assets=_FEEDER_MEMBERSHIP[feeder_id],
                    status=status,
                )
            )

            if loading >= 80 and status != "faulted":
                eta = max(240 - (self.tick_count - (self.scenario_tick_start or self.tick_count)) * 15, 0)
                predictions.append(
                    Prediction(feeder_id=feeder_id, predicted_overload=True, eta_seconds=eta, confidence=0.9)
                )

        return GridState(
            timestamp=datetime.utcnow(),
            assets=list(self.assets.values()),
            feeders=feeders,
            predictions=predictions,
            active_faults=self.active_faults,
            islands=self.islands,
        )

    # ---- validation -------------------------------------------------------

    def validate(self, actions: list[ProposedAction]) -> list[ValidationResult]:
        results = []
        for action in actions:
            asset = self.assets.get(action.asset_id)
            if asset is None:
                results.append(ValidationResult(action_id=action.action_id, feasible=False,
                                                  reason="unknown asset"))
                continue

            # Enforce the hospital battery's emergency reserve floor — the one
            # constraint the demo explicitly wants to show being respected.
            if asset.asset_type == "battery" and asset.min_reserve_percent is not None:
                kwh_equivalent_percent = action.kw_amount / 10  # toy conversion for the demo
                projected_soc = (asset.soc_percent or 100) - kwh_equivalent_percent
                if projected_soc < asset.min_reserve_percent:
                    results.append(
                        ValidationResult(
                            action_id=action.action_id,
                            feasible=False,
                            reason="would breach emergency reserve constraint",
                        )
                    )
                    continue

            if action.kw_amount <= 0:
                results.append(ValidationResult(action_id=action.action_id, feasible=False,
                                                  reason="non-positive kw amount"))
                continue

            results.append(ValidationResult(action_id=action.action_id, feasible=True))

            # Apply the action's effect to mock state so subsequent ticks reflect it.
            if action.action_type == "battery_discharge" and asset.soc_percent is not None:
                asset.soc_percent = max(asset.soc_percent - action.kw_amount / 10, 0)
            else:
                asset.current_load_kw = max(asset.current_load_kw - action.kw_amount, 0)

        return results
