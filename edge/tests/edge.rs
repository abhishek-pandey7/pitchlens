//! The edge against a fake engine (an axum server with the engine's `/stream` and `/sync` semantics):
//! joining via a snapshot, resuming from the ring, resyncing a lagging viewer, reconnecting upstream,
//! tearing idle channels down, and shutting down gracefully.

use std::collections::VecDeque;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use bytes::Bytes;
use futures_util::{Stream, StreamExt};
use pitchlens_edge::hub::Hub;
use pitchlens_edge::sse::{self, Event, Parser};
use pitchlens_edge::{serve, Config};
use tokio::net::TcpListener;
use tokio::sync::broadcast;

// ------------------------------------------------------------------------------------------ fake engine

#[derive(Clone, Debug)]
enum Out {
    Msg(Option<u64>, String, String),
    Kick, // end every open stream (the engine dropped its connections)
}

struct Log {
    msgs: VecDeque<(u64, String, String)>,
    next_id: u64,
}

struct Fake {
    log: Mutex<Log>,
    tx: broadcast::Sender<Out>,
    open: AtomicUsize,
    connects: AtomicUsize,
    syncs: AtomicUsize,
    last_event_ids: Mutex<Vec<Option<String>>>,
}

struct OpenGuard(Arc<Fake>);
impl Drop for OpenGuard {
    fn drop(&mut self) {
        self.0.open.fetch_sub(1, SeqCst);
    }
}

impl Fake {
    fn new() -> Arc<Self> {
        Arc::new(Self {
            log: Mutex::new(Log {
                msgs: VecDeque::new(),
                next_id: 1,
            }),
            tx: broadcast::channel(100_000).0,
            open: AtomicUsize::new(0),
            connects: AtomicUsize::new(0),
            syncs: AtomicUsize::new(0),
            last_event_ids: Mutex::new(Vec::new()),
        })
    }

    fn publish(&self, event: &str, data: &str) -> u64 {
        let mut log = self.log.lock().unwrap();
        let id = log.next_id;
        log.next_id += 1;
        log.msgs.push_back((id, event.into(), data.into()));
        let _ = self.tx.send(Out::Msg(Some(id), event.into(), data.into()));
        id
    }

    fn frame(&self) {
        let _log = self.log.lock().unwrap();
        let _ = self
            .tx
            .send(Out::Msg(None, "frame".into(), "{\"f\":1}".into()));
    }

    fn last_id(&self) -> u64 {
        self.log.lock().unwrap().next_id - 1
    }

    /// Forget the log, so a resume can't be served (as when a gap is older than the engine keeps).
    fn forget(&self) {
        self.log.lock().unwrap().msgs.clear();
    }

    fn kick(&self) {
        let _ = self.tx.send(Out::Kick);
    }

    fn sync_data(n: u64) -> String {
        format!("{{\"n\":{n}}}")
    }
}

async fn fake_sync(State(fake): State<Arc<Fake>>, Path(id): Path<String>) -> Response {
    if id == "nope" {
        return (
            StatusCode::NOT_FOUND,
            "{\"detail\":\"unknown match 'nope'\"}",
        )
            .into_response();
    }
    fake.syncs.fetch_add(1, SeqCst);
    let n = fake.last_id();
    format!("{{\"last_id\":{n},\"sync\":{}}}", Fake::sync_data(n)).into_response()
}

