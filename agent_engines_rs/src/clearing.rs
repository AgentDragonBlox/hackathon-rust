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

#[cfg(test)]
mod tests {
    use super::*;

    fn offer(asset_id: &str, kw: f64, cost: f64) -> AgentOffer {
        AgentOffer {
            offer_id: format!("offer-{asset_id}"),
            request_id: "req-1".into(),
            asset_id: asset_id.into(),
            offer_type: "load_reduction".into(),
            kw_offered: kw,
            cost,
            rejected: false,
            rejection_reason: None,
        }
    }

    #[test]
    fn merit_order_picks_cheapest_first() {
        let offers = vec![offer("expensive", 50.0, 10.0), offer("cheap", 50.0, 2.0)];
        let mut recency = RecencyTracker::default();
        let actions = clear_offers_for_request(&offers, 30.0, &mut recency);
        assert_eq!(actions.len(), 1);
        assert_eq!(actions[0].asset_id, "cheap");
        assert_eq!(actions[0].kw_amount, 30.0);
    }

    #[test]
    fn partial_fill_on_last_offer_taken() {
        let offers = vec![offer("a", 20.0, 1.0), offer("b", 20.0, 2.0)];
        let mut recency = RecencyTracker::default();
        let actions = clear_offers_for_request(&offers, 25.0, &mut recency);
        assert_eq!(actions[0].asset_id, "a");
        assert_eq!(actions[0].kw_amount, 20.0);
        assert_eq!(actions[1].asset_id, "b");
        assert_eq!(actions[1].kw_amount, 5.0);
    }

    #[test]
    fn rejected_offers_excluded() {
        let mut rejected = offer("x", 0.0, 0.0);
        rejected.rejected = true;
        let offers = vec![rejected, offer("ok", 20.0, 1.0)];
        let mut recency = RecencyTracker::default();
        let actions = clear_offers_for_request(&offers, 10.0, &mut recency);
        assert_eq!(actions.len(), 1);
        assert_eq!(actions[0].asset_id, "ok");
    }

    #[test]
    fn fairness_penalizes_recently_used_asset() {
        let offers = vec![offer("cheap", 20.0, 2.0), offer("pricier", 20.0, 3.0)];
        let mut recency = RecencyTracker::default();
        for _ in 0..3 {
            recency.record("cheap");
        }
        let actions = clear_offers_for_request(&offers, 15.0, &mut recency);
        // effective price of "cheap" = 2.0 * (1 + 0.3*3) = 3.8, above "pricier"'s 3.0
        assert_eq!(actions[0].asset_id, "pricier");
    }
}
