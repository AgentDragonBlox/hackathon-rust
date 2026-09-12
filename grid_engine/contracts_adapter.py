"""
contracts_adapter.py

Translates between grid_engine's internal pandapower network/results and
the shared `shared/contracts.py` Pydantic models that Person 2 and Person
3 actually consume (GridState, AssetState, NetworkState, ProposedAction,
ValidationResult). Nothing in grid.py/simulation.py/forecasting.py/
scenarios.py needs to know these shapes exist -- this is the one place
that boundary is crossed, so the rest of the module stays pandapower-only
and easy to test without pydantic in the loop.

-------------------------------------------------------------------------
ASSET ID MAPPING (a real integration decision -- flagged, not silent)
-------------------------------------------------------------------------

shared/contracts.py's AssetState.asset_id is just a free-form `str` --
there's no team-agreed ID scheme yet. Person 2's INTEGRATION.md verified
example used "hosp-1", "acad-1", "ev-1", "batt-1" as illustrative IDs, so
this file adopts that exact scheme (plus "fac-1" and "solar-1" for the two
assets their example didn't cover) so the two services speak the same
asset IDs out of the box. If the team wants a different scheme, this
mapping is the one place to change it.

-------------------------------------------------------------------------
KNOWN GAPS (see grid_engine/INTEGRATION.md for the full writeup)
-------------------------------------------------------------------------

- AssetState.asset_type has no slot for a generic office/admin facility
  load -- the Literal enum is
  ("hospital","academic","factory","battery","solar","ev","utility").
  facility_load is mapped to "factory" here as the closest fit, which is
  semantically off (factory implies industrial/production load, which is
  also what Person 2's "production_flex" offer type assumes). Flagged for
  the team; not changed unilaterally since it's a shared enum.
- NetworkState is feeder-only -- there's no field for transformer loading,
  which grid_engine tracks as a real, separate constraint (see grid.py).
  Represented here as a synthetic feeder_id="TRANSFORMER" entry so the
  information isn't silently dropped from GridState. This is a workaround,
  not a real feeder -- flagged for the team to decide on a proper field.
- GridState.predictions is always [] from build_grid_state() here. Real
  predictions need a rolling buffer of recent history (forecasting.py's
  `history` parameter) that persists across API calls -- there's no
  server-side live-state loop wired into api.py yet (that's Milestone 6+
  territory, same shape of gap as Person 2's own "statefulness gap" note
  in their INTEGRATION.md). build_grid_state() takes an optional
  `history` argument for when a caller does have one available.
"""

from datetime import datetime, timezone

import pandapower as pp

from shared.contracts import AssetState, GridState, NetworkState, Prediction

# --- Asset ID <-> internal (pandapower element table, name) mapping --------

ASSET_ID_TO_INTERNAL = {
    "hosp-1": ("load", "hospital_load"),
    "acad-1": ("load", "academic_load"),
    "ev-1": ("load", "ev_load"),
    "fac-1": ("load", "facility_load"),
    "solar-1": ("sgen", "solar"),
    "batt-1": ("storage", "hospital_bess"),
}
INTERNAL_TO_ASSET_ID = {internal_name: asset_id for asset_id, (_, internal_name) in ASSET_ID_TO_INTERNAL.items()}

ASSET_TYPE_BY_INTERNAL_NAME = {
    "hospital_load": "hospital",
    "academic_load": "academic",
    "ev_load": "ev",
    "facility_load": "factory",  # closest fit in the shared enum -- see KNOWN GAPS above
    "solar": "solar",
    "hospital_bess": "battery",
}

# Which asset_ids sit on each feeder -- mirrors forecasting.py's
# CONSTRAINT_RELIEF_BUS grouping, expressed in asset_id terms for GridState.
FEEDER_ASSET_IDS = {
    "F1": ["hosp-1", "batt-1"],
    "F2": ["acad-1", "ev-1", "solar-1"],
    "F3": ["fac-1"],
}
TRANSFORMER_ASSET_IDS = ["hosp-1", "batt-1", "acad-1", "ev-1", "solar-1", "fac-1"]

# Loading thresholds for NetworkState.status. ASSUMED (not in contracts.py):
# roughly matches the "F2=91% -> warning" framing from the project brief.
WARNING_LOADING_PCT = 90.0
OVERLOAD_LOADING_PCT = 100.0


def _network_status(loading_pct: float) -> str:
    if loading_pct > OVERLOAD_LOADING_PCT:
        return "overloaded"
    if loading_pct >= WARNING_LOADING_PCT:
        return "warning"
    return "normal"


def _safe_kw(value, default: float = 0.0) -> float:
    """
    NaN-safe float conversion for a power-flow result cell. NaN shows up
    here in exactly one real scenario today: Milestone 6's grid_outage
    fault disables the ext_grid, leaving the network with no reference
    bus -- pandapower can't solve AC power flow at all and clears every
    res_* table to NaN (confirmed by direct experiment) rather than
    leaving stale values. Reporting 0.0 in that case is not a fabricated
    number -- it's the physically correct statement that nothing can
    flow without a slack bus. GridState.active_faults / islands are what
    tell the caller *why* it's 0, not this value.
    """
    v = float(value)
    return v if v == v else default  # NaN != NaN


