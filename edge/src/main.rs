//! `pitchlens-edge`: configured by `ENGINE_URL`, `PORT` and `CORS_ORIGINS` (see `Config`).

use std::net::SocketAddr;
use std::time::Duration;

use pitchlens_edge::{hub::Hub, serve, Config};
use tracing::info;
use tracing_subscriber::EnvFilter;

async fn shutdown_signal() {
    let ctrl_c = async {
        let _ = tokio::signal::ctrl_c().await;
    };
    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut s) => {
                s.recv().await;
            }
            Err(_) => std::future::pending::<()>().await,
        }
    };
    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();
    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}

/// `pitchlens-edge healthcheck`: exit 0 when this container's edge answers `/health` (the distroless
/// image has no shell or curl for a Docker HEALTHCHECK).
async fn healthcheck() -> ! {
    let port = std::env::var("PORT").unwrap_or_else(|_| "8080".into());
    let ok = reqwest::Client::new()
        .get(format!("http://127.0.0.1:{port}/health"))
        .timeout(Duration::from_secs(4))
        .send()
        .await
        .is_ok_and(|r| r.status().is_success());
    std::process::exit(if ok { 0 } else { 1 })
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    if std::env::args().nth(1).as_deref() == Some("healthcheck") {
        healthcheck().await;
    }
    tracing_subscriber::fmt()
        .json()
        .flatten_event(true)
        .with_current_span(false)
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .init();
    let cfg = match Config::from_env() {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!("pitchlens-edge: bad configuration: {e}");
            std::process::exit(2);
        }
    };
    let addr = SocketAddr::from(([0, 0, 0, 0], cfg.port));
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, engine = %cfg.engine_url, cors = ?cfg.cors_origins, "pitchlens-edge listening");
    let hub = Hub::new(cfg);
    serve(listener, hub, shutdown_signal(), Duration::from_secs(10)).await?;
    info!("stopped");
    Ok(())
}
