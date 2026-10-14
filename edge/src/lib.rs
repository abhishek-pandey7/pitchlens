//! `pitchlens-edge`: the PitchLens broadcast edge.
//!
//! It serves the engine's live stream, `GET /api/v1/matches/{id}/stream?profile_id=&frames=&events=`,
//! with the same SSE event names, ids and `Last-Event-ID` semantics, from one upstream connection per
//! channel. See `docs/rust-expansion-plan.md` §5 and `docs/api.md` (the `/sync` contract).

pub mod config;
pub mod hub;
pub mod sse;
pub mod stats;

use std::collections::HashMap;
use std::convert::Infallible;
use std::future::{Future, IntoFuture};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::extract::{Path, Query, State};
use axum::http::{header, HeaderMap, HeaderValue, Method, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use bytes::Bytes;
use futures_util::Stream;
use serde_json::json;
use tokio::net::TcpListener;
use tokio::sync::broadcast::error::RecvError;
use tower_http::cors::{AllowOrigin, Any, CorsLayer};
use tracing::{info, warn};

pub use config::Config;
use hub::{Attached, Channel, ChannelKey, EngineError, Hub, Kind, ViewerGuard};

/// How many times a join retries when the ring can't cover its snapshot (continuity just broke).
const ATTACH_TRIES: usize = 5;

pub fn router(hub: Arc<Hub>) -> Router {
    let cors = cors_layer(&hub.cfg.cors_origins);
    Router::new()
        .route("/api/v1/matches/{match_id}/stream", get(stream))
        .route("/health", get(health))
        .route("/stats", get(stats))
        .layer(cors)
        .with_state(hub)
}

fn cors_layer(origins: &[String]) -> CorsLayer {
    let layer = CorsLayer::new()
        .allow_methods([Method::GET, Method::OPTIONS])
        .allow_headers([
            header::CONTENT_TYPE,
            header::AUTHORIZATION,
            header::HeaderName::from_static("last-event-id"),
        ]);
    if origins.iter().any(|o| o == "*") {
        return layer.allow_origin(Any);
    }
    let list: Vec<HeaderValue> = origins
        .iter()
        .filter_map(|o| HeaderValue::from_str(o).ok())
        .collect();
    layer.allow_origin(AllowOrigin::list(list))
}

/// Serve until `shutdown` resolves, then end every stream and stop (at most `grace` later).
pub async fn serve(
    listener: TcpListener,
    hub: Arc<Hub>,
    shutdown: impl Future<Output = ()> + Send + 'static,
    grace: Duration,
) -> std::io::Result<()> {
    let ticker = {
        let hub = hub.clone();
        tokio::spawn(async move {
            let mut every = tokio::time::interval(Duration::from_secs(1));
            loop {
                every.tick().await;
                hub.stats.tick(Instant::now());
            }
        })
    };
    let (stopping_tx, mut stopping_rx) = tokio::sync::watch::channel(false);
    let hub_for_signal = hub.clone();
    let server = axum::serve(listener, router(hub)).with_graceful_shutdown(async move {
        shutdown.await;
        info!("shutting down: closing streams");
        hub_for_signal.shutdown();
        let _ = stopping_tx.send(true);
    });
    let result = tokio::select! {
        r = server.into_future() => r,
        _ = async {
            let _ = stopping_rx.wait_for(|s| *s).await;
            tokio::time::sleep(grace).await;
        } => {
            warn!("graceful shutdown timed out");
            Ok(())
        }
    };
    ticker.abort();
    result
}

/// A boolean query flag, read the way the engine (FastAPI) reads one.
fn flag(q: &HashMap<String, String>, name: &str, default: bool) -> Result<bool, String> {
    match q.get(name) {
        None => Ok(default),
        Some(v) => match v.to_ascii_lowercase().as_str() {
            "true" | "1" | "yes" | "on" | "t" | "y" => Ok(true),
            "false" | "0" | "no" | "off" | "f" | "n" => Ok(false),
            _ => Err(format!("{name}: not a boolean: {v:?}")),
        },
    }
}

fn engine_error(e: EngineError) -> Response {
    match e {
        EngineError::Status(code, body) => {
            let status = StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_GATEWAY);
            (status, [(header::CONTENT_TYPE, "application/json")], body).into_response()
        }
        EngineError::Unavailable(why) => {
            warn!(error = %why, "engine unavailable");
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"detail": "the match engine is unavailable"})),
            )
                .into_response()
        }
    }
}