def build_asset_states(net: pp.pandapowerNet) -> list[AssetState]:
    """
    Build the AssetState list from a power-flow-solved network. Requires
    net.res_load / net.res_sgen / net.res_storage to be populated (i.e.
    run_power_flow(net) must have been called already).
    """
    assets = []

    for idx, row in net.load.iterrows():
        asset_id = INTERNAL_TO_ASSET_ID.get(row["name"])
        if asset_id is None:
            continue
        assets.append(AssetState(
            asset_id=asset_id,
            asset_type=ASSET_TYPE_BY_INTERNAL_NAME[row["name"]],
            current_load_kw=_safe_kw(net.res_load.loc[idx, "p_mw"] * 1000.0),
            current_gen_kw=0.0,
            online=bool(row["in_service"]),
        ))

    for idx, row in net.sgen.iterrows():
        asset_id = INTERNAL_TO_ASSET_ID.get(row["name"])
        if asset_id is None:
            continue
        assets.append(AssetState(
            asset_id=asset_id,
            asset_type=ASSET_TYPE_BY_INTERNAL_NAME[row["name"]],
            current_load_kw=0.0,
            current_gen_kw=_safe_kw(net.res_sgen.loc[idx, "p_mw"] * 1000.0),
            online=bool(row["in_service"]),
        ))

    for idx, row in net.storage.iterrows():
        asset_id = INTERNAL_TO_ASSET_ID.get(row["name"])
        if asset_id is None:
            continue
        p_mw = _safe_kw(net.res_storage.loc[idx, "p_mw"])  # NaN -> 0.0, see _safe_kw (still in MW here)
        # pandapower storage sign convention matches a load: positive p_mw
        # = charging (consuming), negative p_mw = discharging (supplying).
        load_kw = p_mw * 1000.0 if p_mw > 0 else 0.0
        gen_kw = -p_mw * 1000.0 if p_mw < 0 else 0.0
        max_e_mwh = float(row["max_e_mwh"])
        min_e_mwh = float(row["min_e_mwh"])
        min_reserve_percent = (min_e_mwh / max_e_mwh * 100.0) if max_e_mwh else None
        assets.append(AssetState(
            asset_id=asset_id,
            asset_type=ASSET_TYPE_BY_INTERNAL_NAME[row["name"]],
            current_load_kw=load_kw,
            current_gen_kw=gen_kw,
            soc_percent=float(row["soc_percent"]) if row["soc_percent"] == row["soc_percent"] else None,  # NaN-safe
            min_reserve_percent=min_reserve_percent,
            online=bool(row["in_service"]),
        ))

    return assets


def _line_status(net: pp.pandapowerNet, line_idx, loading_pct: float) -> str:
    """
    Milestone 6: a line's status now has two more real states beyond
    normal/warning/overloaded (see contracts.py's NetworkState.status
    enum, which already had "faulted"/"islanded" defined -- unused until
    now). Not in_service -> this line was directly taken out by a
    line_fault. in_service but NaN loading -> it's carrying no power
    because something upstream (grid_outage) cut off its reference bus,
    not because the line itself failed.
    """
    if not bool(net.line.loc[line_idx, "in_service"]):
        return "faulted"
    if loading_pct != loading_pct:  # NaN
        return "islanded"
    return _network_status(loading_pct)


def build_network_states(net: pp.pandapowerNet) -> list[NetworkState]:
    """Build the NetworkState list (feeders + the synthetic TRANSFORMER
    entry) from a power-flow-solved network."""
    networks = []
    for line_idx, row in net.line.iterrows():
        feeder_id = row["name"]
        raw_loading_pct = float(net.res_line.loc[line_idx, "loading_percent"])
        networks.append(NetworkState(
            feeder_id=feeder_id,
            loading_percent=_safe_kw(raw_loading_pct),  # 0.0 for a de-energized line -- see _safe_kw
            connected_assets=FEEDER_ASSET_IDS.get(feeder_id, []),
            status=_line_status(net, line_idx, raw_loading_pct),
        ))

    trafo_loading_pct = float(net.res_trafo["loading_percent"].iloc[0])
    trafo_status = "islanded" if trafo_loading_pct != trafo_loading_pct else _network_status(trafo_loading_pct)
    networks.append(NetworkState(
        feeder_id="TRANSFORMER",  # synthetic -- see KNOWN GAPS above
        loading_percent=_safe_kw(trafo_loading_pct),
        connected_assets=TRANSFORMER_ASSET_IDS,
        status=trafo_status,
    ))
    return networks


def build_grid_state(
    net: pp.pandapowerNet,
    predictions: list[Prediction] | None = None,
    active_faults: list[str] | None = None,
    islands: list[list[str]] | None = None,
) -> GridState:
    """
    Build a full GridState from a power-flow-solved (or, for a
    grid_outage fault, power-flow-attempted -- see _safe_kw) network.

    `predictions` defaults to [] -- see KNOWN GAPS above for why real
    predictions aren't wired in here automatically. A caller that does
    have recent history can compute one with
    forecasting.detect_predicted_violation() and pass it in as:

        Prediction(feeder_id=..., predicted_overload=True,
                    eta_seconds=result["time_to_violation_min"] * 60,
                    confidence=<your own call -- contracts.py doesn't
                    define how confidence should be computed>)

    `active_faults` / `islands` default to [] for a plain, fault-free
    network. Milestone 6's grid_engine/faults.py is the one place that
    computes real values for these (a live fault registry for
    active_faults, and real topology-based connected-components analysis
    for islands, via faults.compute_islands) -- this function just wires
    whatever it's given into the GridState shape, same as it already does
    for predictions.
    """
    return GridState(
        timestamp=datetime.now(timezone.utc),
        assets=build_asset_states(net),
        feeders=build_network_states(net),
        predictions=predictions or [],
        active_faults=active_faults or [],
        islands=islands or [],
    )
