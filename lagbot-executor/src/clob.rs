//! CLOB client wrapper using polyfill-rs for order construction + submission.

use anyhow::Result;
use rust_decimal::Decimal;
use std::time::Instant;
use tracing::info;

use crate::config::Config;
use crate::types::{Side, TradeResult, TradeSignal};

#[derive(Clone)]
pub struct ClobClient {
    // TODO: Replace with actual polyfill-rs::ClobClient once compiled
    // For now, this is the interface skeleton
    _config: Config,
}

impl ClobClient {
    pub async fn new(config: &Config) -> Result<Self> {
        // Initialize polyfill-rs client:
        //
        // let mut client = polyfill_rs::ClobClient::with_l1_headers(
        //     &config.clob_url,
        //     &config.private_key,
        //     config.chain_id,
        // );
        // let api_creds = client.create_or_derive_api_key(None).await?;
        // client.set_api_creds(api_creds);

        Ok(ClobClient {
            _config: config.clone(),
        })
    }

    pub async fn execute_signal(&self, signal: TradeSignal) -> TradeResult {
        let start = Instant::now();

        // TODO: Implement with polyfill-rs:
        //
        // let order_args = polyfill_rs::OrderArgs::new(
        //     &signal.token_id,
        //     signal.price,
        //     signal.size,
        //     match signal.side {
        //         Side::Buy => polyfill_rs::Side::BUY,
        //         Side::Sell => polyfill_rs::Side::SELL,
        //     },
        // );
        //
        // if signal.post_only {
        //     // Two-step maker: create_order → post_order with post_only flag
        //     let signed = self.client.create_order(&order_args).await?;
        //     let result = self.client.post_order(signed, Some(true)).await?;
        // } else {
        //     // Taker: create_and_post_order (FOK)
        //     let result = self.client.create_and_post_order(&order_args).await?;
        // }

        let latency = start.elapsed();
        info!(
            "Order submitted: {} @ {} x {} ({:?})",
            signal.slug, signal.price, signal.size, latency
        );

        TradeResult {
            success: false, // TODO: wire to real result
            order_id: String::new(),
            fill_price: Decimal::ZERO,
            fill_size: Decimal::ZERO,
            error: "Not yet implemented — skeleton only".to_string(),
            latency_us: latency.as_micros() as u64,
        }
    }

    pub async fn get_midpoint(&self, _token_id: &str) -> Result<Decimal> {
        // TODO: self.client.get_midpoint(token_id).await
        Ok(Decimal::ZERO)
    }

    pub async fn cancel_order(&self, _order_id: &str) -> Result<()> {
        // TODO: self.client.cancel(order_id).await
        Ok(())
    }
}
