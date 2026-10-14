//! Channels: one upstream SSE connection to the engine per `(match, profile, frames, events)`, fanned out
//! to every viewer of that channel through a `tokio::sync::broadcast` of pre-encoded messages.
//!
//! Ids are the engine's. The edge keeps the last `ring_size` messages with an id per channel, so a viewer
//! reconnecting with `Last-Event-ID` resumes locally; anyone else starts from a snapshot taken by the
//! engine's `/sync`, then skips every id the snapshot already covers.

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bytes::Bytes;
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::value::RawValue;
use tokio::sync::{broadcast, watch};
use tokio::task::JoinHandle;
use tracing::{debug, info, warn};

use crate::config::Config;
use crate::sse;
use crate::stats::Stats;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ChannelKey {
    pub match_id: String,
    pub profile_id: Option<String>,
    pub frames: bool,
    pub events: bool,
}

impl ChannelKey {
    fn query(&self) -> Vec<(&'static str, String)> {
        let mut q = Vec::with_capacity(3);
        if let Some(p) = &self.profile_id {
            q.push(("profile_id", p.clone()));
        }
        q.push(("frames", self.frames.to_string()));
        q.push(("events", self.events.to_string()));
        q
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// An SSE message from the engine, encoded for viewers.
    Message,
    /// Not sent to viewers: the upstream lost continuity, so every viewer takes a fresh snapshot.
    Resync,
}

#[derive(Debug)]
pub struct Msg {
    pub id: Option<u64>,
    pub kind: Kind,
    pub bytes: Bytes,
    /// When the edge read it from the engine (fan-out delay is measured from here).
    pub at: Instant,
}

/// A snapshot from the engine: the encoded `sync` message and the last id it covers.
#[derive(Debug, Clone)]
pub struct Snapshot {
    pub last_id: u64,
    pub bytes: Bytes,
    at: Instant,
    generation: u64,
}

#[derive(Debug)]
pub enum EngineError {
    /// The engine answered with an error status (404 for an unknown match or profile); passed through.
    Status(u16, String),
    /// The engine could not be reached or answered with something unreadable.
    Unavailable(String),
}

struct Ring {
    msgs: VecDeque<Arc<Msg>>,
    /// The ring holds every message of this channel with an id above `floor` (None: not known since a
    /// break in continuity, until the next message arrives).
    floor: Option<u64>,
    /// The highest id received from the engine.
    last_id: u64,
}

pub struct Channel {
    pub key: ChannelKey,
    ring: Mutex<Ring>,
    tx: broadcast::Sender<Arc<Msg>>,
    viewers: AtomicUsize,
    /// Bumped on every join, so a teardown timer started before it knows to stand down.
    epoch: AtomicU64,
    /// Bumped on every break in continuity: snapshots taken before it are not reused.
    generation: AtomicU64,
    snapshot: tokio::sync::Mutex<Option<Snapshot>>,
    upstream: Mutex<Option<JoinHandle<()>>>,
}

/// What a viewer gets on attaching: the buffered messages after its position, then the live broadcast.
pub struct Attached {
    pub backlog: Vec<Arc<Msg>>,
    pub rx: broadcast::Receiver<Arc<Msg>>,
}

impl Channel {
    fn new(key: ChannelKey, start_id: u64, capacity: usize) -> Self {
        let (tx, _) = broadcast::channel(capacity);
        Self {
            key,
            ring: Mutex::new(Ring {
                msgs: VecDeque::new(),
                floor: Some(start_id),
                last_id: start_id,
            }),
            tx,
            viewers: AtomicUsize::new(0),
            epoch: AtomicU64::new(0),
            generation: AtomicU64::new(0),
            snapshot: tokio::sync::Mutex::new(None),
            upstream: Mutex::new(None),
        }
    }

    pub fn viewers(&self) -> usize {
        self.viewers.load(Relaxed)
    }

    pub fn last_id(&self) -> u64 {
        self.ring.lock().unwrap().last_id
    }

    pub fn ring_len(&self) -> usize {
        self.ring.lock().unwrap().msgs.len()
    }

