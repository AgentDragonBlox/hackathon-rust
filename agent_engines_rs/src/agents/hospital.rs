//! Direct port of agent_engines/agents/hospital_agent.py. Same internal
//! assumption, same caveat: AssetState has no critical-vs-flexible load
//! split, so a fixed fraction stands in until the contract has a real field.

use uuid::Uuid;

use crate::contracts::{AgentOffer, AssetState, FlexibilityRequest};

const FLEXIBLE_FRACTION: f64 = 0.05;
const PRICE_PER_KW: f64 = 9.50;

pub fn generate_offer(asset: &AssetState, request: &FlexibilityRequest) -> AgentOffer {
    let offer_id = Uuid::new_v4().simple().to_string()[..8].to_string();
    let offerable_kw = (asset.current_load_kw * FLEXIBLE_FRACTION * 1000.0).round() / 1000.0;

    if !asset.online || offerable_kw < 1.0 {
        return AgentOffer {
            offer_id,
            request_id: request.request_id.clone(),
            asset_id: asset.asset_id.clone(),
            offer_type: "load_reduction".to_string(),
            kw_offered: 0.0,
            cost: 0.0,
            rejected: true,
            rejection_reason: Some("no meaningful non-critical load available to shed".to_string()),
        };
    }

    AgentOffer {
        offer_id,
        request_id: request.request_id.clone(),
        asset_id: asset.asset_id.clone(),
        offer_type: "load_reduction".to_string(),
        kw_offered: offerable_kw,
        cost: PRICE_PER_KW,
        rejected: false,
        rejection_reason: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> FlexibilityRequest {
        FlexibilityRequest {
            request_id: "req-test".into(),
            feeder_id: "F1".into(),
            kw_needed: 10.0,
            deadline_seconds: 180.0,
            reason: "test".into(),
        }
    }

    #[test]
    fn offers_flexible_fraction() {
        let asset = AssetState { asset_id: "hosp-1".into(), asset_type: "hospital".into(), current_load_kw: 380.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: true };
        let offer = generate_offer(&asset, &request());
        assert!(!offer.rejected);
        assert_eq!(offer.kw_offered, 380.0 * FLEXIBLE_FRACTION);
    }

    #[test]
    fn rejects_when_load_too_small() {
        let asset = AssetState { asset_id: "hosp-2".into(), asset_type: "hospital".into(), current_load_kw: 5.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: true };
        let offer = generate_offer(&asset, &request());
        assert!(offer.rejected);
        assert_eq!(offer.kw_offered, 0.0);
    }

    #[test]
    fn rejects_when_offline() {
        let asset = AssetState { asset_id: "hosp-3".into(), asset_type: "hospital".into(), current_load_kw: 380.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: false };
        let offer = generate_offer(&asset, &request());
        assert!(offer.rejected);
    }
}
