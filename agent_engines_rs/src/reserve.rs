//! Direct port of agent_engines/reserve.py. Not called by anything else
//! in the pipeline yet — same status as the Python version.

use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::contracts::ReserveContract;

pub fn create_reserve_contract(
    asset_id: String,
    reserved_kw: f64,
    valid_until: DateTime<Utc>,
    purpose: String,
) -> ReserveContract {
    ReserveContract {
        contract_id: Uuid::new_v4().simple().to_string()[..8].to_string(),
        asset_id,
        reserved_kw,
        valid_until,
        purpose,
    }
}