async fn stream(
    State(hub): State<Arc<Hub>>,
    Path(match_id): Path<String>,
    Query(q): Query<HashMap<String, String>>,
    headers: HeaderMap,
) -> Response {
    let key = match (flag(&q, "frames", false), flag(&q, "events", true)) {
        (Ok(frames), Ok(events)) => ChannelKey {
            match_id,
            profile_id: q.get("profile_id").cloned(),
            frames,
            events,
        },
        (Err(e), _) | (_, Err(e)) => {
            return (StatusCode::UNPROCESSABLE_ENTITY, Json(json!({"detail": e}))).into_response()
        }
    };
    // Like the engine: a Last-Event-ID that isn't a plain number is ignored.
    let last_event_id = headers
        .get("last-event-id")
        .and_then(|v| v.to_str().ok())
        .filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|v| v.parse::<u64>().ok());

    let existing = hub.join_existing(&key);
    if let (Some(last), Some((channel, _))) = (last_event_id, &existing) {
        if let Some(attached) = channel.attach(last, false) {
            hub.stats.resumes_local.fetch_add(1, Relaxed);
            let (channel, guard) = existing.unwrap();
            return sse_response(viewer(hub, channel, guard, None, attached, last));
        }
    }
    // Everyone else starts from a snapshot: a newcomer, or a resume the ring can't serve.
    let (channel, guard, mut snap) = match existing {
        Some((channel, guard)) => match hub.snapshot(&channel, false).await {
            Ok(snap) => (channel, guard, snap),
            Err(e) => return engine_error(e),
        },
        None => match hub.fetch_snapshot(&key, 0).await {
            Ok(snap) => {
                let (channel, guard) = hub.join_or_create(&key, &snap);
                (channel, guard, snap)
            }
            Err(e) => return engine_error(e),
        },
    };
    for attempt in 0..ATTACH_TRIES {
        if let Some(attached) = channel.attach(snap.last_id, true) {
            hub.stats.snapshot_joins.fetch_add(1, Relaxed);
            let after = snap.last_id;
            return sse_response(viewer(
                hub,
                channel,
                guard,
                Some(snap.bytes),
                attached,
                after,
            ));
        }
        tokio::time::sleep(Duration::from_millis(50 * attempt as u64)).await;
        snap = match hub.snapshot(&channel, true).await {
            Ok(snap) => snap,
            Err(e) => return engine_error(e),
        };
    }
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({"detail": "the stream is resynchronising; retry"})),
    )
        .into_response()
}

fn sse_response(body: impl Stream<Item = Result<Bytes, Infallible>> + Send + 'static) -> Response {
    let mut resp = Response::new(Body::from_stream(body));
    let h = resp.headers_mut();
    h.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream; charset=utf-8"),
    );
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    h.insert("x-accel-buffering", HeaderValue::from_static("no"));
    resp
}

enum Step {
    Send(Bytes),
    Resync,
    End,
}

