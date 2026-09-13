//! Direct port of agent_engines/agents/battery_agent.py.

use uuid::Uuid;

use crate::contracts::{AgentOffer, AssetState, FlexibilityRequest};

const PRICE_PER_KW: f64 = 5.10;
const DEFAULT_RESERVE_PERCENT: f64 = 20.0;
const ASSUMED_CAPACITY_KWH: f64 = 100.0;
const ASSUMED_MAX_DISCHARGE_KW: f64 = 30.0;

pub fn generate_offer(asset: &AssetState, request: &FlexibilityRequest) -> AgentOffer {
    let offer_id = Uuid::new_v4().simple().to_string()[..8].to_string();
    let soc = asset.soc_percent.unwrap_or(0.0);
    let reserve = asset.min_reserve_percent.unwrap_or(DEFAULT_RESERVE_PERCENT);

    let available_percent = soc - reserve;
    if !asset.online || available_percent <= 0.0 {
        return AgentOffer {
            offer_id,
            request_id: request.request_id.clone(),
            asset_id: asset.asset_id.clone(),
            offer_type: "battery_discharge".to_string(),
            kw_offered: 0.0,
            cost: 0.0,
            rejected: true,
            rejection_reason: Some(format!("at or below reserve floor ({:.0}%)", reserve)),
        };
    }

    let available_kwh = (available_percent / 100.0) * ASSUMED_CAPACITY_KWH;
    let offerable_kw = ASSUMED_MAX_DISCHARGE_KW.min(available_kwh);

    AgentOffer {
        offer_id,
        request_id: request.request_id.clone(),
        asset_id: asset.asset_id.clone(),
        offer_type: "battery_discharge".to_string(),
        kw_offered: (offerable_kw * 1000.0).round() / 1000.0,
        cost: PRICE_PER_KW,
        rejected: false,
        rejection_reason: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn offers_when_above_reserve() {
        let asset = AssetState { asset_id: "batt-1".into(), asset_type: "battery".into(), current_load_kw: 0.0, current_gen_kw: 0.0, soc_percent: Some(73.0), min_reserve_percent: Some(20.0), online: true };
        let request = FlexibilityRequest { request_id: "r".into(), feeder_id: "F1".into(), kw_needed: 10.0, deadline_seconds: 180.0, reason: "t".into() };
        let offer = generate_offer(&asset, &request);
        assert!(!offer.rejected);
        assert!(offer.kw_offered > 0.0);
    }

    #[test]
    fn rejects_at_reserve_floor() {
        let asset = AssetState { asset_id: "batt-2".into(), asset_type: "battery".into(), current_load_kw: 0.0, current_gen_kw: 0.0, soc_percent: Some(20.0), min_reserve_percent: Some(20.0), online: true };
        let request = FlexibilityRequest { request_id: "r".into(), feeder_id: "F1".into(), kw_needed: 10.0, deadline_seconds: 180.0, reason: "t".into() };
        let offer = generate_offer(&asset, &request);
        assert!(offer.rejected);
    }

    #[test]
    fn uses_default_reserve_when_unset() {
        let asset = AssetState { asset_id: "batt-3".into(), asset_type: "battery".into(), current_load_kw: 0.0, current_gen_kw: 0.0, soc_percent: Some(50.0), min_reserve_percent: None, online: true };
        let request = FlexibilityRequest { request_id: "r".into(), feeder_id: "F1".into(), kw_needed: 10.0, deadline_seconds: 180.0, reason: "t".into() };
        let offer = generate_offer(&asset, &request);
        assert!(!offer.rejected); // 50% > default 20% reserve
    }
}
