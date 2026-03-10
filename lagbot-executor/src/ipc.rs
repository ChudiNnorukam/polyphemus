//! Unix socket IPC server for Python → Rust communication.
//!
//! Protocol: newline-delimited JSON over Unix domain socket.
//! Python sends TradeSignal JSON, Rust responds with TradeResult JSON.

use anyhow::Result;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixListener;
use tracing::{info, warn};

use crate::clob::ClobClient;
use crate::types::{TradeResult, TradeSignal};

pub async fn start_ipc_server(socket_path: &str, clob: ClobClient) -> Result<()> {
    // Remove stale socket file
    let _ = std::fs::remove_file(socket_path);

    let listener = UnixListener::bind(socket_path)?;
    info!("IPC server listening on {}", socket_path);

    loop {
        let (stream, _) = listener.accept().await?;
        let clob = clob.clone();

        tokio::spawn(async move {
            let (reader, mut writer) = stream.into_split();
            let mut reader = BufReader::new(reader);
            let mut line = String::new();

            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) => break, // EOF
                    Ok(_) => {
                        let trimmed = line.trim();
                        if trimmed.is_empty() {
                            continue;
                        }

                        // Parse signal
                        let result = match serde_json::from_str::<TradeSignal>(trimmed) {
                            Ok(signal) => {
                                info!("IPC signal received: {} {}", signal.slug, signal.source);
                                clob.execute_signal(signal).await
                            }
                            Err(e) => {
                                warn!("IPC parse error: {}", e);
                                TradeResult {
                                    success: false,
                                    order_id: String::new(),
                                    fill_price: rust_decimal::Decimal::ZERO,
                                    fill_size: rust_decimal::Decimal::ZERO,
                                    error: format!("Parse error: {}", e),
                                    latency_us: 0,
                                }
                            }
                        };

                        // Send result back
                        let response = serde_json::to_string(&result).unwrap_or_default();
                        if let Err(e) = writer.write_all(format!("{}\n", response).as_bytes()).await
                        {
                            warn!("IPC write error: {}", e);
                            break;
                        }
                    }
                    Err(e) => {
                        warn!("IPC read error: {}", e);
                        break;
                    }
                }
            }
        });
    }
}