/// One viewer's stream: the optional snapshot, the backlog, then the broadcast, skipping every id it
/// already has. Falling behind the broadcast, or a break upstream, means a fresh snapshot, never an
/// unbounded queue.
fn viewer(
    hub: Arc<Hub>,
    channel: Arc<Channel>,
    guard: ViewerGuard,
    first: Option<Bytes>,
    attached: Attached,
    after: u64,
) -> impl Stream<Item = Result<Bytes, Infallible>> + Send + 'static {
    async_stream::stream! {
        let _guard = guard;
        let stats = &hub.stats;
        let mut shutdown = hub.shutdown_signal();
        let ping_every = hub.cfg.ping;
        let mut ping = tokio::time::interval_at(tokio::time::Instant::now() + ping_every, ping_every);
        let mut after = after;
        let mut attached = attached;
        if let Some(snapshot) = first {
            stats.messages_out.fetch_add(1, Relaxed);
            yield Ok(snapshot);
        }
        'viewer: loop {
            for msg in std::mem::take(&mut attached.backlog) {
                if let Some(id) = msg.id {
                    if id <= after {
                        continue;
                    }
                    after = id;
                }
                stats.messages_out.fetch_add(1, Relaxed);
                yield Ok(msg.bytes.clone());
            }
            loop {
                let step = tokio::select! {
                    biased;
                    _ = shutdown.changed() => Step::End,
                    received = attached.rx.recv() => match received {
                        Ok(msg) if msg.kind == Kind::Resync => {
                            stats.resyncs_upstream.fetch_add(1, Relaxed);
                            Step::Resync
                        }
                        Ok(msg) => {
                            if let Some(id) = msg.id {
                                if id <= after {
                                    continue;
                                }
                                after = id;
                            }
                            stats.record_delay(msg.at.elapsed());
                            Step::Send(msg.bytes.clone())
                        }
                        Err(RecvError::Lagged(_)) => {
                            stats.resyncs_lagged.fetch_add(1, Relaxed);
                            Step::Resync
                        }
                        Err(RecvError::Closed) => Step::End,
                    },
                    _ = ping.tick() => Step::Send(sse::ping()),
                };
                match step {
                    Step::Send(bytes) => {
                        stats.messages_out.fetch_add(1, Relaxed);
                        stats.bytes_out.fetch_add(bytes.len() as u64, Relaxed);
                        yield Ok(bytes);
                    }
                    Step::End => break 'viewer,
                    Step::Resync => break,
                }
            }
            // Resync: a snapshot (shared with other viewers resyncing now), then the broadcast from there.
            let mut fresh = false;
            loop {
                if *shutdown.borrow() {
                    break 'viewer;
                }
                match hub.snapshot(&channel, fresh).await {
                    Ok(snap) => {
                        if let Some(next) = channel.attach(snap.last_id, true) {
                            attached = next;
                            after = snap.last_id;
                            stats.messages_out.fetch_add(1, Relaxed);
                            yield Ok(snap.bytes);
                            break;
                        }
                        fresh = true;
                        tokio::time::sleep(Duration::from_millis(50)).await;
                    }
                    Err(e) => {
                        warn!(channel = ?channel.key, error = ?e, "resync failed; retrying");
                        tokio::time::sleep(Duration::from_secs(1)).await;
                        yield Ok(sse::ping());
                    }
                }
            }
        }
    }
}

async fn health(State(hub): State<Arc<Hub>>) -> Json<serde_json::Value> {
    Json(json!({
        "status": "ok",
        "service": "pitchlens-edge",
        "version": env!("CARGO_PKG_VERSION"),
        "channels": hub.channel_count(),
        "viewers": hub.stats.viewers.load(Relaxed),
    }))
}

async fn stats(State(hub): State<Arc<Hub>>) -> Json<serde_json::Value> {
    let s = &hub.stats;
    let rates = *s.rates.lock().unwrap();
    let delay = s.delay_counts();
    let samples: u64 = delay.iter().sum();
    let mut channels: Vec<_> = hub.channels();
    channels.sort_by_key(|c| std::cmp::Reverse(c.viewers()));
    let top: Vec<_> = channels
        .iter()
        .take(50)
        .map(|c| {
            json!({
                "match_id": c.key.match_id,
                "profile_id": c.key.profile_id,
                "frames": c.key.frames,
                "events": c.key.events,
                "viewers": c.viewers(),
                "last_id": c.last_id(),
                "buffered": c.ring_len(),
            })
        })
        .collect();
    Json(json!({
        "channels": channels.len(),
        "viewers": s.viewers.load(Relaxed),
        "messages_in_per_s": (rates.in_per_s * 10.0).round() / 10.0,
        "messages_out_per_s": (rates.out_per_s * 10.0).round() / 10.0,
        "fanout_delay_ms": {
            "p50": stats::quantile_ms(&delay, 0.50),
            "p95": stats::quantile_ms(&delay, 0.95),
            "p99": stats::quantile_ms(&delay, 0.99),
            "samples": samples,
            "window_s": stats::DELAY_WINDOW.as_secs() * 2,
        },
        "totals": {
            "messages_in": s.messages_in.load(Relaxed),
            "messages_out": s.messages_out.load(Relaxed),
            "bytes_out": s.bytes_out.load(Relaxed),
            "viewers": s.viewers_total.load(Relaxed),
            "resumes_local": s.resumes_local.load(Relaxed),
            "snapshot_joins": s.snapshot_joins.load(Relaxed),
            "snapshot_fetches": s.snapshot_fetches.load(Relaxed),
            "resyncs_lagged": s.resyncs_lagged.load(Relaxed),
            "resyncs_upstream": s.resyncs_upstream.load(Relaxed),
            "upstream_connects": s.upstream_connects.load(Relaxed),
        },
        "top_channels": top,
    }))
}