    /// Attach after `after`: subscribe to the broadcast and copy the ring's messages with a higher id,
    /// atomically with publishing, so nothing falls between the two. `None` when the ring can't prove it
    /// holds everything after `after` (it rolled past, or continuity broke), or, unless `ahead_ok`,
    /// when `after` is beyond anything this channel has seen.
    pub fn attach(&self, after: u64, ahead_ok: bool) -> Option<Attached> {
        let ring = self.ring.lock().unwrap();
        if after > ring.last_id && !ahead_ok {
            return None;
        }
        if after < ring.last_id && !ring.floor.is_some_and(|f| f <= after) {
            return None;
        }
        let backlog = ring
            .msgs
            .iter()
            .filter(|m| m.id.is_some_and(|id| id > after))
            .cloned()
            .collect();
        Some(Attached {
            backlog,
            rx: self.tx.subscribe(),
        })
    }

    /// Publish one upstream message to the ring and every viewer.
    fn publish(&self, event: sse::Event, ring_size: usize, stats: &Stats) {
        let at = Instant::now();
        stats.messages_in.fetch_add(1, Relaxed);
        let mut ring = self.ring.lock().unwrap();
        match event.id {
            // A message without an id is a tracking frame, or the `sync` the engine sends when it could
            // not resume from our Last-Event-ID: continuity is lost, so viewers resync from a snapshot.
            None if event.event == "sync" => {
                self.discontinuity(&mut ring, at);
                return;
            }
            None => {}
            Some(id) => {
                if id <= ring.last_id && ring.floor.is_some() {
                    // Ids went backwards: the engine restarted. Resync everyone, then carry on.
                    warn!(channel = ?self.key, id, last_id = ring.last_id, "upstream ids went backwards");
                    self.discontinuity(&mut ring, at);
                    ring.last_id = 0;
                }
                if ring.floor.is_none() {
                    ring.floor = Some(id.saturating_sub(1));
                }
                ring.last_id = id;
            }
        }
        let msg = Arc::new(Msg {
            id: event.id,
            kind: Kind::Message,
            bytes: event.encode(),
            at,
        });
        if msg.id.is_some() {
            if ring.msgs.len() >= ring_size {
                if let Some(old) = ring.msgs.pop_front() {
                    ring.floor = old.id.or(ring.floor);
                }
            }
            ring.msgs.push_back(msg.clone());
        }
        let _ = self.tx.send(msg); // no receivers is fine
    }

    fn discontinuity(&self, ring: &mut Ring, at: Instant) {
        ring.msgs.clear();
        ring.floor = None;
        self.generation.fetch_add(1, Relaxed);
        let marker = Arc::new(Msg {
            id: None,
            kind: Kind::Resync,
            bytes: Bytes::new(),
            at,
        });
        let _ = self.tx.send(marker);
    }

    fn stop(&self) {
        if let Some(task) = self.upstream.lock().unwrap().take() {
            task.abort();
        }
    }
}

#[derive(Deserialize)]
struct SyncBody<'a> {
    last_id: u64,
    #[serde(borrow)]
    sync: &'a RawValue,
}

pub struct Hub {
    pub cfg: Config,
    pub stats: Stats,
    client: reqwest::Client,
    channels: Mutex<HashMap<ChannelKey, Arc<Channel>>>,
    shutdown: watch::Sender<bool>,
}

/// Counts a viewer for as long as it lives; the last one out starts the channel's teardown timer.
pub struct ViewerGuard {
    hub: Arc<Hub>,
    channel: Arc<Channel>,
}

impl Drop for ViewerGuard {
    fn drop(&mut self) {
        self.hub.stats.viewers.fetch_sub(1, Relaxed);
        if self.channel.viewers.fetch_sub(1, Relaxed) == 1 {
            let epoch = self.channel.epoch.load(Relaxed);
            let hub = self.hub.clone();
            let channel = self.channel.clone();
            if let Ok(rt) = tokio::runtime::Handle::try_current() {
                rt.spawn(async move {
                    tokio::time::sleep(hub.cfg.idle_close).await;
                    hub.close_if_idle(&channel, epoch);
                });
            }
        }
    }
}

