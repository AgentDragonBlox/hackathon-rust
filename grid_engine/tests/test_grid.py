"""
Milestone 1 sanity tests for grid_engine.grid.

These are deliberately basic: they check that the network builds with the
expected shape, that power flow converges, and that results land within
physically sane bounds. They are NOT meant to lock in exact numeric
results -- assumed parameters (cable length, transformer tap, load sizes)
are expected to change as the project evolves through later milestones.
"""

from grid_engine.grid import build_campus_network, run_power_flow


def test_network_has_five_buses():
    net = build_campus_network()
    assert len(net.bus) == 5


def test_network_has_expected_elements():
    net = build_campus_network()
    assert len(net.ext_grid) == 1
    assert len(net.trafo) == 1
    assert len(net.line) == 3
    assert len(net.load) == 4  # hospital, academic, ev, facility
    assert len(net.sgen) == 1  # solar
    assert len(net.storage) == 1  # hospital BESS
    assert set(net.line["name"]) == {"F1", "F2", "F3"}


def test_build_campus_network_returns_independent_copies():
    """Each call must return a fresh net -- scenarios.py will later need to
    apply proposed actions to a *copy* of the current state without
    mutating the live network."""
    net_a = build_campus_network()
    net_b = build_campus_network()
    net_a.load.loc[0, "p_mw"] = 999.0
    assert net_b.load.loc[0, "p_mw"] != 999.0


def test_power_flow_converges():
    net = build_campus_network()
    run_power_flow(net)
    assert net["converged"] is True


def test_bus_voltages_within_healthy_band():
    net = build_campus_network()
    run_power_flow(net)
    # 0.95-1.05 pu is a commonly used "healthy" LV operating band.
    assert net.res_bus["vm_pu"].min() >= 0.95
    assert net.res_bus["vm_pu"].max() <= 1.05


def test_feeder_and_transformer_loading_are_sane():
    net = build_campus_network()
    run_power_flow(net)
    # Loading should be positive (power is flowing) and, in this
    # Milestone-1 baseline, under 100% (no violation expected yet --
    # violations are introduced deliberately in later milestones via
    # scenarios/forecasts).
    assert (net.res_line["loading_percent"] > 0).all()
    assert (net.res_line["loading_percent"] < 100).all()
    assert 0 < net.res_trafo["loading_percent"].iloc[0] < 100


def test_disconnected_buses_show_up_as_nan_not_an_exception():
    """
    IMPORTANT finding for later milestones (scenarios.py / outage handling):
    dropping all three feeders does NOT raise LoadflowNotConverged. By
    default pandapower's connectivity check (check_connectivity=True)
    detects the islanded hospital/academic/facility buses, excludes them
    from the Newton-Raphson solve, and reports converged=True for the
    remaining (still-connected) part of the network -- with NaN results
    for the islanded buses/elements. Milestone 6 (outages/islanding) must
    check for NaN / bus connectivity explicitly rather than relying on a
    non-convergence exception to detect a full feeder loss.
    """
    net = build_campus_network()
    net.line.loc[net.line["name"].isin(["F1", "F2", "F3"]), "in_service"] = False
    run_power_flow(net)
    assert net["converged"] is True
    islanded_bus_names = {"hospital", "academic", "facility"}
    islanded_mask = net.bus["name"].isin(islanded_bus_names)
    assert net.res_bus.loc[islanded_mask, "vm_pu"].isna().all()
