//! Configuration from environment variables.

use anyhow::{Context, Result};

#[derive(Debug, Clone)]
pub struct Config {
    pub private_key: String,
    pub wallet_address: String,
    pub clob_api_key: String,
    pub clob_secret: String,
    pub clob_passphrase: String,
    pub clob_url: String,
    pub chain_id: u64,
    pub signature_type: u8,
    pub ipc_socket_path: String,
}

impl Config {
    pub fn from_env() -> Result<Self> {
        dotenvy::dotenv().ok();

        Ok(Config {
            private_key: std::env::var("PRIVATE_KEY").context("PRIVATE_KEY required")?,
            wallet_address: std::env::var("WALLET_ADDRESS").context("WALLET_ADDRESS required")?,
            clob_api_key: std::env::var("CLOB_API_KEY").context("CLOB_API_KEY required")?,
            clob_secret: std::env::var("CLOB_SECRET").context("CLOB_SECRET required")?,
            clob_passphrase: std::env::var("CLOB_PASSPHRASE").context("CLOB_PASSPHRASE required")?,
            clob_url: std::env::var("CLOB_URL")
                .unwrap_or_else(|_| "https://clob.polymarket.com".to_string()),
            chain_id: std::env::var("POLYGON_CHAIN_ID")
                .unwrap_or_else(|_| "137".to_string())
                .parse()
                .context("Invalid POLYGON_CHAIN_ID")?,
            signature_type: std::env::var("SIGNATURE_TYPE")
                .unwrap_or_else(|_| "0".to_string())
                .parse()
                .context("Invalid SIGNATURE_TYPE")?,
            ipc_socket_path: std::env::var("IPC_SOCKET_PATH")
                .unwrap_or_else(|_| "/tmp/lagbot-executor.sock".to_string()),
        })
    }
}
