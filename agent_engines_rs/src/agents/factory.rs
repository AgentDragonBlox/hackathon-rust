//! Direct port of agent_engines/agents/factory_agent.py — the fix for
//! the F3 gap found via grid_engine's real topology.

use uuid::Uuid;

use crate::contracts::{AgentOffer, AssetState, FlexibilityRequest};

const FLEXIBLE_FRACTION: f64 = 0.20;
const PRICE_PER_KW: f64 = 6.50;

pub fn generate_offer(asset: &AssetState, request: &FlexibilityRequest) -> AgentOffer {
    let offer_id = Uuid::new_v4().simple().to_string()[..8].to_string();
    let offerable_kw = (asset.current_load_kw * FLEXIBLE_FRACTION * 1000.0).round() / 1000.0;

    if !asset.online || offerable_kw < 1.0 {
        return AgentOffer {
            offer_id,
            request_id: request.request_id.clone(),
            asset_id: asset.asset_id.clone(),
            offer_type: "production_flex".to_string(),
            kw_offered: 0.0,
            cost: 0.0,
            rejected: true,
            rejection_reason: Some("no meaningful flexible production load available".to_string()),
        };
    }

    AgentOffer {
        offer_id,
        request_id: request.request_id.clone(),
        asset_id: asset.asset_id.clone(),
        offer_type: "production_flex".to_string(),
        kw_offered: offerable_kw,
        cost: PRICE_PER_KW,
        rejected: false,
        rejection_reason: None,
    }
}
