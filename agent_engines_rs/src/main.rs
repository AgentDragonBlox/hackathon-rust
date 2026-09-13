//! Direct port of agent_engines/main.py. Same routes, same JSON shapes,
//! same statefulness caveat (call /agents/offers before /agents/clear_market
//! before /agents/settle, for a given request_id, within one run of this
//! service) — a Python client (grid_engine, orchestrator) talking to this
//! shouldn't be able to tell the difference from the FastAPI version.
//!
//! Python's module-level globals (_recency, _pending_requests, _offer_cache)
//! become one Mutex-guarded AppState here — same shared mutable state,
//! made explicit instead of implicit, which is the real, honest "why
//! Rust" difference for this file rather than a raw speed claim.

mod agents;
mod clearing;
mod contracts;
mod fairness;
mod reserve;
mod settlement;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Json},
    routing::{get, post},
    Router,
};
use serde_json::json;
use tower_http::cors::{Any, CorsLayer};

use contracts::{
    AgentOffer, AssetState, ClearMarketRequest, FlexibilityRequest, OffersRequest, ProposedAction,
    ReserveContract, Trade,
};
use fairness::RecencyTracker;

struct AppState {
    recency: RecencyTracker,
    pending_requests: HashMap<String, FlexibilityRequest>,
    offer_cache: HashMap<String, AgentOffer>,
    ev_deadlines: HashMap<String, f64>,
}

type SharedState = Arc<Mutex<AppState>>;

fn dispatch_agent(
    asset: &AssetState,
    request: &FlexibilityRequest,
    ev_deadlines: &HashMap<String, f64>,
) -> Option<AgentOffer> {
    match asset.asset_type.as_str() {
        "hospital" => Some(agents::hospital::generate_offer(asset, request)),
        "academic" => Some(agents::academic::generate_offer(asset, request)),
        "ev" => Some(agents::ev::generate_offer(asset, request, ev_deadlines)),
        "battery" => Some(agents::battery::generate_offer(asset, request)),
        "factory" => Some(agents::factory::generate_offer(asset, request)),
        _ => None, // e.g. "solar", "utility" -- no agent yet, same as Python
    }
}

async fn get_offers(
    State(state): State<SharedState>,
    Json(payload): Json<OffersRequest>,
) -> Json<Vec<AgentOffer>> {
    let mut state = state.lock().unwrap();
    let mut offers = Vec::new();

    for request in &payload.requests {
        state
            .pending_requests
            .insert(request.request_id.clone(), request.clone());

        let feeder = payload
            .grid_state
            .feeders
            .iter()
            .find(|f| f.feeder_id == request.feeder_id);
        let connected: Vec<&String> = feeder.map(|f| f.connected_assets.iter().collect()).unwrap_or_default();

        for asset in &payload.grid_state.assets {
            if !connected.iter().any(|id| **id == asset.asset_id) {
                continue;
            }
            if let Some(offer) = dispatch_agent(asset, request, &state.ev_deadlines) {
                state.offer_cache.insert(offer.offer_id.clone(), offer.clone());
                offers.push(offer);
            }
        }
    }

    Json(offers)
}

async fn clear_market(
    State(state): State<SharedState>,
    Json(payload): Json<ClearMarketRequest>,
) -> Json<Vec<ProposedAction>> {
    let mut state = state.lock().unwrap();

    // Preserve request order: Python dicts do, and grid validation is cumulative.
    let mut by_request: Vec<(String, Vec<AgentOffer>)> = Vec::new();
    for offer in payload.offers {
        if let Some((_, offers)) = by_request.iter_mut().find(|(id, _)| *id == offer.request_id) {
            offers.push(offer);
        } else {
            by_request.push((offer.request_id.clone(), vec![offer]));
        }
    }

    let mut actions = Vec::new();
    for (request_id, offers) in by_request {
        let kw_needed = state
            .pending_requests
            .get(&request_id)
            .map(|r| r.kw_needed)
            .unwrap_or_else(|| offers.iter().map(|o| o.kw_offered).sum());

        let mut cleared = clearing::clear_offers_for_request(&offers, kw_needed, &mut state.recency);
        actions.append(&mut cleared);
    }

    Json(actions)
}

async fn settle(
    State(state): State<SharedState>,
    Json(actions): Json<Vec<ProposedAction>>,
) -> Result<Json<Vec<Trade>>, (StatusCode, Json<serde_json::Value>)> {
    let state = state.lock().unwrap();
    let mut trades = Vec::new();

    for action in &actions {
        match state.offer_cache.get(&action.source_offer_id) {
            Some(offer) => trades.push(settlement::build_trade(action, offer)),
            None => {
                return Err((
                    StatusCode::NOT_FOUND,
                    Json(json!({
                        "detail": format!(
                            "no cached offer for source_offer_id={} — was /agents/offers called first in this server run?",
                            action.source_offer_id
                        )
                    })),
                ));
            }
        }
    }

    Ok(Json(trades))
}

async fn reserve(Query(params): Query<contracts::ReserveQuery>) -> Json<ReserveContract> {
    Json(reserve::create_reserve_contract(
        params.asset_id,
        params.reserved_kw,
        params.valid_until,
        params.purpose,
    ))
}