impl Hub {
    pub fn new(cfg: Config) -> Arc<Self> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .tcp_nodelay(true)
            .pool_idle_timeout(Duration::from_secs(60))
            .build()
            .expect("HTTP client");
        let (shutdown, _) = watch::channel(false);
        Arc::new(Self {
            cfg,
            stats: Stats::default(),
            client,
            channels: Mutex::new(HashMap::new()),
            shutdown,
        })
    }

    pub fn shutdown_signal(&self) -> watch::Receiver<bool> {
        self.shutdown.subscribe()
    }

    /// Ends every viewer stream and every upstream connection (graceful shutdown).
    pub fn shutdown(&self) {
        let _ = self.shutdown.send(true);
        for channel in self.channels.lock().unwrap().drain().map(|(_, c)| c) {
            channel.stop();
        }
    }

    pub fn channel_count(&self) -> usize {
        self.channels.lock().unwrap().len()
    }

    pub fn channels(&self) -> Vec<Arc<Channel>> {
        self.channels.lock().unwrap().values().cloned().collect()
    }

    fn url(&self, key: &ChannelKey, endpoint: &str) -> Result<reqwest::Url, EngineError> {
        let mut url = reqwest::Url::parse(&self.cfg.engine_url)
            .map_err(|e| EngineError::Unavailable(format!("bad ENGINE_URL: {e}")))?;
        url.path_segments_mut()
            .map_err(|_| EngineError::Unavailable("bad ENGINE_URL".into()))?
            .pop_if_empty()
            .extend(["api", "v1", "matches", &key.match_id, endpoint]);
        url.query_pairs_mut().extend_pairs(key.query());
        Ok(url)
    }

    /// A fresh snapshot from the engine's `/sync`.
    pub async fn fetch_snapshot(
        &self,
        key: &ChannelKey,
        generation: u64,
    ) -> Result<Snapshot, EngineError> {
        self.stats.snapshot_fetches.fetch_add(1, Relaxed);
        let url = self.url(key, "sync")?;
        let resp = self
            .client
            .get(url)
            .timeout(Duration::from_secs(10))
            .send()
            .await
            .map_err(|e| EngineError::Unavailable(e.to_string()))?;
        let status = resp.status();
        let body = resp
            .bytes()
            .await
            .map_err(|e| EngineError::Unavailable(e.to_string()))?;
        if !status.is_success() {
            return Err(EngineError::Status(
                status.as_u16(),
                String::from_utf8_lossy(&body).into_owned(),
            ));
        }
        let parsed: SyncBody = serde_json::from_slice(&body)
            .map_err(|e| EngineError::Unavailable(format!("unreadable /sync answer: {e}")))?;
        Ok(Snapshot {
            last_id: parsed.last_id,
            bytes: sse::encode(None, "sync", parsed.sync.get()),
            at: Instant::now(),
            generation,
        })
    }

    /// A snapshot for `channel`: a recent one is shared by viewers joining together (one engine call per
    /// channel per `snapshot_ttl`, whatever the crowd), unless `fresh` is asked for.
    pub async fn snapshot(&self, channel: &Channel, fresh: bool) -> Result<Snapshot, EngineError> {
        let mut cached = channel.snapshot.lock().await;
        let generation = channel.generation.load(Relaxed);
        if let Some(s) = cached.as_ref() {
            if !fresh && s.generation == generation && s.at.elapsed() < self.cfg.snapshot_ttl {
                return Ok(s.clone());
            }
        }
        let snap = self.fetch_snapshot(&channel.key, generation).await?;
        *cached = Some(snap.clone());
        Ok(snap)
    }

    /// The channel for `key` with one more viewer, if it exists.
    pub fn join_existing(
        self: &Arc<Self>,
        key: &ChannelKey,
    ) -> Option<(Arc<Channel>, ViewerGuard)> {
        let channels = self.channels.lock().unwrap();
        let channel = channels.get(key)?.clone();
        Some((channel.clone(), self.guard(channel)))
    }

    /// The channel for `key` with one more viewer, created from `snap` (its upstream resumes right after
    /// the snapshot) unless another viewer created it first.
    pub fn join_or_create(
        self: &Arc<Self>,
        key: &ChannelKey,
        snap: &Snapshot,
    ) -> (Arc<Channel>, ViewerGuard) {
        let mut channels = self.channels.lock().unwrap();
        if let Some(channel) = channels.get(key) {
            let channel = channel.clone();
            return (channel.clone(), self.guard(channel));
        }
        let channel = Arc::new(Channel::new(
            key.clone(),
            snap.last_id,
            self.cfg.broadcast_capacity,
        ));
        *channel.snapshot.try_lock().expect("new channel") = Some(snap.clone());
        let task = tokio::spawn(upstream(self.clone(), channel.clone()));
        *channel.upstream.lock().unwrap() = Some(task);
        channels.insert(key.clone(), channel.clone());
        info!(channel = ?key, from_id = snap.last_id, "channel opened");
        (channel.clone(), self.guard(channel))
    }

    fn guard(self: &Arc<Self>, channel: Arc<Channel>) -> ViewerGuard {
        // Called with the channel map locked, so a teardown can't slip in between.
        channel.viewers.fetch_add(1, Relaxed);
        channel.epoch.fetch_add(1, Relaxed);
        self.stats.viewers.fetch_add(1, Relaxed);
        self.stats.viewers_total.fetch_add(1, Relaxed);
        ViewerGuard {
            hub: self.clone(),
            channel,
        }
    }

    fn close_if_idle(&self, channel: &Arc<Channel>, epoch: u64) {
        let mut channels = self.channels.lock().unwrap();
        let current = channels
            .get(&channel.key)
            .is_some_and(|c| Arc::ptr_eq(c, channel));
        if current && channel.viewers() == 0 && channel.epoch.load(Relaxed) == epoch {
            channels.remove(&channel.key);
            channel.stop();
            info!(channel = ?channel.key, "channel closed (idle)");
        }
    }
}

