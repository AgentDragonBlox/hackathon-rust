//! Rust mirror of the subset of `shared/contracts.py` that agent_engines
//! needs. Field names, types, and optionality all match exactly — this
//! is what makes this service a true drop-in replacement: Person 1's
//! grid_engine and Person 3's orchestrator send/receive the same JSON
//! shapes regardless of which language answers on port 8002.
//!
//! NOT importing or wrapping the Python file — this is an independent
//! mirror. If shared/contracts.py changes, this file needs a matching
//! manual update (same "don't fork copies without discussing" rule from
//! the Python file's own docstring applies conceptually here too).

use chrono::{DateTime, NaiveDateTime, Utc};
use serde::{Deserialize, Deserializer, Serialize};

/// Python's pydantic accepts both timezone-aware and naive ISO datetime
/// strings for a `datetime` field. Rust's chrono, via serde's default
/// impl, only accepts RFC3339 WITH an explicit timezone -- stricter than
/// the Python service this needs to be a drop-in replacement for. This
/// custom deserializer restores that leniency: try RFC3339 first, fall
/// back to a naive datetime assumed as UTC. Without this, a legitimate
/// payload that worked against the Python version could 422 against this
/// one for no reason a caller would expect.
fn lenient_datetime<'de, D>(deserializer: D) -> Result<DateTime<Utc>, D::Error>
where
    D: Deserializer<'de>,
{
    let s = String::deserialize(deserializer)?;
    if let Ok(dt) = DateTime::parse_from_rfc3339(&s) {
        return Ok(dt.with_timezone(&Utc));
    }
    NaiveDateTime::parse_from_str(&s, "%Y-%m-%dT%H:%M:%S%.f")
        .or_else(|_| NaiveDateTime::parse_from_str(&s, "%Y-%m-%dT%H:%M:%S"))
        .map(|naive| naive.and_utc())
        .map_err(serde::de::Error::custom)
}

fn serialize_datetime<S>(dt: &DateTime<Utc>, serializer: S) -> Result<S::Ok, S::Error>
where
    S: serde::Serializer,
{
    serializer.serialize_str(&dt.to_rfc3339())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AssetState {
    pub asset_id: String,
    pub asset_type: String, // Literal in Python; validated loosely here, see main.rs dispatch
    #[serde(default)]
    pub current_load_kw: f64,
    #[serde(default)]
    pub current_gen_kw: f64,
    #[serde(default)]
    pub soc_percent: Option<f64>,
    #[serde(default)]
    pub min_reserve_percent: Option<f64>,
    #[serde(default = "default_true")]
    pub online: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NetworkState {
    pub feeder_id: String,
    pub loading_percent: f64,
    #[serde(default)]
    pub connected_assets: Vec<String>,
    #[serde(default = "default_status")]
    pub status: String,
}

fn default_status() -> String {
    "normal".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Prediction {
    pub feeder_id: String,
    pub predicted_overload: bool,
    #[serde(default)]
    pub eta_seconds: Option<f64>,
    #[serde(default = "default_confidence")]
    pub confidence: f64,
}

fn default_confidence() -> f64 {
    1.0
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GridState {
    #[serde(deserialize_with = "lenient_datetime", serialize_with = "serialize_datetime")]
    pub timestamp: DateTime<Utc>,
    #[serde(default)]
    pub assets: Vec<AssetState>,
    #[serde(default)]
    pub feeders: Vec<NetworkState>,
    #[serde(default)]
    pub predictions: Vec<Prediction>,
    #[serde(default)]
    pub active_faults: Vec<String>,
    #[serde(default)]
    pub islands: Vec<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FlexibilityRequest {
    pub request_id: String,
    pub feeder_id: String,
    pub kw_needed: f64,
    pub deadline_seconds: f64,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentOffer {
    pub offer_id: String,
    pub request_id: String,
    pub asset_id: String,
    pub offer_type: String,
    pub kw_offered: f64,
    pub cost: f64,
    #[serde(default)]
    pub rejected: bool,
    #[serde(default)]
    pub rejection_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProposedAction {
    pub action_id: String,
    pub request_id: String,
    pub asset_id: String,
    pub action_type: String,
    pub kw_amount: f64,
    pub source_offer_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    pub trade_id: String,
    pub request_id: String,
    pub buyer_id: String,
    pub seller_id: String,
    pub kw_amount: f64,
    pub price: f64,
    pub action_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReserveContract {
    pub contract_id: String,
    pub asset_id: String,
    pub reserved_kw: f64,
    #[serde(deserialize_with = "lenient_datetime", serialize_with = "serialize_datetime")]
    pub valid_until: DateTime<Utc>,
    pub purpose: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OffersRequest {
    pub grid_state: GridState,
    pub requests: Vec<FlexibilityRequest>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClearMarketRequest {
    pub offers: Vec<AgentOffer>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReserveQuery {
    pub asset_id: String,
    pub reserved_kw: f64,
    #[serde(deserialize_with = "lenient_datetime")]
    pub valid_until: DateTime<Utc>,
    pub purpose: String,
}
