//! Direct port of agent_engines/settlement.py.

use uuid::Uuid;

use crate::contracts::{AgentOffer, ProposedAction, Trade};

pub fn build_trade(action: &ProposedAction, source_offer: &AgentOffer) -> Trade {
    Trade {
        trade_id: Uuid::new_v4().simple().to_string()[..8].to_string(),
        request_id: action.request_id.clone(),
        buyer_id: "grid".to_string(),
        seller_id: action.asset_id.clone(),
        kw_amount: action.kw_amount,
        price: source_offer.cost,
        action_id: action.action_id.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_trade_uses_offer_price() {
        let offer = AgentOffer { offer_id: "o1".into(), request_id: "r1".into(), asset_id: "acad-1".into(), offer_type: "hvac_reduction".into(), kw_offered: 28.0, cost: 4.2, rejected: false, rejection_reason: None };
        let action = ProposedAction { action_id: "a1".into(), request_id: "r1".into(), asset_id: "acad-1".into(), action_type: "hvac_reduction".into(), kw_amount: 28.0, source_offer_id: "o1".into() };
        let trade = build_trade(&action, &offer);
        assert_eq!(trade.price, 4.2);
        assert_eq!(trade.kw_amount, 28.0);
        assert_eq!(trade.buyer_id, "grid");
    }

    #[test]
    fn build_trade_respects_adjusted_kw_amount() {
        // Simulates a partially-approved action -- kw_amount already
        // overwritten with adjusted_kw_amount before this function sees it.
        let offer = AgentOffer { offer_id: "o1".into(), request_id: "r1".into(), asset_id: "acad-1".into(), offer_type: "hvac_reduction".into(), kw_offered: 28.0, cost: 4.2, rejected: false, rejection_reason: None };
        let action = ProposedAction { action_id: "a1".into(), request_id: "r1".into(), asset_id: "acad-1".into(), action_type: "hvac_reduction".into(), kw_amount: 14.0, source_offer_id: "o1".into() };
        let trade = build_trade(&action, &offer);
        assert_eq!(trade.kw_amount, 14.0); // NOT the original 28.0
    }
}