async fn health() -> impl IntoResponse {
    Json(json!({"status": "ok", "engine": "rust", "service": "agent_engines", "version": env!("CARGO_PKG_VERSION")}))
}

async fn set_ev_deadlines(
    State(state): State<SharedState>,
    Json(deadlines): Json<HashMap<String, f64>>,
) -> Json<serde_json::Value> {
    let mut state = state.lock().unwrap();
    let keys: Vec<String> = deadlines.keys().cloned().collect();
    state.ev_deadlines.extend(deadlines);
    Json(json!({"updated": keys}))
}

fn create_app() -> Router {
    let shared_state: SharedState = Arc::new(Mutex::new(AppState {
        recency: RecencyTracker::default(),
        pending_requests: HashMap::new(),
        offer_cache: HashMap::new(),
        ev_deadlines: HashMap::new(),
    }));

    // CORS: permissive for local dev/hackathon purposes -- this lets a
    // browser-based client (the Svelte dashboard, running on a different
    // port) actually read responses from this API. Without this, every
    // browser fetch() call silently fails with no useful error beyond
    // "unreachable" -- the request succeeds server-side, but the browser
    // refuses to hand the response to JavaScript. Curl/server-side fetch
    // (Node, Python) are NOT subject to this -- which is exactly why this
    // gap wasn't caught by the httpx-based testing done during development.
    let cors = CorsLayer::new().allow_origin(Any).allow_methods(Any).allow_headers(Any);

    Router::new()
        .route("/health", get(health))
        .route("/agents/offers", post(get_offers))
        .route("/agents/clear_market", post(clear_market))
        .route("/agents/settle", post(settle))
        .route("/agents/reserve", post(reserve))
        .route("/agents/debug/ev_deadlines", post(set_ev_deadlines))
        .layer(cors)
        .with_state(shared_state)
}

#[tokio::main]
async fn main() {
    let app = create_app();
    let port = std::env::var("PORT").unwrap_or_else(|_| "8002".to_string());
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await.unwrap();
    println!("agent_engines (Rust) listening on :{port}");
    axum::serve(listener, app).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    async fn post(app: Router, path: &str, payload: serde_json::Value) -> (StatusCode, serde_json::Value) {
        let response = app.oneshot(Request::post(path).header("content-type", "application/json")
            .body(Body::from(payload.to_string())).unwrap()).await.unwrap();
        let status = response.status();
        let body = response.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&body).unwrap())
    }

    #[tokio::test]
    async fn real_market_settles_only_adjusted_amount_and_protects_hospital() {
        let app = create_app();
        let payload = json!({
            "grid_state": {"timestamp": "2016-06-06T06:00:00Z", "assets": [
                {"asset_id": "hosp-1", "asset_type": "hospital", "current_load_kw": 380.0},
                {"asset_id": "acad-1", "asset_type": "academic", "current_load_kw": 240.0}
            ], "feeders": [{"feeder_id": "F1", "loading_percent": 105.0, "connected_assets": ["hosp-1", "acad-1"]}]},
            "requests": [{"request_id": "request-1", "feeder_id": "F1", "kw_needed": 20.0,
                          "deadline_seconds": 120.0, "reason": "test overload"}]
        });
        let (status, offers) = post(app.clone(), "/agents/offers", payload).await;
        assert_eq!(status, StatusCode::OK);
        let hospital = offers.as_array().unwrap().iter().find(|o| o["asset_id"] == "hosp-1").unwrap();
        // Existing policy offers only the assumed 5% non-critical hospital load.
        assert_eq!(hospital["kw_offered"], 19.0);
        let (_, mut actions) = post(app.clone(), "/agents/clear_market", json!({"offers": offers})).await;
        assert_eq!(actions[0]["asset_id"], "acad-1");
        actions[0]["kw_amount"] = json!(7.5);
        let (status, trades) = post(app, "/agents/settle", actions).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(trades[0]["kw_amount"], 7.5);
        assert_eq!(trades[0]["seller_id"], "acad-1");
        assert_eq!(trades[0]["price"], 4.2); // Contract stores the offer's unit price.
    }

    #[tokio::test]
    async fn missing_cached_offer_does_not_create_trade() {
        let (status, _) = post(create_app(), "/agents/settle", json!([{
            "action_id": "a", "request_id": "r", "asset_id": "acad-1", "action_type": "hvac_reduction",
            "kw_amount": 10.0, "source_offer_id": "missing"
        }])).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
    }

    #[tokio::test]
    async fn clearing_keeps_request_order_for_cumulative_grid_validation() {
        let offers: Vec<_> = ["z", "a", "m"].iter().map(|id| json!({
            "offer_id": id, "request_id": id, "asset_id": "acad-1", "offer_type": "hvac_reduction",
            "kw_offered": 10.0, "cost": 1.0
        })).collect();
        let (_, actions) = post(create_app(), "/agents/clear_market", json!({"offers": offers})).await;
        let ids: Vec<_> = actions.as_array().unwrap().iter().map(|a| a["request_id"].as_str().unwrap()).collect();
        assert_eq!(ids, vec!["z", "a", "m"]);
    }
}