/// The channel's one connection to the engine: read, parse, publish; reconnect with `Last-Event-ID`
/// (the engine replays what we missed) until the channel is closed.
async fn upstream(hub: Arc<Hub>, channel: Arc<Channel>) {
    let mut backoff = Duration::from_millis(250);
    loop {
        // The first connection resumes right after the snapshot the channel was created from.
        let last_id = channel.last_id();
        match read_upstream(&hub, &channel, last_id).await {
            Ok(()) => debug!(channel = ?channel.key, "upstream ended; reconnecting"),
            Err(e) => warn!(channel = ?channel.key, error = %e, "upstream failed; reconnecting"),
        }
        if *hub.shutdown.borrow() {
            return;
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(Duration::from_secs(5));
        if channel.last_id() != last_id {
            backoff = Duration::from_millis(250); // that connection made progress
        }
    }
}

async fn read_upstream(hub: &Hub, channel: &Channel, resume: u64) -> Result<(), String> {
    let url = hub
        .url(&channel.key, "stream")
        .map_err(|e| format!("{e:?}"))?;
    let resp = hub
        .client
        .get(url)
        .header("accept", "text/event-stream")
        .header("last-event-id", resume.to_string())
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("engine answered {}", resp.status()));
    }
    hub.stats.upstream_connects.fetch_add(1, Relaxed);
    info!(channel = ?channel.key, resume, "upstream connected");
    let mut body = resp.bytes_stream();
    let mut parser = sse::Parser::new();
    loop {
        let chunk = match tokio::time::timeout(hub.cfg.upstream_idle_timeout, body.next()).await {
            Err(_) => return Err("upstream silent; reconnecting".into()),
            Ok(None) => return Ok(()),
            Ok(Some(Err(e))) => return Err(e.to_string()),
            Ok(Some(Ok(chunk))) => chunk,
        };
        for event in parser.feed(&chunk) {
            channel.publish(event, hub.cfg.ring_size, &hub.stats);
        }
    }
}
