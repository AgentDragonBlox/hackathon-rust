"""Exercise the running Rust/Python demo. Resets shared demo state."""
import time

import httpx


def main():
    with httpx.Client(timeout=60) as client:
        for port in (8002, 8001, 8000):
            deadline = time.monotonic() + 120
            while True:
                try:
                    response = client.get(f"http://127.0.0.1:{port}/health")
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
        assert client.get("http://127.0.0.1:8002/health").json()["engine"] == "rust"

        def control(action):
            result = client.post("http://127.0.0.1:8000/replay/control", json={"action": action})
            result.raise_for_status()
            return result.json()

        initial = control("restart")
        assert initial["index"] == 0 and not initial["playing"]
        advanced = control("step")
        assert advanced["index"] == 1 and not advanced["playing"]
        current = client.get("http://127.0.0.1:8001/grid/state").json()
        academic = next(a for a in current["assets"] if a["asset_id"] == "acad-1")
        assert abs(academic["current_load_kw"] - advanced["sample"]["campus_kw"]["academic_kw"]) < 1e-4

        previous_trades = {trade["trade_id"] for trade in client.get("http://127.0.0.1:8000/state/snapshot").json()["trades"]}
        response = client.post("http://127.0.0.1:8000/scenario/feeder_overload")
        response.raise_for_status()
        deadline = time.monotonic() + 30
        while True:
            snapshot = client.get("http://127.0.0.1:8000/state/snapshot").json()
            if any(trade["trade_id"] not in previous_trades for trade in snapshot["trades"]):
                break
            if time.monotonic() > deadline:
                raise AssertionError(f"No Rust-settled trades: {snapshot['recent_events']}")
            time.sleep(1)
        assert snapshot["agent_source"] == "live"
        assert snapshot["data_source"]["mode"] == "public_dataset_replay"
        assert snapshot["blockchain"]
        assert not any("fallback" in event["message"].lower() for event in snapshot["recent_events"])
        print(f"PASS: Rust identity, public input -> AC power flow, paused step, injected fault -> {len(snapshot['trades'])} settled trades + ledger")
        control("restart")


if __name__ == "__main__":
    main()
