import json

import pytest
from fastapi.testclient import TestClient

from grid_engine import api
from grid_engine.replay import DATA_PATH, PublicReplay


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "_replay", PublicReplay())
    api._active_faults.clear()
    api._rebuild_live_state()
    yield TestClient(api.app)
    api._active_faults.clear()
    api._replay = None
    api._rebuild_live_state()


def test_bundled_data_is_hourly_scaled_and_attributed():
    data = json.loads(DATA_PATH.read_text())
    assert len(data["samples"]) == 168
    assert data["license"] == "CC-BY-4.0"
    for key, mapping in data["mapping"].items():
        assert max(s["campus_kw"][key] for s in data["samples"]) == mapping["campus_peak_kw"]
        for sample in data["samples"]:
            expected = sample["source_average_kw"][key] / mapping["source_peak_kw"] * mapping["campus_peak_kw"]
            assert sample["campus_kw"][key] == pytest.approx(expected, abs=1e-6)


def test_read_does_not_advance_and_pause_prevents_tick(client):
    before = client.get("/grid/state").json()
    assert client.get("/grid/state").json()["assets"] == before["assets"]
    client.post("/grid/replay/control", json={"action": "pause"})
    result = client.post("/grid/replay/control", json={"action": "tick"}).json()
    assert result["index"] == 0
    assert not result["playing"]


def test_step_updates_real_power_flow_and_preserves_faults(client):
    client.post("/grid/fault", json={"feeder_id": "F1", "fault_type": "line_fault"})
    result = client.post("/grid/replay/control", json={"action": "step"}).json()
    current = client.get("/grid/state").json()
    assert result["index"] == 1
    assert current["active_faults"] == ["F1"]
    assert ["batt-1", "hosp-1"] in current["islands"]
    academic = next(a for a in current["assets"] if a["asset_id"] == "acad-1")
    assert academic["current_load_kw"] == pytest.approx(result["sample"]["campus_kw"]["academic_kw"])
    restarted = client.post("/grid/replay/control", json={"action": "restart"}).json()
    assert restarted["index"] == 0
    assert not restarted["playing"]
    assert client.get("/grid/state").json()["active_faults"] == []


def test_end_stops_and_invalid_control_rejected(client):
    api._replay.index = 166
    result = client.post("/grid/replay/control", json={"action": "tick"}).json()
    assert result["index"] == 167
    assert not result["playing"]
    assert client.post("/grid/replay/control", json={"action": "invalid"}).status_code == 422
