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