async fn fake_stream(
    State(fake): State<Arc<Fake>>,
    Path(id): Path<String>,
    headers: HeaderMap,
) -> Response {
    if id == "nope" {
        return (
            StatusCode::NOT_FOUND,
            "{\"detail\":\"unknown match 'nope'\"}",
        )
            .into_response();
    }
    let last = headers
        .get("last-event-id")
        .and_then(|v| v.to_str().ok())
        .map(String::from);
    fake.last_event_ids.lock().unwrap().push(last.clone());
    let resume = last.and_then(|v| v.parse::<u64>().ok());
    let (initial, mut rx) = {
        let log = fake.log.lock().unwrap();
        let oldest = log.msgs.front().map(|m| m.0).unwrap_or(log.next_id);
        let initial: Vec<Bytes> = match resume {
            Some(l) if l + 1 >= oldest => log
                .msgs
                .iter()
                .filter(|m| m.0 > l)
                .map(|(id, e, d)| sse::encode(Some(*id), e, d))
                .collect(),
            _ => vec![sse::encode(None, "sync", &Fake::sync_data(log.next_id - 1))],
        };
        (initial, fake.tx.subscribe())
    };
    fake.connects.fetch_add(1, SeqCst);
    fake.open.fetch_add(1, SeqCst);
    let guard = OpenGuard(fake.clone());
    let body = async_stream::stream! {
        let _guard = guard;
        for b in initial {
            yield Ok::<_, std::convert::Infallible>(b);
        }
        let mut ping = tokio::time::interval(Duration::from_millis(50));
        loop {
            tokio::select! {
                m = rx.recv() => match m {
                    Ok(Out::Msg(id, e, d)) => yield Ok(sse::encode(id, &e, &d)),
                    Ok(Out::Kick) | Err(_) => break,
                },
                _ = ping.tick() => yield Ok(Bytes::from_static(b": ping\r\n\r\n")),
            }
        }
    };
    Response::builder()
        .header("content-type", "text/event-stream")
        .body(Body::from_stream(body))
        .unwrap()
}

async fn start_fake() -> (Arc<Fake>, String) {
    let fake = Fake::new();
    let app = Router::new()
        .route("/api/v1/matches/{id}/sync", get(fake_sync))
        .route("/api/v1/matches/{id}/stream", get(fake_stream))
        .with_state(fake.clone());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (fake, format!("http://{addr}"))
}

// ------------------------------------------------------------------------------------------ the edge

struct Edge {
    hub: Arc<Hub>,
    base: String,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
    server: tokio::task::JoinHandle<std::io::Result<()>>,
}

fn test_config(engine_url: String) -> Config {
    Config {
        engine_url,
        port: 0,
        cors_origins: vec!["http://allowed.test".into()],
        idle_close: Duration::from_millis(400),
        ring_size: 50,
        broadcast_capacity: 1024,
        // Short pings: a viewer that hung up is noticed at the next write.
        ping: Duration::from_millis(100),
        snapshot_ttl: Duration::from_millis(200),
        upstream_idle_timeout: Duration::from_secs(5),
    }
}

async fn start_edge(cfg: Config) -> Edge {
    let hub = Hub::new(cfg);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr: SocketAddr = listener.local_addr().unwrap();
    let (stop, stopped) = tokio::sync::oneshot::channel::<()>();
    let server = tokio::spawn(serve(
        listener,
        hub.clone(),
        async move {
            let _ = stopped.await;
        },
        Duration::from_secs(5),
    ));
    Edge {
        hub,
        base: format!("http://{addr}"),
        stop: Some(stop),
        server,
    }
}

struct Viewer {
    body: std::pin::Pin<Box<dyn Stream<Item = reqwest::Result<Bytes>> + Send>>,
    parser: Parser,
    pending: VecDeque<Event>,
}

impl Viewer {
    async fn open(edge: &Edge, query: &str, last_event_id: Option<u64>) -> Viewer {
        let mut req =
            reqwest::Client::new().get(format!("{}/api/v1/matches/m1/stream{query}", edge.base));
        if let Some(id) = last_event_id {
            req = req.header("last-event-id", id.to_string());
        }
        let resp = req.send().await.unwrap();
        assert_eq!(resp.status(), 200);
        assert!(resp.headers()["content-type"]
            .to_str()
            .unwrap()
            .starts_with("text/event-stream"));
        assert_eq!(resp.headers()["x-accel-buffering"], "no");
        Viewer {
            body: Box::pin(resp.bytes_stream()),
            parser: Parser::new(),
            pending: VecDeque::new(),
        }
    }

