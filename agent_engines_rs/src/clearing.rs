//! Direct port of agent_engines/clearing.py.

use uuid::Uuid;

use crate::contracts::{AgentOffer, ProposedAction};
use crate::fairness::RecencyTracker;

const FAIRNESS_WEIGHT: f64 = 0.3;
const LOOKBACK: u64 = 5;

pub fn clear_offers_for_request(
    offers: &[AgentOffer],
    kw_needed: f64,
    recency: &mut RecencyTracker,
) -> Vec<ProposedAction> {
    let mut candidates: Vec<&AgentOffer> = offers
        .iter()
        .filter(|o| !o.rejected && o.kw_offered > 1e-9)
        .collect();

    candidates.sort_by(|a, b| {
        let ea = a.cost * (1.0 + FAIRNESS_WEIGHT * recency.count(&a.asset_id, LOOKBACK) as f64);
        let eb = b.cost * (1.0 + FAIRNESS_WEIGHT * recency.count(&b.asset_id, LOOKBACK) as f64);
        ea.partial_cmp(&eb).unwrap()
    });

    let mut actions = Vec::new();
    let mut remaining = kw_needed;

    for offer in candidates {
        if remaining <= 1e-9 {
            break;
        }
        let take = offer.kw_offered.min(remaining);
        actions.push(ProposedAction {
            action_id: Uuid::new_v4().simple().to_string()[..8].to_string(),
            request_id: offer.request_id.clone(),
            asset_id: offer.asset_id.clone(),
            action_type: offer.offer_type.clone(),
            kw_amount: (take * 1000.0).round() / 1000.0,
            source_offer_id: offer.offer_id.clone(),
        });
        remaining -= take;
        recency.record(&offer.asset_id);
    }

    actions
}
