//! Lagbot Executor — High-performance Polymarket order execution engine.
//!
//! Architecture: Python signal layer → Unix socket → Rust executor → CLOB API
//!
//! Responsibilities:
//! - Subscribe to Polymarket WebSocket for real-time order books
//! - Accept trade signals from Python via Unix socket
//! - Construct + sign + submit orders in <100μs
//! - Report fills back to Python

mod config;
mod clob;
mod ws;
mod ipc;
mod types;

use anyhow::Result;
use tracing::{info, warn};
use tracing_subscriber::EnvFilter;
use tokio::signal;

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("lagbot_executor=info".parse()?))
        .init();

    info!("Lagbot Executor v{}", env!("CARGO_PKG_VERSION"));

    // Load config
    let config = config::Config::from_env()?;
    info!(
        "Config loaded: clob={}, signature_type={}",
        config.clob_url, config.signature_type
    );

    // Initialize CLOB client
    let clob = clob::ClobClient::new(&config).await?;
    info!("CLOB client initialized");

    // Start subsystems
    let clob_for_ipc = clob.clone();
    let ipc_handle = tokio::spawn(async move {
        if let Err(e) = ipc::start_ipc_server(&config.ipc_socket_path, clob_for_ipc).await {
            warn!("IPC server error: {}", e);
        }
    });

    let ws_handle = tokio::spawn(async move {
        if let Err(e) = ws::start_market_ws().await {
            warn!("Market WebSocket error: {}", e);
        }
    });

    info!("Executor ready — listening on {}", config.ipc_socket_path);

    // Wait for shutdown signal
    signal::ctrl_c().await?;
    info!("Shutting down...");

    ipc_handle.abort();
    ws_handle.abort();

    Ok(())
}
