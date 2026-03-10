//! Shared types for IPC communication between Python and Rust.

use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

/// Signal from Python to Rust: "place this order"
#[derive(Debug, Serialize, Deserialize)]
pub struct TradeSignal {
    pub token_id: String,
    pub price: Decimal,
    pub size: Decimal,
    pub side: Side,
    pub slug: String,
    pub source: String, // "momentum", "window_delta", etc.
    pub post_only: bool,
}

/// Result from Rust to Python: "order placed/filled"
#[derive(Debug, Serialize, Deserialize)]
pub struct TradeResult {
    pub success: bool,
    pub order_id: String,
    pub fill_price: Decimal,
    pub fill_size: Decimal,
    pub error: String,
    pub latency_us: u64, // microseconds from signal to submission
}

#[derive(Debug, Serialize, Deserialize, Clone, Copy)]
pub enum Side {
    Buy,
    Sell,
}

/// Order book snapshot from WebSocket
#[derive(Debug, Clone)]
pub struct OrderBook {
    pub token_id: String,
    pub bids: Vec<PriceLevel>,
    pub asks: Vec<PriceLevel>,
    pub midpoint: Decimal,
    pub timestamp: u64,
}

#[derive(Debug, Clone)]
pub struct PriceLevel {
    pub price: Decimal,
    pub size: Decimal,
}
