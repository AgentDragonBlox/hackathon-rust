"""Standalone smoke test for the running agent_engines service.

Not part of the HTTP API — this is a manual verification / demo harness.
Start the service first (uvicorn agent_engines.main:app --port 8002),
then run this file directly:

    python -m agent_engines.run_market

Exercises the full loop — offers -> clear_market -> settle — against one
of the mock scenarios, and prints exactly what got picked and why. Useful
for a quick "is my running server actually working" check without curl,
and doubles as a live mini-demo if you want to show the loop working
standalone before Person 3's dashboard exists.
"""
import sys

import httpx

from agent_engines.tests.mock_states import ALL_MOCK_REQUESTS
from shared.contracts import ClearMarketRequest

BASE_URL = "http://localhost:8002"


def run_scenario(name: str) -> None:
    request = ALL_MOCK_REQUESTS[name]
    print(f"=== {name} ===")

    offers = httpx.post(f"{BASE_URL}/agents/offers", json=request.model_dump(mode="json")).json()
    for o in offers:
        tag = "REJECTED" if o["rejected"] else "offered "
        reason = f"  ({o['rejection_reason']})" if o["rejection_reason"] else ""
        print(f"  {o['asset_id']:10s} {tag} {o['kw_offered']:6.2f} kW @ {o['cost']}{reason}")

    clear_req = ClearMarketRequest(offers=offers)
    actions = httpx.post(f"{BASE_URL}/agents/clear_market", json=clear_req.model_dump(mode="json")).json()

    if not actions:
        print("  -> no actions needed / no feasible combination found")
        return

    trades = httpx.post(f"{BASE_URL}/agents/settle", json=actions).json()
    total = sum(t["kw_amount"] for t in trades)
    print(f"  -> settled {total:.2f} kW:")
    for t in trades:
        print(f"       {t['seller_id']:10s} {t['kw_amount']:6.2f} kW @ {t['price']}  (trade {t['trade_id']})")
    print()


def main() -> None:
    try:
        httpx.get(f"{BASE_URL}/health", timeout=2).raise_for_status()
    except httpx.HTTPError:
        print(f"Can't reach {BASE_URL} — start the service first:")
        print("  uvicorn agent_engines.main:app --port 8002")
        sys.exit(1)

    for name in ALL_MOCK_REQUESTS:
        run_scenario(name)


if __name__ == "__main__":
    main()
