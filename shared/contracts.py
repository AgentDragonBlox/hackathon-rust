"""
Shared data contracts for the microgrid resilience exchange hackathon project.

ALL THREE TEAM MEMBERS IMPORT FROM THIS FILE. Do not fork copies.
If a field needs to change, discuss with the team first — this is the
one file that, if it drifts, breaks integration for everyone.

Person 1 (Grid Engine)     -> produces GridState, ValidationResult
Person 2 (Agents/Market)   -> produces AgentOffer, ProposedAction, Trade, ReserveContract
Person 3 (Orchestrator)    -> produces ExecutedAction, BlockchainTransaction, SystemEvent
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# GRID (Person 1 produces)
# ---------------------------------------------------------------------------


class AssetState(BaseModel):
    asset_id: str
    asset_type: Literal[
        "hospital", "academic", "factory", "battery", "solar", "ev", "utility"
    ]
    current_load_kw: float = 0.0
    current_gen_kw: float = 0.0
    soc_percent: Optional[float] = None  # batteries only, 0-100
    min_reserve_percent: Optional[float] = None  # emergency floor, e.g. hospital battery
    online: bool = True


class NetworkState(BaseModel):
    feeder_id: str
    loading_percent: float  # 0-100+, >100 means overloaded
    connected_assets: list[str] = Field(default_factory=list)  # asset_ids
    status: Literal["normal", "warning", "overloaded", "faulted", "islanded"] = "normal"


class Prediction(BaseModel):
    feeder_id: str
    predicted_overload: bool
    eta_seconds: Optional[float] = None  # time until predicted overload
    confidence: float = 1.0


class GridState(BaseModel):
    timestamp: datetime
    assets: list[AssetState] = Field(default_factory=list)
    feeders: list[NetworkState] = Field(default_factory=list)
    predictions: list[Prediction] = Field(default_factory=list)
    active_faults: list[str] = Field(default_factory=list)  # feeder_ids currently faulted
    islands: list[list[str]] = Field(default_factory=list)  # groups of asset_ids


# ---------------------------------------------------------------------------
# AGENTS / MARKET (Person 2 produces)
# ---------------------------------------------------------------------------


class FlexibilityRequest(BaseModel):
    request_id: str
    feeder_id: str
    kw_needed: float
    deadline_seconds: float
    reason: str  # human-readable, feeds the agent activity log


class AgentOffer(BaseModel):
    offer_id: str
    request_id: str
    asset_id: str
    offer_type: Literal[
        "battery_discharge",
        "load_reduction",
        "ev_delay",
        "hvac_reduction",
        "production_flex",
        "emergency_reserve",
    ]
    kw_offered: float
    cost: float
    rejected: bool = False
    rejection_reason: Optional[str] = None


class ProposedAction(BaseModel):
    action_id: str
    request_id: str
    asset_id: str
    action_type: str  # mirrors offer_type once selected by the clearing agent
    kw_amount: float
    source_offer_id: str


# ---------------------------------------------------------------------------
# VALIDATION (Person 1 consumes ProposedAction, produces this)
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    action_id: str
    feasible: bool
    reason: Optional[str] = None
    adjusted_kw_amount: Optional[float] = None  # if grid can only partially accept it


# ---------------------------------------------------------------------------
# EXECUTION (Orchestrator produces after validation)
# ---------------------------------------------------------------------------


class ExecutedAction(BaseModel):
    action_id: str
    asset_id: str
    kw_amount: float
    executed_at: datetime
    resulting_feeder_loading: Optional[float] = None


# ---------------------------------------------------------------------------
# BLOCKCHAIN / MARKET RECORD
# ---------------------------------------------------------------------------


class ReserveContract(BaseModel):
    contract_id: str
    asset_id: str
    reserved_kw: float
    valid_until: datetime
    purpose: str  # e.g. "hospital emergency reserve"


class Trade(BaseModel):
    trade_id: str
    request_id: str
    buyer_id: str  # e.g. "grid" or a facility id
    seller_id: str  # asset_id providing flexibility
    kw_amount: float
    price: float
    action_id: str


class BlockchainTransaction(BaseModel):
    tx_id: str
    tx_type: Literal["trade_settlement", "reserve_contract", "fulfillment_record"]
    payload_ref: str  # trade_id or contract_id
    timestamp: datetime
    block_number: Optional[int] = None
    prev_hash: Optional[str] = None
    hash: Optional[str] = None
    status: Literal["pending", "confirmed"] = "pending"


# ---------------------------------------------------------------------------
# SYSTEM / DASHBOARD
# ---------------------------------------------------------------------------


class SystemEvent(BaseModel):
    event_id: str
    timestamp: datetime
    source: str  # "Network Agent", "Grid Engine", "System", etc.
    message: str
    severity: Literal["info", "warning", "error"] = "info"


# ---------------------------------------------------------------------------
# COMPOSITE REQUEST/RESPONSE SHAPES FOR THE PERSON 2 ENDPOINTS
# (not core domain objects, just wire shapes for /agents/offers)
# ---------------------------------------------------------------------------


class OffersRequest(BaseModel):
    grid_state: GridState
    requests: list[FlexibilityRequest]


class ClearMarketRequest(BaseModel):
    offers: list[AgentOffer]


class FaultInjectionRequest(BaseModel):
    feeder_id: str
    fault_type: Literal["solar_drop", "demand_spike", "feeder_overload", "battery_failure", "grid_outage", "line_fault"]


# ---------------------------------------------------------------------------
# COMPOSITE REQUEST/RESPONSE SHAPES FOR THE PERSON 1 (GRID ENGINE) ENDPOINTS
# (not core domain objects, just wire shapes for POST /grid/validate -- added
# by Person 1, following the same pattern as the Person-2 composite shapes
# above. See grid_engine/INTEGRATION.md for the endpoint docs, verified
# example payloads, and known gaps.)
# ---------------------------------------------------------------------------


class ValidateActionsRequest(BaseModel):
    actions: list[ProposedAction]


class ClearFaultRequest(BaseModel):
    """Milestone 6: wire shape for POST /grid/fault/clear. `feeder_id`
    matches the registry key GridState.active_faults reports -- for every
    fault_type except grid_outage that's the feeder_id it was injected
    with; for grid_outage it's the synthetic value "GRID" (grid_outage
    isn't actually feeder-scoped -- see grid_engine/faults.py's module
    docstring). See grid_engine/INTEGRATION.md for the full write-up."""
    feeder_id: str
