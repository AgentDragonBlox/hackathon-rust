"""Hand-crafted OffersRequest payloads for testing against the real
shared/contracts.py shapes. Same intent as the earlier dict-based mocks,
rebuilt for the actual GridState / AssetState / FlexibilityRequest models.
"""
from datetime import datetime

from shared.contracts import (
    AssetState,
    FlexibilityRequest,
    GridState,
    NetworkState,
    OffersRequest,
    Prediction,
)


def _feeder(feeder_id, loading, connected, status="warning"):
    return NetworkState(
        feeder_id=feeder_id, loading_percent=loading,
        connected_assets=connected, status=status,
    )


# Baseline: academic + ev + battery together can cover 42 kW without
# needing the hospital at all.
NORMAL_OVERLOAD = OffersRequest(
    grid_state=GridState(
        timestamp=datetime.now(),
        assets=[
            AssetState(asset_id="hosp-1", asset_type="hospital", current_load_kw=380),
            AssetState(asset_id="acad-1", asset_type="academic", current_load_kw=240),
            AssetState(asset_id="ev-1", asset_type="ev", current_load_kw=20),
            AssetState(asset_id="batt-1", asset_type="battery", current_load_kw=0,
                       soc_percent=73, min_reserve_percent=20),
        ],
        feeders=[_feeder("F2", 91, ["hosp-1", "acad-1", "ev-1", "batt-1"])],
        predictions=[Prediction(feeder_id="F2", predicted_overload=True,
                                 eta_seconds=240, confidence=0.9)],
    ),
    requests=[FlexibilityRequest(request_id="req-1", feeder_id="F2", kw_needed=42,
                                  deadline_seconds=180,
                                  reason="F2 predicted overload in 4 min")],
)

# Battery already near its reserve floor — should be rejected or offer very
# little; relief has to come from academic/ev instead.
BATTERY_NEAR_RESERVE = OffersRequest(
    grid_state=GridState(
        timestamp=datetime.now(),
        assets=[
            AssetState(asset_id="hosp-1", asset_type="hospital", current_load_kw=300),
            AssetState(asset_id="acad-1", asset_type="academic", current_load_kw=200),
            AssetState(asset_id="ev-1", asset_type="ev", current_load_kw=15),
            AssetState(asset_id="batt-1", asset_type="battery", current_load_kw=0,
                       soc_percent=22, min_reserve_percent=20),
        ],
        feeders=[_feeder("F3", 95, ["hosp-1", "acad-1", "ev-1", "batt-1"])],
        predictions=[Prediction(feeder_id="F3", predicted_overload=True,
                                 eta_seconds=120, confidence=0.85)],
    ),
    requests=[FlexibilityRequest(request_id="req-2", feeder_id="F3", kw_needed=25,
                                  deadline_seconds=90,
                                  reason="F3 predicted overload in 2 min")],
)

# EV_SECONDS_UNTIL_DEADLINE (in ev_agent.py) needs asset_id "ev-2" set below
# 300 seconds before running this one, to exercise the "refuses near
# deadline" path:
#   from agent_engines.agents import ev_agent
#   ev_agent.EV_SECONDS_UNTIL_DEADLINE["ev-2"] = 120
EV_DEADLINE_IMMINENT = OffersRequest(
    grid_state=GridState(
        timestamp=datetime.now(),
        assets=[
            AssetState(asset_id="hosp-1", asset_type="hospital", current_load_kw=300),
            AssetState(asset_id="acad-1", asset_type="academic", current_load_kw=220),
            AssetState(asset_id="ev-2", asset_type="ev", current_load_kw=20),
            AssetState(asset_id="batt-1", asset_type="battery", current_load_kw=0,
                       soc_percent=60, min_reserve_percent=20),
        ],
        feeders=[_feeder("F2", 88, ["hosp-1", "acad-1", "ev-2", "batt-1"])],
        predictions=[Prediction(feeder_id="F2", predicted_overload=True,
                                 eta_seconds=180, confidence=0.8)],
    ),
    requests=[FlexibilityRequest(request_id="req-3", feeder_id="F2", kw_needed=20,
                                  deadline_seconds=150,
                                  reason="F2 predicted overload")],
)

# Regression test for a real gap grid_engine's actual topology surfaced:
# F3 connects ONLY to a factory-type asset. Before factory_agent.py
# existed, this scenario returned zero offers and could never be
# resolved. Keep this scenario in the suite so that gap can't silently
# come back.
F3_FACTORY_ONLY = OffersRequest(
    grid_state=GridState(
        timestamp=datetime.now(),
        assets=[
            AssetState(asset_id="fac-1", asset_type="factory", current_load_kw=150.0),
        ],
        feeders=[_feeder("F3", 105.0, ["fac-1"])],
        predictions=[Prediction(feeder_id="F3", predicted_overload=True,
                                 eta_seconds=200, confidence=0.85)],
    ),
    requests=[FlexibilityRequest(request_id="req-f3", feeder_id="F3", kw_needed=20.0,
                                  deadline_seconds=180,
                                  reason="F3 predicted overload")],
)

ALL_MOCK_REQUESTS = {
    "normal_overload": NORMAL_OVERLOAD,
    "battery_near_reserve": BATTERY_NEAR_RESERVE,
    "ev_deadline_imminent": EV_DEADLINE_IMMINENT,
    "f3_factory_only": F3_FACTORY_ONLY,
}
