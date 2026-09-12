//! Direct port of agent_engines/agents/ev_agent.py.
//!
//! One real difference from the Python version, worth being explicit
//! about: Python used a module-level mutable dict for EV_SECONDS_UNTIL_
//! DEADLINE. Rust doesn't have an equivalent "just mutate a global"
//! escape hatch without unsafe code or a static Mutex, so this takes the
//! deadlines map as a parameter instead — main.rs owns it as part of
//! shared app state (Arc<Mutex<...>>) and passes a reference in. Same
//! behavior, more explicit about where the mutable state actually lives.

use std::collections::HashMap;

use uuid::Uuid;

use crate::contracts::{AgentOffer, AssetState, FlexibilityRequest};

const FLEXIBLE_FRACTION: f64 = 0.70;
const PRICE_PER_KW: f64 = 2.80;
const URGENT_THRESHOLD_SECONDS: f64 = 300.0;

pub fn generate_offer(
    asset: &AssetState,
    request: &FlexibilityRequest,
    deadlines: &HashMap<String, f64>,
) -> AgentOffer {
    let offer_id = Uuid::new_v4().simple().to_string()[..8].to_string();

    if let Some(&deadline) = deadlines.get(&asset.asset_id) {
        if deadline < URGENT_THRESHOLD_SECONDS {
            return AgentOffer {
                offer_id,
                request_id: request.request_id.clone(),
                asset_id: asset.asset_id.clone(),
                offer_type: "ev_delay".to_string(),
                kw_offered: 0.0,
                cost: 0.0,
                rejected: true,
                rejection_reason: Some(format!(
                    "vehicle must resume charging within {:.0}s",
                    deadline
                )),
            };
        }
    }

    let offerable_kw = (asset.current_load_kw * FLEXIBLE_FRACTION * 1000.0).round() / 1000.0;
    if !asset.online || offerable_kw < 0.5 {
        return AgentOffer {
            offer_id,
            request_id: request.request_id.clone(),
            asset_id: asset.asset_id.clone(),
            offer_type: "ev_delay".to_string(),
            kw_offered: 0.0,
            cost: 0.0,
            rejected: true,
            rejection_reason: Some("no active charging session to delay".to_string()),
        };
    }

    AgentOffer {
        offer_id,
        request_id: request.request_id.clone(),
        asset_id: asset.asset_id.clone(),
        offer_type: "ev_delay".to_string(),
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
        FlexibilityRequest { request_id: "r".into(), feeder_id: "F2".into(), kw_needed: 10.0, deadline_seconds: 180.0, reason: "t".into() }
    }

    #[test]
    fn offers_when_no_urgency_set() {
        let asset = AssetState { asset_id: "ev-1".into(), asset_type: "ev".into(), current_load_kw: 20.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: true };
        let offer = generate_offer(&asset, &request(), &HashMap::new());
        assert!(!offer.rejected);
        assert_eq!(offer.kw_offered, 20.0 * FLEXIBLE_FRACTION);
    }

    #[test]
    fn rejects_near_deadline() {
        let asset = AssetState { asset_id: "ev-urgent".into(), asset_type: "ev".into(), current_load_kw: 20.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: true };
        let mut deadlines = HashMap::new();
        deadlines.insert("ev-urgent".to_string(), 100.0);
        let offer = generate_offer(&asset, &request(), &deadlines);
        assert!(offer.rejected);
    }

    #[test]
    fn rejects_when_no_active_charging() {
        let asset = AssetState { asset_id: "ev-idle".into(), asset_type: "ev".into(), current_load_kw: 0.0, current_gen_kw: 0.0, soc_percent: None, min_reserve_percent: None, online: true };
        let offer = generate_offer(&asset, &request(), &HashMap::new());
        assert!(offer.rejected);
    }
}
