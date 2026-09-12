"""
grid.py

Milestone 1: builds the representative 5-bus campus microgrid in pandapower
and runs a single AC power flow so we can inspect bus voltages, feeder
(line) loading, and transformer loading.

This file is intentionally minimal at Milestone 1 -- static network,
single power-flow snapshot. Dynamic profiles (simulation.py), forecasting
(forecasting.py), and action/scenario validation (scenarios.py) build on
top of what's here in later milestones. Other team members should be able
to call build_campus_network() / run_power_flow() / summarize_power_flow()
without knowing anything about pandapower internals.

-------------------------------------------------------------------------
NETWORK TOPOLOGY (5 buses)
-------------------------------------------------------------------------

    UTILITY GRID (11 kV)
          |
      TRANSFORMER  (11/0.415 kV, 1 MVA -- ASSUMED)
          |
    CAMPUS MAIN BUS (0.415 kV)
      /        |         \
   F1 (x3)   F2 (x1)    F3 (x1)      <- LV feeder cables
    |          |            \
 HOSPITAL   ACADEMIC      FACILITY
    |        |    |
  BESS    SOLAR   EV

Buses: utility (HV) -> campus_main (LV) -> hospital -> academic -> facility
     = 5 buses total, matching the brief's "~5-bus representative network".

Everything downstream of the transformer is modeled at a single LV level
(0.415 kV), which is a common simplification for a compact campus where
buildings share one low-voltage switchroom/main distribution board and
are each fed by a dedicated armored LV feeder cable. This is an explicitly
ASSUMED, simplified topology -- NOT a real single-line diagram of any
actual site.

-------------------------------------------------------------------------
ASSUMED ELECTRICAL PARAMETERS (clearly synthetic, not measured)
-------------------------------------------------------------------------

- Utility connection: 11 kV, treated as an infinite bus (slack), vm_pu=1.02
  (typical utility supply is a few % above nominal).
- Transformer: 1 MVA, 11/0.415 kV distribution transformer. vk_percent=6,
  vkr_percent=1.2, pfe_kw=1.7, i0_percent=0.3 -- typical catalog values
  for an oil-type 1000 kVA distribution transformer of this class. An
  off-nominal tap of +2.5% (HV-side tap_pos=-1, one step below neutral on
  a +-2x2.5% tap range) is applied -- this is standard real-world practice
  for a transformer supplying LV feeders with meaningful voltage drop, and
  without it the far-end buses (academic, facility) sit below the 0.95 pu
  healthy-operating threshold even at this Milestone-1 baseline. See the
  "ELECTRICAL FINDING" note further down for why this was needed.
- Feeder cables: pandapower's built-in "NAYY 4x150 SE" LV cable standard
  type (a real cable spec from pandapower's own std_type library, not an
  invented value: r=0.208 ohm/km, x=0.080 ohm/km, max_i_ka=0.270).
  Length assumed 0.3 km per feeder (typical intra-campus run).
  The hospital feeder (F1) uses 3 parallel cables to reflect realistic
  redundant/high-capacity supply practice for a critical-care load;
  academic (F2) and facility (F3) use a single cable run each.
- Loads: base-case demand figures reused from the project's GridState
  schema example (shared/schemas/grid_state.json) for continuity across
  the project: hospital 380 kW, academic 240 kW, EV charging 70 kW,
  facility/admin 150 kW (facility has no schema entry yet, so this one
  figure IS a fresh assumption). Power factor assumed 0.95 lagging for
  building loads (typical mixed HVAC/lighting/equipment load) and 0.98
  for EV charging (modern chargers are close to unity via power
  electronics).
- Solar PV: 165 kW (from the schema example), unity power factor
  (standard baseline assumption for a grid-tied inverter with no
  reactive power support configured).
- Hospital BESS: idle in this Milestone-1 snapshot (p_mw=0). Capacity
  assumed 0.5 MWh, current state of charge 72% (matches the schema
  example's battery_soc=0.72). Dispatch (charge/discharge) is added in
  later milestones.

If you change any of these, update the docstring comment next to the
relevant create_* call below so the assumption stays visible in the code,
not just here.

-------------------------------------------------------------------------
ELECTRICAL FINDING FROM BUILDING THIS MODEL (not fabricated -- computed)
-------------------------------------------------------------------------

With the transformer at its nominal ratio (no tap boost), power flow
converges fine, but the academic bus sits at ~0.92 pu -- already below the
commonly-used 0.95 pu healthy LV operating threshold, in the *baseline*,
un-stressed snapshot. That's not the story we want: per the project's
prediction philosophy, the baseline should be normal, and violations
should show up as *forecasted* future events, not already present at
timestamp zero. This isn't a code bug, it's a real consequence of feeding
~250 A through a single 150 mm^2 LV cable over 0.3 km -- realistic feeder
voltage drop. The fix applied here (a +2.5% transformer tap) is standard
real-world practice, not a fudge: LV distribution transformers commonly
have off-load taps specifically to compensate for known feeder drop.
"""

import logging

import numpy as np
import pandapower as pp

