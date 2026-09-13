import asyncio
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from scripts.prepare_public_data import CHANNELS, build_replay
from orchestrator import loop as loop_module
from orchestrator.state import SystemState
from shared.contracts import AgentOffer, GridState, NetworkState, Prediction, ProposedAction, ValidationResult


def source_csv():
    output = io.StringIO()
    columns = [column for column, _ in CHANNELS.values()]
    writer = csv.DictWriter(output, fieldnames=["utc_timestamp", *columns, "interpolated"])
    writer.writeheader()
    start = datetime(2016, 6, 6, 6, tzinfo=timezone.utc)
    for index in range(169):
        writer.writerow({"utc_timestamp": (start + timedelta(hours=index)).isoformat(),
                         **{column: 1000 + index * 2 for column in columns},
                         "interpolated": columns[0] if index == 1 else ""})
    return output.getvalue().encode()


def test_counter_conversion_and_quality_flags():
    replay = build_replay(source_csv())
    assert replay["samples"][0]["source_average_kw"]["academic_kw"] == 2
    assert replay["samples"][0]["campus_kw"]["academic_kw"] == 240
    assert replay["samples"][0]["source_interpolated_columns"] == [CHANNELS["academic_kw"][0]]
    assert replay["samples"][1]["source_interpolated_columns"] == [CHANNELS["academic_kw"][0]]


def test_invalid_counter_or_missing_interval_is_rejected():
    with pytest.raises(ValueError):
        build_replay(source_csv().replace(b"1002", b"999"))
    with pytest.raises(ValueError):
        build_replay(source_csv().replace(b"2016-06-06T07:00:00", b"2016-06-06T07:30:00"))


def test_failed_settlement_never_fabricates_trades(monkeypatch):
    demo_state = SystemState()
    monkeypatch.setattr(loop_module, "state", demo_state)
    class Agents:
        async def get_offers(self, grid, requests):
            return [AgentOffer(offer_id="o", request_id=requests[0].request_id,
                               asset_id="acad-1", offer_type="hvac_reduction", kw_offered=10, cost=4.2)]
        async def clear_market(self, offers):
            return [ProposedAction(action_id="a", request_id=offers[0].request_id,
                                   asset_id="acad-1", action_type="hvac_reduction", kw_amount=10, source_offer_id="o")]
        async def settle(self, actions):
            raise ConnectionError("Rust service unavailable")
    class Grid:
        async def validate(self, actions):
            return [ValidationResult(action_id="a", feasible=True)]
    grid = GridState(timestamp=datetime.now(timezone.utc), assets=[], predictions=[],
                     feeders=[NetworkState(feeder_id="F2", loading_percent=105, connected_assets=[])])
    engine = loop_module.OrchestrationLoop(Grid(), Agents())
    asyncio.run(engine._run_market_for_feeder(grid, Prediction(feeder_id="F2", predicted_overload=True)))
    assert demo_state.trades == []
    assert demo_state.ledger.all_transactions() == []
    assert any("no trades recorded" in event.message for event in demo_state.event_log)
