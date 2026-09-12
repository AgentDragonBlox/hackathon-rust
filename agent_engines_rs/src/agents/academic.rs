//! Direct port of agent_engines/agents/academic_agent.py.

use uuid::Uuid;

use crate::contracts::{AgentOffer, AssetState, FlexibilityRequest};

const FLEXIBLE_FRACTION: f64 = 0.30;
const PRICE_PER_KW: f64 = 4.20;

pub fn generate_offer(asset: &AssetState, request: &FlexibilityRequest) -> AgentOffer {
    let offer_id = Uuid::new_v4().simple().to_string()[..8].to_string();
    let offerable_kw = (asset.current_load_kw * FLEXIBLE_FRACTION * 1000.0).round() / 1000.0;

    if !asset.online || offerable_kw < 1.0 {
        return AgentOffer {
            offer_id,
            request_id: request.request_id.clone(),
            asset_id: asset.asset_id.clone(),
            offer_type: "hvac_reduction".to_string(),
            kw_offered: 0.0,
            cost: 0.0,
            rejected: true,
            rejection_reason: Some("no meaningful flexible load available".to_string()),
        };
    }

    AgentOffer {
        offer_id,
        request_id: request.request_id.clone(),
        asset_id: asset.asset_id.clone(),
        offer_type: "hvac_reduction".to_string(),
        kw_offered: offerable_kw,
        cost: PRICE_PER_KW,
        rejected: false,
        rejection_reason: None,
    }
}