# pandapower warns on every runpp() call that numba isn't installed and
# power flow will be "slow". For our 5-bus network this is irrelevant (each
# solve is milliseconds either way), but the warning gets noisy over a
# multi-step time series (simulation.py calls runpp() ~180 times). We
# deliberately are NOT installing numba (see requirements.txt notes / chat
# history -- unnecessary dependency for a network this small), so silence
# just this cosmetic warning rather than leaving it to spam every run.
logging.getLogger("pandapower").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Assumed load power factors (used to convert kW -> kvar for pandapower).
# Kept as named constants so they're easy to find and justify, rather than
# scattered magic numbers.
# ---------------------------------------------------------------------------
PF_BUILDING_LOAD = 0.95   # typical commercial/hospital mixed load (HVAC, lighting, equipment)
PF_EV_CHARGING = 0.98     # modern EV chargers are near-unity via power electronics
PF_SOLAR = 1.00           # grid-tied inverter, no reactive power support modeled yet


def kw_to_mw(kw: float) -> float:
    """pandapower works in MW/MVA/kV; our assumptions are easier to read in kW."""
    return kw / 1000.0


def q_mvar_from_pf(p_mw: float, pf: float) -> float:
    """Derive reactive power from real power and an assumed power factor."""
    if not (0 < pf <= 1):
        raise ValueError(f"power factor must be in (0, 1], got {pf}")
    return p_mw * np.tan(np.arccos(pf))


def build_campus_network() -> pp.pandapowerNet:
    """
    Construct the 5-bus representative campus microgrid described in this
    module's docstring. Returns a fresh pandapower network -- callers get
    their own independent copy each time this is called (important later,
    when scenarios.py needs to apply proposed actions to a *copy* of the
    current state without mutating the live network).
    """
    net = pp.create_empty_network(name="campus_microgrid_milestone1")

    # --- Buses -------------------------------------------------------------
    bus_utility = pp.create_bus(net, vn_kv=11.0, name="utility_hv")
    bus_main = pp.create_bus(net, vn_kv=0.415, name="campus_main")
    bus_hospital = pp.create_bus(net, vn_kv=0.415, name="hospital")
    bus_academic = pp.create_bus(net, vn_kv=0.415, name="academic")
    bus_facility = pp.create_bus(net, vn_kv=0.415, name="facility")

    # --- External grid (slack / utility connection) -------------------------
    # vm_pu=1.02: utility supply point voltage assumed slightly above nominal,
    # which is typical for an MV feed close to a distribution substation.
    pp.create_ext_grid(net, bus=bus_utility, vm_pu=1.02, va_degree=0.0, name="utility_grid")

    # --- Transformer: 11 kV / 0.415 kV, 1 MVA (ASSUMED, see module docstring) ---
    trafo_idx = pp.create_transformer_from_parameters(
        net,
        hv_bus=bus_utility,
        lv_bus=bus_main,
        sn_mva=1.0,
        vn_hv_kv=11.0,
        vn_lv_kv=0.415,
        vk_percent=6.0,
        vkr_percent=1.2,
        pfe_kw=1.7,
        i0_percent=0.3,
        name="campus_transformer",
    )
    # Off-nominal tap: +2.5% boost on the LV side (one step below HV-side
    # neutral on a +-2 x 2.5% tap range). Standard practice for a
    # transformer feeding LV runs with meaningful voltage drop -- see the
    # "ELECTRICAL FINDING" note in the module docstring for why this is
    # here rather than left at the nominal ratio.
    net.trafo.loc[trafo_idx, "tap_side"] = "hv"
    net.trafo.loc[trafo_idx, "tap_neutral"] = 0
    net.trafo.loc[trafo_idx, "tap_min"] = -2
    net.trafo.loc[trafo_idx, "tap_max"] = 2
    net.trafo.loc[trafo_idx, "tap_step_percent"] = 2.5
    net.trafo.loc[trafo_idx, "tap_pos"] = -1
    net.trafo.loc[trafo_idx, "tap_changer_type"] = "Ratio"

    # --- LV feeder cables (F1 hospital, F2 academic, F3 facility) -----------
    # std_type "NAYY 4x150 SE" is a real cable definition from pandapower's
    # own standard-type library (not an invented value).
    feeder_length_km = 0.3  # ASSUMED typical intra-campus cable run
    pp.create_line(
        net, from_bus=bus_main, to_bus=bus_hospital,
        length_km=feeder_length_km, std_type="NAYY 4x150 SE",
        parallel=3,  # redundant/high-capacity feeder for the critical hospital load
        name="F1",
    )
    pp.create_line(
        net, from_bus=bus_main, to_bus=bus_academic,
        length_km=feeder_length_km, std_type="NAYY 4x150 SE",
        parallel=1,
        name="F2",
    )
    pp.create_line(
        net, from_bus=bus_main, to_bus=bus_facility,
        length_km=feeder_length_km, std_type="NAYY 4x150 SE",
        parallel=1,
        name="F3",
    )

    # --- Loads ---------------------------------------------------------------
    hospital_kw = 380.0
    academic_kw = 240.0
    ev_kw = 70.0
    facility_kw = 150.0  # ASSUMED -- no schema example value exists for this asset yet

    p_hospital = kw_to_mw(hospital_kw)
    pp.create_load(
        net, bus=bus_hospital, p_mw=p_hospital,
        q_mvar=q_mvar_from_pf(p_hospital, PF_BUILDING_LOAD),
        name="hospital_load",
    )

    p_academic = kw_to_mw(academic_kw)
    pp.create_load(
        net, bus=bus_academic, p_mw=p_academic,
        q_mvar=q_mvar_from_pf(p_academic, PF_BUILDING_LOAD),
        name="academic_load",
    )

    p_ev = kw_to_mw(ev_kw)
    pp.create_load(
        net, bus=bus_academic, p_mw=p_ev,
        q_mvar=q_mvar_from_pf(p_ev, PF_EV_CHARGING),
        name="ev_load",
    )

    p_facility = kw_to_mw(facility_kw)
    pp.create_load(
        net, bus=bus_facility, p_mw=p_facility,
        q_mvar=q_mvar_from_pf(p_facility, PF_BUILDING_LOAD),
        name="facility_load",
    )

    # --- Solar PV (static generator) at the academic bus ----------------------
    solar_kw = 165.0
    pp.create_sgen(
        net, bus=bus_academic, p_mw=kw_to_mw(solar_kw), q_mvar=0.0,
        name="solar",
    )

    # --- Hospital BESS (idle in this Milestone-1 snapshot) --------------------
    pp.create_storage(
        net, bus=bus_hospital,
        p_mw=0.0, q_mvar=0.0,          # idle baseline -- dispatch added later
        max_e_mwh=0.5,                  # ASSUMED 500 kWh usable capacity
        min_e_mwh=0.05,                 # ASSUMED 10% reserve floor
        soc_percent=72.0,               # matches the schema example's battery_soc=0.72
        max_p_mw=0.25,                  # ASSUMED 250 kW rated charge/discharge power
        name="hospital_bess",
    )

    return net


