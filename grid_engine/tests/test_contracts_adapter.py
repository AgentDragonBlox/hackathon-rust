"""
Milestone 5 tests for grid_engine.contracts_adapter -- building shared/
contracts.py GridState/AssetState/NetworkState objects from a solved
pandapower network.
"""

import pytest

from grid_engine.contracts_adapter import (
    ASSET_ID_TO_INTERNAL,
    INTERNAL_TO_ASSET_ID,
    build_asset_states,
    build_grid_state,
    build_network_states,
)
from grid_engine.grid import build_campus_network, run_power_flow
from shared.contracts import AssetState, GridState, NetworkState


@pytest.fixture
def solved_net():
    net = build_campus_network()
    run_power_flow(net)
    return net


def test_asset_id_mapping_is_bijective():
    """Every asset_id maps to a unique internal name and back again."""
    assert len(ASSET_ID_TO_INTERNAL) == len(INTERNAL_TO_ASSET_ID)
    for asset_id, (_, internal_name) in ASSET_ID_TO_INTERNAL.items():
        assert INTERNAL_TO_ASSET_ID[internal_name] == asset_id


def test_build_asset_states_covers_all_six_assets(solved_net):
    assets = build_asset_states(solved_net)
    assert len(assets) == 6
    ids = {a.asset_id for a in assets}
    assert ids == set(ASSET_ID_TO_INTERNAL.keys())
    for asset in assets:
        assert isinstance(asset, AssetState)


def test_build_asset_states_load_vs_gen_kw_are_mutually_exclusive_per_asset(solved_net):
    """A pure load asset should have current_gen_kw == 0 and vice versa for
    the pure generator (solar)."""
    assets = {a.asset_id: a for a in build_asset_states(solved_net)}
    assert assets["hosp-1"].current_load_kw > 0
    assert assets["hosp-1"].current_gen_kw == 0.0
    assert assets["solar-1"].current_gen_kw > 0
    assert assets["solar-1"].current_load_kw == 0.0


def test_build_asset_states_battery_reports_soc_and_reserve(solved_net):
    assets = {a.asset_id: a for a in build_asset_states(solved_net)}
    batt = assets["batt-1"]
    assert batt.soc_percent == pytest.approx(72.0)
    assert batt.min_reserve_percent == pytest.approx(10.0)  # 0.05 / 0.5 * 100


def test_build_network_states_includes_feeders_and_synthetic_transformer(solved_net):
    networks = build_network_states(solved_net)
    feeder_ids = {n.feeder_id for n in networks}
    assert feeder_ids == {"F1", "F2", "F3", "TRANSFORMER"}
    for n in networks:
        assert isinstance(n, NetworkState)
        assert n.loading_percent > 0


def test_build_network_states_status_matches_thresholds(solved_net):
    """Regression check against the known baseline loading numbers (see
    contracts_adapter tested-output verified earlier): F2 sits in the
    'warning' band (>=90%) at baseline, F1/F3/TRANSFORMER are 'normal'."""
    networks = {n.feeder_id: n for n in build_network_states(solved_net)}
    assert networks["F2"].status == "warning"
    assert networks["F1"].status == "normal"
    assert networks["F3"].status == "normal"
    assert networks["TRANSFORMER"].status == "normal"


def test_build_grid_state_is_well_formed(solved_net):
    gs = build_grid_state(solved_net)
    assert isinstance(gs, GridState)
    assert len(gs.assets) == 6
    assert len(gs.feeders) == 4
    assert gs.predictions == []
    assert gs.active_faults == []
    assert gs.islands == []
    # round-trips through pydantic validation cleanly
    GridState.model_validate(gs.model_dump())
