"""Task 5 test: GET /experiment/results serves scripts/run_experiment.py's
output for the dashboard's Scenario Comparison panel. Calls the route
function directly (not through TestClient/app lifespan) so this doesn't
need to spin up the orchestration loop's background tick task."""

import asyncio
import json

import pytest
from fastapi import HTTPException

import orchestrator.main as main_module


def test_experiment_results_404_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "EXPERIMENT_RESULTS_PATH", tmp_path / "missing.json")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main_module.get_experiment_results())
    assert exc_info.value.status_code == 404


def test_experiment_results_returns_file_contents(monkeypatch, tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"summary": {"no_intervention": {"overloaded_timesteps": 1}}}))
    monkeypatch.setattr(main_module, "EXPERIMENT_RESULTS_PATH", path)
    result = asyncio.run(main_module.get_experiment_results())
    assert result["summary"]["no_intervention"]["overloaded_timesteps"] == 1