    async fn next(&mut self) -> Option<Event> {
        loop {
            if let Some(e) = self.pending.pop_front() {
                return Some(e);
            }
            match tokio::time::timeout(Duration::from_secs(10), self.body.next()).await {
                Ok(Some(Ok(chunk))) => self.pending.extend(self.parser.feed(&chunk)),
                Ok(_) => return None,
                Err(_) => panic!("no message within 10 s"),
            }
        }
    }

    /// Messages until (and including) the one with id `until`.
    async fn until(&mut self, until: u64) -> Vec<Event> {
        let mut out = Vec::new();
        loop {
            let e = self.next().await.expect("stream ended");
            let done = e.id == Some(until);
            out.push(e);
            if done {
                return out;
            }
        }
    }
}

fn sync_n(e: &Event) -> u64 {
    assert_eq!(e.event, "sync");
    assert_eq!(e.id, None, "a snapshot sync has no id, like the engine's");
    let v: serde_json::Value = serde_json::from_str(&e.data).unwrap();
    v["n"].as_u64().unwrap()
}

fn ids(events: &[Event]) -> Vec<u64> {
    events.iter().filter_map(|e| e.id).collect()
}

async fn eventually(what: &str, mut check: impl FnMut() -> bool) {
    for _ in 0..200 {
        if check() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!("timed out waiting for {what}");
}

// ------------------------------------------------------------------------------------------ tests

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn joins_mid_match_via_snapshot_without_gaps_or_duplicates() {
    let (fake, url) = start_fake().await;
    for i in 0..5 {
        fake.publish("event", &format!("{{\"i\":{i}}}"));
    }
    let edge = start_edge(test_config(url)).await;

    let mut a = Viewer::open(&edge, "", None).await;
    assert_eq!(sync_n(&a.next().await.unwrap()), 5); // the snapshot covers ids 1..=5
    eventually("the upstream to connect", || fake.open.load(SeqCst) == 1).await; // frames aren't replayed
    fake.frame();
    let six = fake.publish("state", "{\"s\":6}");
    let got = a.until(six).await;
    assert_eq!(
        got.iter()
            .map(|e| (e.id, e.event.as_str()))
            .collect::<Vec<_>>(),
        [(None, "frame"), (Some(6), "state")]
    );

    // A second viewer joins while the engine publishes as fast as it can: whatever the snapshot covers,
    // it gets everything after it exactly once.
    let publisher = {
        let fake = fake.clone();
        tokio::spawn(async move {
            for i in 0..400 {
                fake.publish("insight", &format!("{{\"i\":{i}}}"));
                if i % 20 == 0 {
                    tokio::time::sleep(Duration::from_millis(1)).await;
                }
            }
        })
    };
    let mut b = Viewer::open(&edge, "", None).await;
    let n = sync_n(&b.next().await.unwrap());
    publisher.await.unwrap();
    let last = fake.last_id();
    assert_eq!(
        ids(&b.until(last).await),
        (n + 1..=last).collect::<Vec<_>>()
    );
    assert_eq!(ids(&a.until(last).await), (7..=last).collect::<Vec<_>>());
    // One upstream connection for the channel, however many viewers.
    assert_eq!(fake.connects.load(SeqCst), 1);
    assert_eq!(
        fake.last_event_ids.lock().unwrap()[0].as_deref(),
        Some("5"),
        "upstream resumes after the snapshot"
    );

    // Another channel (different flags) gets its own upstream.
    let mut c = Viewer::open(&edge, "?frames=true&events=false", None).await;
    sync_n(&c.next().await.unwrap());
    eventually("a second upstream", || fake.connects.load(SeqCst) == 2).await;
    assert_eq!(edge.hub.channel_count(), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resumes_from_the_ring_and_snapshots_when_too_old() {
    let (fake, url) = start_fake().await;
    let edge = start_edge(test_config(url)).await;
    let mut keep = Viewer::open(&edge, "?profile_id=p1", None).await; // keeps the channel open
    sync_n(&keep.next().await.unwrap());
    let mut a = Viewer::open(&edge, "?profile_id=p1", None).await;
    sync_n(&a.next().await.unwrap());
    for i in 0..10 {
        fake.publish("event", &format!("{{\"i\":{i}}}"));
    }
    a.until(10).await;
    drop(a);
    for i in 0..5 {
        fake.publish("event", &format!("{{\"i\":{i}}}"));
    }
    keep.until(15).await; // the edge has them
    let syncs = fake.syncs.load(SeqCst);

    // Last-Event-ID 10: exactly 11..=15 from the ring, no snapshot, no engine call.
    let mut resumed = Viewer::open(&edge, "?profile_id=p1", Some(10)).await;
    let got = resumed.until(15).await;
    assert_eq!(ids(&got), (11..=15).collect::<Vec<_>>());
    assert!(got.iter().all(|e| e.event != "sync"));
    assert_eq!(fake.syncs.load(SeqCst), syncs);

    // An id older than the ring (50 messages) gets a fresh snapshot instead.
    for i in 0..80 {
        fake.publish("event", &format!("{{\"i\":{i}}}"));
    }
    keep.until(95).await;
    let mut old = Viewer::open(&edge, "?profile_id=p1", Some(12)).await;
    assert_eq!(sync_n(&old.next().await.unwrap()), 95);
    let next = fake.publish("state", "{}");
    assert_eq!(ids(&old.until(next).await), [96]);
    // An id from the future (another engine's) is not trusted either.
    let mut future = Viewer::open(&edge, "?profile_id=p1", Some(10_000)).await;
    let n = sync_n(&future.next().await.unwrap());
    let next = fake.publish("state", "{}");
    assert_eq!(
        ids(&future.until(next).await),
        (n + 1..=next).collect::<Vec<_>>()
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_lagging_viewer_is_resynced_with_a_snapshot() {
    let (fake, url) = start_fake().await;
    let edge = start_edge(Config {
        broadcast_capacity: 16,
        ring_size: 2000,
        ..test_config(url)
    })
    .await;
    let mut slow = Viewer::open(&edge, "", None).await;
    sync_n(&slow.next().await.unwrap());
    let mut fast = Viewer::open(&edge, "", None).await;
    sync_n(&fast.next().await.unwrap());

    // The slow viewer stops reading; 400 x 64 KB is far more than socket buffers hold.
    let big = format!("\"{}\"", "x".repeat(64 * 1024));
    let reader = tokio::spawn(async move {
        let mut seen = Vec::new();
        while let Some(e) = fast.next().await {
            if let Some(id) = e.id {
                seen.push(id);
                if id == 400 {
                    break;
                }
            }
        }
        seen
    });
    for _ in 0..400 {
        fake.publish("event", &big);
        tokio::time::sleep(Duration::from_micros(200)).await;
    }
    assert_eq!(
        reader.await.unwrap(),
        (1..=400).collect::<Vec<_>>(),
        "a fast viewer is unaffected"
    );

    // Reading again: what was still in flight, then a fresh snapshot, then everything after it, in order.
    let mut in_flight = 0;
    let n = loop {
        let e = slow.next().await.expect("stream open");
        if e.event == "sync" && e.id.is_none() {
            break sync_n(&e);
        }
        in_flight += 1;
    };
    assert!(in_flight < 400, "the slow viewer skipped ahead");
    let next = fake.publish("state", "{}");
    assert_eq!(
        ids(&slow.until(next).await),
        (n + 1..=next).collect::<Vec<_>>()
    );
    assert!(edge.hub.stats.resyncs_lagged.load(SeqCst) >= 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn reconnects_upstream_with_last_event_id_and_resyncs_when_continuity_breaks() {
    let (fake, url) = start_fake().await;
    let edge = start_edge(test_config(url)).await;
    let mut v = Viewer::open(&edge, "", None).await;
    sync_n(&v.next().await.unwrap());
    for _ in 0..3 {
        fake.publish("event", "{}");
    }
    v.until(3).await;

    // The engine drops the connection; meanwhile more is published. The edge reconnects with
    // Last-Event-ID 3 and the engine replays the gap: the viewer sees no difference.
    fake.kick();
    for _ in 0..3 {
        fake.publish("event", "{}");
    }
    eventually("the upstream to reconnect", || {
        fake.connects.load(SeqCst) == 2
    })
    .await;
    assert_eq!(fake.last_event_ids.lock().unwrap()[1].as_deref(), Some("3"));
    let got = v.until(6).await;
    assert_eq!(ids(&got), [4, 5, 6]);
    assert!(got.iter().all(|e| e.event != "sync"));

    // This time the engine can't resume (the gap is gone from its log): it sends a fresh sync, and the
    // edge moves every viewer onto a new snapshot.
    fake.kick();
    fake.publish("event", "{}");
    fake.forget();
    eventually("the upstream to reconnect again", || {
        fake.connects.load(SeqCst) == 3
    })
    .await;
    let e = v.next().await.unwrap();
    let n = sync_n(&e);
    assert_eq!(n, 7);
    let next = fake.publish("state", "{}");
    assert_eq!(ids(&v.until(next).await), [8]);
    assert!(edge.hub.stats.resyncs_upstream.load(SeqCst) >= 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closes_the_upstream_after_the_last_viewer_leaves() {
    let (fake, url) = start_fake().await;
    let edge = start_edge(test_config(url)).await; // idle_close: 400 ms
    let mut v = Viewer::open(&edge, "", None).await;
    sync_n(&v.next().await.unwrap());
    eventually("the upstream to connect", || fake.open.load(SeqCst) == 1).await;
    drop(v);

    // Back within the idle window: the same upstream is reused, and the first leave's timer stands down.
    eventually("the edge to notice the viewer left", || {
        edge.hub.stats.viewers.load(SeqCst) == 0
    })
    .await;
    let mut v = Viewer::open(&edge, "", None).await;
    sync_n(&v.next().await.unwrap());
    tokio::time::sleep(Duration::from_millis(700)).await;
    assert_eq!(edge.hub.channel_count(), 1);
    assert_eq!(fake.connects.load(SeqCst), 1);
    assert_eq!(fake.open.load(SeqCst), 1);
    drop(v);

    eventually("the channel to close", || edge.hub.channel_count() == 0).await;
    eventually("the upstream connection to close", || {
        fake.open.load(SeqCst) == 0
    })
    .await;
    let stats: serde_json::Value = reqwest::get(format!("{}/stats", edge.base))
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(stats["channels"], 0);
    assert_eq!(stats["viewers"], 0);
    assert_eq!(stats["totals"]["viewers"], 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn errors_cors_health_and_graceful_shutdown() {
    let (fake, url) = start_fake().await;
    let mut edge = start_edge(test_config(url)).await;
    let client = reqwest::Client::new();

    let r = client
        .get(format!("{}/api/v1/matches/nope/stream", edge.base))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 404);
    assert!(r.text().await.unwrap().contains("unknown match"));
    assert_eq!(
        edge.hub.channel_count(),
        0,
        "no channel for a match the engine doesn't know"
    );
    let r = client
        .get(format!(
            "{}/api/v1/matches/m1/stream?frames=maybe",
            edge.base
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(r.status(), 422);

    let health: serde_json::Value = client
        .get(format!("{}/health", edge.base))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(health["status"], "ok");

    let allowed = client
        .get(format!("{}/health", edge.base))
        .header("origin", "http://allowed.test")
        .send()
        .await
        .unwrap();
    assert_eq!(
        allowed.headers()["access-control-allow-origin"],
        "http://allowed.test"
    );
    let other = client
        .get(format!("{}/health", edge.base))
        .header("origin", "http://evil.test")
        .send()
        .await
        .unwrap();
    assert!(other.headers().get("access-control-allow-origin").is_none());

    // Shutdown ends open streams, closes the upstream and stops the server.
    let mut v = Viewer::open(&edge, "", None).await;
    sync_n(&v.next().await.unwrap());
    edge.stop.take().unwrap().send(()).unwrap();
    assert!(v.next().await.is_none(), "the stream ends");
    tokio::time::timeout(Duration::from_secs(5), &mut edge.server)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    eventually("the upstream to close", || fake.open.load(SeqCst) == 0).await;
}