def get_asset_indices(net: pp.pandapowerNet) -> dict:
    """
    Look up the pandapower table + row index for each named asset, by name,
    instead of relying on creation order/position. Returns e.g.:

        {"hospital_load": ("load", 0), "solar": ("sgen", 0), ...}

    Both simulation.py (updating p_mw/q_mvar each timestep) and, later,
    scenarios.py (applying a proposed action to a specific agent's load)
    need this same "find the element for this asset name" lookup, so it
    lives here once rather than being duplicated.
    """
    indices = {}
    for table in ("load", "sgen", "storage"):
        df = getattr(net, table)
        for idx, name in df["name"].items():
            indices[name] = (table, idx)
    return indices


def run_power_flow(net: pp.pandapowerNet) -> pp.pandapowerNet:
    """
    Run a standard Newton-Raphson AC power flow on the given network.

    Raises pandapower's own LoadflowNotConverged if the network doesn't
    converge -- we deliberately do NOT catch and paper over that here.
    A non-convergent power flow is itself an important signal (e.g. a
    disconnected or badly infeasible network) and later milestones
    (scenarios.py) need to be able to tell "converged but violates limits"
    apart from "didn't converge at all".
    """
    pp.runpp(net, calculate_voltage_angles=True)
    return net


def summarize_power_flow(net: pp.pandapowerNet) -> None:
    """
    Print bus voltages, line (feeder) loading, and transformer loading from
    a network that has already had run_power_flow() called on it.
    """
    print("=" * 60)
    print("BUS VOLTAGES")
    print("=" * 60)
    bus_view = net.res_bus.copy()
    bus_view["name"] = net.bus["name"]
    print(bus_view[["name", "vm_pu", "va_degree"]].to_string(index=False))

    print()
    print("=" * 60)
    print("FEEDER (LINE) LOADING")
    print("=" * 60)
    line_view = net.res_line.copy()
    line_view["name"] = net.line["name"]
    print(line_view[["name", "loading_percent", "i_ka", "p_from_mw", "q_from_mvar"]].to_string(index=False))

    print()
    print("=" * 60)
    print("TRANSFORMER LOADING")
    print("=" * 60)
    trafo_view = net.res_trafo.copy()
    trafo_view["name"] = net.trafo["name"]
    print(trafo_view[["name", "loading_percent", "p_hv_mw", "q_hv_mvar"]].to_string(index=False))

    print()
    print("=" * 60)
    print("CONSTRAINT CHECK (informational -- thresholds not enforced yet)")
    print("=" * 60)
    max_line_loading = net.res_line["loading_percent"].max()
    max_trafo_loading = net.res_trafo["loading_percent"].max()
    min_vm_pu = net.res_bus["vm_pu"].min()
    max_vm_pu = net.res_bus["vm_pu"].max()
    print(f"Max feeder loading:        {max_line_loading:.1f}%")
    print(f"Max transformer loading:   {max_trafo_loading:.1f}%")
    print(f"Bus voltage range:         {min_vm_pu:.4f} - {max_vm_pu:.4f} pu")


if __name__ == "__main__":
    net = build_campus_network()
    run_power_flow(net)
    summarize_power_flow(net)
